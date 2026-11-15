import { useEffect, useMemo, useState } from 'react'
import { Link, useBlocker } from 'react-router'
import { ApiError, errorProblems } from '../../api/client'
import { useApproveTemplate, useSaveTemplate, useTemplate } from '../../api/hooks'
import type { Field, FieldType, Rect, Role, Template } from '../../api/types'
import {
  approvalProblems,
  clampRect,
  colorFor,
  FIELD_TYPES,
  fillerLabel,
  newFieldId,
  SENDER,
  SIGNER_ONLY,
  TYPE_LABELS,
} from '../../lib/fields'
import { formatDate } from '../../lib/format'
import { Alert, Button, StatusBadge } from '../ui'
import { PageCanvas } from './PageCanvas'
import { FieldList, FieldPanel, FillerSelect, RolesPanel } from './Panels'

type Draft = { name: string; roles: Role[]; fields: Field[] }

const fromTemplate = (t: Template): Draft => ({ name: t.name, roles: t.roles, fields: t.fields })

export function ReviewEditor({ template }: { template: Template }) {
  const save = useSaveTemplate(template.id)
  const approve = useApproveTemplate(template.id)
  const refetch = useTemplate(template.id).refetch
  const [draft, setDraft] = useState<Draft>(() => fromTemplate(template))
  const [history, setHistory] = useState<Draft[]>([])
  const [dirty, setDirty] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [adding, setAdding] = useState(false)
  const [addType, setAddType] = useState<FieldType>('text')
  const [addFor, setAddFor] = useState<string>(SENDER)
  const [justApproved, setJustApproved] = useState(false)

  const change = (fn: (d: Draft) => Draft) => {
    setHistory((h) => [...h.slice(-49), draft])
    setDraft(fn(draft))
    setDirty(true)
    setJustApproved(false)
  }

  const undo = () => {
    if (!history.length) return
    setDraft(history[history.length - 1])
    setHistory(history.slice(0, -1))
    setDirty(true)
  }

  const reset = (t: Template) => {
    setDraft(fromTemplate(t))
    setHistory([])
    setDirty(false)
  }

  const selected = draft.fields.find((f) => f.id === selectedId) ?? null
  const problems = useMemo(() => approvalProblems(draft.roles, draft.fields), [draft])
  const byPage = useMemo(() => {
    const m = new Map<number, Field[]>()
    for (const f of draft.fields) m.set(f.page, [...(m.get(f.page) ?? []), f])
    return m
  }, [draft.fields])

  const patchField = (id: string, patch: Partial<Field>) =>
    change((d) => ({ ...d, fields: d.fields.map((f) => (f.id === id ? { ...f, ...patch } : f)) }))
  const deleteField = (id: string) => {
    change((d) => ({ ...d, fields: d.fields.filter((f) => f.id !== id) }))
    setSelectedId(null)
  }
  const addField = (page: number, rect: Rect) => {
    const filledBy = SIGNER_ONLY.has(addType) && addFor === SENDER ? (draft.roles[0]?.key ?? SENDER) : addFor
    const who = filledBy === SENDER ? '' : `${fillerLabel(filledBy, draft.roles)} `
    const f: Field = {
      id: newFieldId(),
      key: '',
      label: `${who}${TYPE_LABELS[addType].toLowerCase()}`.replace(/^./, (c) => c.toUpperCase()),
      type: addType,
      filled_by: filledBy,
      page,
      rect,
      required: addType !== 'checkbox',
      source: 'manual',
    }
    change((d) => ({ ...d, fields: [...d.fields, f] }))
    setSelectedId(f.id)
    setAdding(false)
  }

  const select = (id: string | null, scroll = false) => {
    setSelectedId(id)
    if (id && scroll) {
      document.querySelector(`[data-field="${id}"]`)?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }

  // Keyboard: Delete removes, arrows nudge (Shift: 10 pt), Esc deselects, Cmd/Ctrl+Z undoes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement
      if (el.closest('input, select, textarea, [contenteditable]')) return
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'z') {
        e.preventDefault()
        undo()
        return
      }
      if (e.key === 'Escape') {
        setAdding(false)
        setSelectedId(null)
        return
      }
      if (!selected) return
      if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault()
        deleteField(selected.id)
        return
      }
      const step = e.shiftKey ? 10 : 1
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      }
      const d = delta[e.key]
      if (!d) return
      e.preventDefault()
      const r = selected.rect
      const page = template.pages[selected.page - 1]
      patchField(selected.id, {
        rect: clampRect({ x0: r.x0 + d[0], y0: r.y0 + d[1], x1: r.x1 + d[0], y1: r.y1 + d[1] }, page),
      })
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  // Don't lose unsaved edits to a closed tab or a click elsewhere in the app.
  useEffect(() => {
    if (!dirty) return
    const onUnload = (e: BeforeUnloadEvent) => e.preventDefault()
    window.addEventListener('beforeunload', onUnload)
    return () => window.removeEventListener('beforeunload', onUnload)
  }, [dirty])
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname)
  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm('You have unsaved changes. Leave without saving?')) blocker.proceed()
    else blocker.reset()
  }, [blocker])

  const body = (d: Draft, revision: number) => ({ revision, roles: d.roles, fields: d.fields, name: d.name })
  const onSave = () => save.mutate(body(draft, template.revision), { onSuccess: reset })
  const onApprove = () => {
    approve.reset()
    const doApprove = (revision: number) =>
      approve.mutate(
        { revision },
        {
          onSuccess: (t) => {
            reset(t)
            setJustApproved(true)
            window.scrollTo({ top: 0, behavior: 'smooth' })
          },
        },
      )
    if (!dirty) {
      doApprove(template.revision)
      return
    }
    save.mutate(body(draft, template.revision), {
      onSuccess: (t) => {
        reset(t)
        doApprove(t.revision)
      },
    })
  }
  const reload = async () => {
    const r = await refetch()
    if (r.data) reset(r.data)
    save.reset()
    approve.reset()
  }

  const error = save.error ?? approve.error
  const conflict = error instanceof ApiError && error.status === 409
  const isActive = template.status === 'active'
  const sharedWith = selected
    ? draft.fields.filter((f) => f.id !== selected.id && f.key && f.key === selected.key && f.filled_by === selected.filled_by).length
    : 0

  return (
    <div>
      {/* toolbar */}
      <div className="sticky top-14 z-20 border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
          <Link to="/templates" className="text-sm text-slate-500 hover:text-slate-800">
            ← Agreements
          </Link>
          <input
            aria-label="Agreement name"
            className="min-w-0 flex-1 rounded border border-transparent px-1.5 py-1 text-lg font-semibold hover:border-slate-200 focus:border-brand focus:outline-none sm:max-w-md"
            value={draft.name}
            maxLength={120}
            onChange={(e) => change((d) => ({ ...d, name: e.target.value }))}
          />
          <StatusBadge status={template.status} />
          <span className="text-xs text-slate-400">v{template.version}</span>
          <div className="ml-auto flex items-center gap-2">
            {dirty && <span className="text-xs text-amber-700">Unsaved changes</span>}
            <Button variant="ghost" onClick={undo} disabled={!history.length} title="Undo (Cmd/Ctrl+Z)">
              Undo
            </Button>
            <Button variant="secondary" onClick={onSave} disabled={!dirty} busy={save.isPending && !approve.isPending}>
              {isActive ? 'Save as new version' : 'Save'}
            </Button>
            {(!isActive || dirty) && (
              <Button onClick={onApprove} disabled={problems.length > 0} busy={approve.isPending} title={problems.join('\n')}>
                Approve
              </Button>
            )}
          </div>
        </div>
      </div>

      <div className="mx-auto grid max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[minmax(0,1fr)_340px]">
        <div className="min-w-0 space-y-3">
          {justApproved && (
            <Alert tone="success" title="Approved">
              This agreement is ready. Every new file will use these fields.{' '}
              <Link to={`/files/new?template=${template.id}`} className="font-medium underline">
                Open a file with it
              </Link>
            </Alert>
          )}
          {isActive && !justApproved && (
            <Alert tone="success" title={`Approved ${formatDate(template.approved_at)}`}>
              Changes you save become version {template.version + 1}, which needs approval again.
              Files already sent keep the version they were sent with.{' '}
              <Link to={`/files/new?template=${template.id}`} className="font-medium underline">
                Open a file with it
              </Link>
            </Alert>
          )}
          {error && (
            <Alert title={conflict ? 'Someone else changed this agreement' : 'Couldn’t save'}>
              <p>{error.message}</p>
              {errorProblems(error).length > 0 && (
                <ul className="mt-1 list-disc pl-5">
                  {errorProblems(error).map((p) => (
                    <li key={p}>{p}</li>
                  ))}
                </ul>
              )}
              {conflict && (
                <Button variant="secondary" className="mt-2" onClick={reload}>
                  Reload the latest (discards your changes)
                </Button>
              )}
            </Alert>
          )}
          {!isActive && (
            <Alert tone="info" title="Check each highlighted field, then approve">
              <p>The colour shows who fills each field in:</p>
              <ul className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
                <li>
                  <Legend color={colorFor(SENDER, draft.roles)} /> You, on each new file (the
                  client’s name, fees, dates)
                </li>
                {[...draft.roles]
                  .sort((a, b) => a.order - b.order)
                  .map((r) => (
                    <li key={r.key}>
                      <Legend color={colorFor(r.key, draft.roles)} /> {r.label}, when they sign
                    </li>
                  ))}
              </ul>
              <p className="mt-1">
                Click a field to change it, drag to move it, or drag a corner to resize it.
              </p>
            </Alert>
          )}
          {template.fallback_reason && template.detector === 'heuristic' && (
            <Alert tone="warning" title="AI detection wasn’t available for this document">
              The fields were proposed by keyword rules instead ({template.fallback_reason}). They
              catch most blanks, but check each page carefully.
            </Alert>
          )}
          {template.warnings.length > 0 && (
            <Alert tone="warning" title="Notes from the analysis">
              <ul className="list-disc pl-5">
                {template.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            </Alert>
          )}

          {template.pages.map((size, i) => (
            <section key={i} id={`page-${i + 1}`} aria-label={`Page ${i + 1}`}>
              <p className="mb-1 text-xs text-slate-400">
                Page {i + 1} of {template.page_count}
              </p>
              <PageCanvas
                templateId={template.id}
                pdfRev={template.pdf_rev}
                page={i + 1}
                size={size}
                fields={byPage.get(i + 1) ?? []}
                roles={draft.roles}
                selectedId={selectedId}
                addType={adding ? addType : null}
                onSelect={(id) => select(id)}
                onMove={(id, rect) => patchField(id, { rect })}
                onAdd={addField}
              />
            </section>
          ))}
        </div>

        <aside className="lg:sticky lg:top-[7.75rem] lg:max-h-[calc(100vh-8.5rem)] lg:overflow-y-auto">
          <div className="space-y-4">
            <Card>
              {problems.length > 0 ? (
                <div>
                  <h3 className="text-sm font-semibold">Before you can approve</h3>
                  <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-red-700">
                    {problems.map((p) => (
                      <li key={p}>{p}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <p className="text-sm text-slate-600">
                  {draft.fields.length} fields. Every signer has somewhere to sign.
                </p>
              )}
            </Card>

            <Card>
              <h3 className="text-sm font-semibold">Add a field</h3>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <div>
                  <label htmlFor="add-type" className="text-xs font-medium text-slate-600">
                    Type
                  </label>
                  <select
                    id="add-type"
                    className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm"
                    value={addType}
                    onChange={(e) => {
                      const t = e.target.value as FieldType
                      setAddType(t)
                      if (SIGNER_ONLY.has(t) && addFor === SENDER && draft.roles[0]) setAddFor(draft.roles[0].key)
                    }}
                  >
                    {FIELD_TYPES.map((t) => (
                      <option key={t} value={t}>
                        {TYPE_LABELS[t]}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor="add-for" className="text-xs font-medium text-slate-600">
                    Filled in by
                  </label>
                  <FillerSelect id="add-for" value={addFor} roles={draft.roles} type={addType} onChange={setAddFor} />
                </div>
              </div>
              <Button
                variant={adding ? 'secondary' : 'primary'}
                className="mt-3 w-full"
                onClick={() => {
                  setAdding((a) => !a)
                  setSelectedId(null)
                }}
              >
                {adding ? 'Cancel' : 'Place it on the page'}
              </Button>
              {adding && <p className="mt-2 text-xs text-slate-500">Click where it goes, or drag to draw its box.</p>}
            </Card>

            <Card>
              {selected ? (
                <FieldPanel
                  key={selected.id}
                  field={selected}
                  roles={draft.roles}
                  sharedWith={sharedWith}
                  onChange={(patch) => patchField(selected.id, patch)}
                  onDelete={() => deleteField(selected.id)}
                />
              ) : (
                <p className="text-sm text-slate-500">Click a field on the page to edit it.</p>
              )}
            </Card>

            <Card>
              <RolesPanel
                roles={draft.roles}
                fields={draft.fields}
                onChange={(roles, fields) => change((d) => ({ ...d, roles, fields: fields ?? d.fields }))}
              />
            </Card>

            <Card>
              <details>
                <summary className="cursor-pointer text-sm font-semibold">All fields ({draft.fields.length})</summary>
                <div className="mt-2">
                  <FieldList fields={draft.fields} roles={draft.roles} selectedId={selectedId} onSelect={(id) => select(id, true)} />
                </div>
              </details>
            </Card>
          </div>
        </aside>
      </div>
    </div>
  )
}

function Card({ children }: { children: React.ReactNode }) {
  return <div className="rounded-xl border border-slate-200 bg-white p-4">{children}</div>
}

function Legend({ color }: { color: string }) {
  return <span className="inline-block h-2.5 w-2.5 rounded-sm align-baseline" style={{ background: color }} aria-hidden />
}
