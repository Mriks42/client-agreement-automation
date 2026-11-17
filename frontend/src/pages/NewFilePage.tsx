import { useEffect, useState, type FormEvent, type KeyboardEvent } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router'
import { errorProblems } from '../api/client'
import {
  useClientFill,
  useClients,
  useCreateFile,
  useEditFile,
  useFile,
  useMe,
  useTemplate,
  useTemplates,
} from '../api/hooks'
import type { ClientFill, FileInput, Role, SavedClient, SenderInput } from '../api/types'
import { Alert, Button, Card, FullPageSpinner, TextArea, TextInput } from '../components/ui'
import { ordinal } from '../lib/fields'

type Values = Record<string, string | boolean>
type People = Record<string, { name: string; email: string }>

/** /files/new?template=… : open a file from an approved agreement. */
export function NewFilePage() {
  const [params, setParams] = useSearchParams()
  const templateId = params.get('template') ?? ''
  const templates = useTemplates()
  const active = templates.data?.filter((t) => t.status === 'active') ?? []

  if (!templateId) {
    if (templates.isPending) return <FullPageSpinner />
    if (active.length === 1) return <Navigate to={`/files/new?template=${active[0].id}`} replace />
    return (
      <Shell title="New file">
        {active.length === 0 ? (
          <Alert tone="info" title="No approved agreement yet">
            Upload your agreement and approve its fields first.{' '}
            <Link to="/templates" className="font-medium underline">
              Go to agreements
            </Link>
          </Alert>
        ) : (
          <Card>
            <h2 className="text-sm font-semibold">Which agreement does this client sign?</h2>
            <ul className="mt-2 divide-y divide-slate-100">
              {active.map((t) => (
                <li key={t.id}>
                  <button
                    type="button"
                    onClick={() => setParams({ template: t.id })}
                    className="flex w-full items-center justify-between px-1 py-2.5 text-left text-sm hover:bg-slate-50"
                  >
                    <span className="font-medium">{t.name}</span>
                    <span className="text-slate-400">v{t.version} →</span>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
        )}
      </Shell>
    )
  }
  return <CreateForm templateId={templateId} />
}

function CreateForm({ templateId }: { templateId: string }) {
  const template = useTemplate(templateId)
  const me = useMe()
  const create = useCreateFile()
  const navigate = useNavigate()
  if (template.isPending || me.isPending) return <FullPageSpinner />
  if (template.isError) return <Shell title="New file"><Alert>{template.error.message}</Alert></Shell>
  const t = template.data
  if (t.status !== 'active') {
    return (
      <Shell title="New file">
        <Alert tone="warning" title="This agreement isn’t approved">
          Approve its fields before opening a file with it.{' '}
          <Link to={`/templates/${t.id}`} className="font-medium underline">
            Review it
          </Link>
        </Alert>
      </Shell>
    )
  }
  const people: People = {}
  for (const r of t.roles) {
    people[r.key] = r.kind === 'firm' && me.data ? { name: me.data.user.name, email: me.data.user.email } : { name: '', email: '' }
  }
  return (
    <Shell title="New file" subtitle={`${t.name} · version ${t.version}`}>
      <FileForm
        inputs={t.sender_inputs}
        roles={t.roles}
        initial={{ title: '', values: {}, people }}
        pickClientFor={t.id}
        busy={create.isPending}
        error={create.error}
        onSubmit={(body) =>
          create.mutate({ ...body, template_id: t.id }, { onSuccess: (f) => navigate(`/files/${f.id}`) })
        }
      />
    </Shell>
  )
}

/** /files/:id/edit : change a draft before it is sent. */
export function EditFilePage() {
  const { id = '' } = useParams()
  const file = useFile(id)
  const edit = useEditFile(id)
  const navigate = useNavigate()
  if (file.isPending) return <FullPageSpinner />
  if (file.isError) return <Shell title="Edit file"><Alert>{file.error.message}</Alert></Shell>
  const f = file.data
  if (f.status !== 'draft') {
    return (
      <Shell title={f.title}>
        <Alert tone="info">This file has been sent, so its details can’t change.</Alert>
      </Shell>
    )
  }
  const people: People = {}
  for (const r of f.recipients) people[r.role] = { name: r.name, email: r.email }
  return (
    <Shell title={`Edit ${f.title}`} subtitle={`${f.template.name} · version ${f.template.version}`} back={`/files/${f.id}`}>
      <FileForm
        inputs={f.sender_inputs}
        roles={f.template.roles}
        initial={{ title: f.title, values: f.values, people }}
        busy={edit.isPending}
        error={edit.error}
        onSubmit={(body) =>
          edit.mutate({ ...body, revision: f.revision }, { onSuccess: () => navigate(`/files/${f.id}`) })
        }
      />
    </Shell>
  )
}

function Shell({
  title,
  subtitle,
  back = '/files',
  children,
}: {
  title: string
  subtitle?: string
  back?: string
  children: React.ReactNode
}) {
  return (
    <main className="mx-auto max-w-2xl px-4 py-8">
      <Link to={back} className="text-sm text-slate-500 hover:text-slate-800">
        ← Back
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{title}</h1>
      {subtitle && <p className="mt-1 text-sm text-slate-600">{subtitle}</p>}
      <div className="mt-6 space-y-4">{children}</div>
    </main>
  )
}

function FileForm({
  inputs,
  roles,
  initial,
  pickClientFor,
  busy,
  error,
  onSubmit,
}: {
  inputs: SenderInput[]
  roles: Role[]
  initial: { title: string; values: Values; people: People }
  /** New file only: the agreement a saved client's details are matched to. */
  pickClientFor?: string
  busy: boolean
  error: Error | null
  onSubmit: (body: FileInput) => void
}) {
  const [title, setTitle] = useState(initial.title)
  const [values, setValues] = useState<Values>(initial.values)
  const [people, setPeople] = useState<People>(initial.people)
  const ordered = [...roles].sort((a, b) => a.order - b.order)
  const problems = errorProblems(error)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    onSubmit({
      title: title.trim() || undefined,
      values,
      recipients: ordered.map((r) => ({ role: r.key, name: people[r.key]?.name ?? '', email: people[r.key]?.email ?? '' })),
    })
  }
  const setPerson = (role: string, patch: Partial<{ name: string; email: string }>) =>
    setPeople((p) => ({ ...p, [role]: { ...(p[role] ?? { name: '', email: '' }), ...patch } }))
  // Picking a saved client fills the client signer and the client's identity details only;
  // the server never returns dates, fees or the firm's own fields.
  const applyClient = (fill: ClientFill) => {
    if (fill.recipient) setPerson(fill.recipient.role, { name: fill.recipient.name, email: fill.recipient.email })
    setValues((v) => ({ ...v, ...fill.values }))
  }

  return (
    <form onSubmit={submit} className="space-y-4">
      {pickClientFor && <ClientPicker templateId={pickClientFor} onPick={applyClient} />}
      <Card>
        <h2 className="text-sm font-semibold">Who signs, in order</h2>
        <p className="mt-0.5 text-xs text-slate-500">
          Each person gets an email with their own signing link when it’s their turn.
        </p>
        <div className="mt-3 space-y-4">
          {ordered.map((r) => (
            <fieldset key={r.key} className="grid gap-3 sm:grid-cols-2">
              <legend className="mb-1 text-sm font-medium text-slate-800">
                {ordinal(r.order)}: {r.label}
              </legend>
              <TextInput
                id={`name-${r.key}`}
                label="Name"
                required
                autoComplete="off"
                value={people[r.key]?.name ?? ''}
                onChange={(e) => setPerson(r.key, { name: e.target.value })}
              />
              <TextInput
                id={`email-${r.key}`}
                label="Email"
                type="email"
                required
                autoComplete="off"
                value={people[r.key]?.email ?? ''}
                onChange={(e) => setPerson(r.key, { email: e.target.value })}
              />
            </fieldset>
          ))}
        </div>
      </Card>

      <Card>
        <h2 className="text-sm font-semibold">Details for this file</h2>
        {inputs.length === 0 ? (
          <p className="mt-1 text-sm text-slate-500">This agreement has no blanks for you to fill in.</p>
        ) : (
          <p className="mt-0.5 text-xs text-slate-500">
            These fill the blanks in the agreement. You’ll see exactly how it looks before anything is sent.
          </p>
        )}
        <div className="mt-3 space-y-3">
          {inputs.map((i) => (
            <ValueInput key={i.key} input={i} value={values[i.key]} onChange={(v) => setValues((s) => ({ ...s, [i.key]: v }))} />
          ))}
        </div>
      </Card>

      <Card>
        <TextInput
          id="file-title"
          label="File name (optional)"
          hint="How this file appears in your list. If you leave it empty, the client’s name is used."
          maxLength={120}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
        />
      </Card>

      {error && (
        <Alert title={problems.length ? 'Fix these first' : 'Couldn’t save'}>
          {problems.length ? (
            <ul className="list-disc pl-5">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          ) : (
            error.message
          )}
        </Alert>
      )}
      <div className="flex justify-end">
        <Button type="submit" busy={busy}>
          Preview the agreement
        </Button>
      </div>
    </form>
  )
}

/** "Client" search at the top of New file. Hidden until the company has sent a file, so the form
 * is exactly as before; ignoring it changes nothing. */
function ClientPicker({ templateId, onPick }: { templateId: string; onPick: (fill: ClientFill) => void }) {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const [picked, setPicked] = useState<ClientFill | null>(null)
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 200)
    return () => clearTimeout(t)
  }, [q])
  const recent = useClients('')
  const results = useClients(term)
  const fill = useClientFill()
  if (!recent.data?.length) return null

  const list = results.data ?? []
  const choose = (c: SavedClient) => {
    setOpen(false)
    fill.mutate(
      { clientId: c.id, templateId },
      {
        onSuccess: (f) => {
          onPick(f)
          setPicked(f)
          setQ('')
        },
      },
    )
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape') setOpen(false)
    if (!list.length) return
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      setOpen(true)
      setActive((i) => (i + 1) % list.length)
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => (i - 1 + list.length) % list.length)
    } else if (e.key === 'Enter' && open) {
      e.preventDefault()
      choose(list[Math.min(active, list.length - 1)])
    }
  }
  const details = picked ? Object.keys(picked.values).length : 0

  return (
    <Card>
      <label htmlFor="client-search" className="block text-sm font-semibold">
        Client
      </label>
      <p className="mt-0.5 text-xs text-slate-500">
        Optional. Pick a client you’ve sent to before to fill in their details. Dates, fees and other details for this
        job are left for you.{' '}
        <Link to="/clients" className="underline hover:text-slate-800">
          Edit saved clients
        </Link>
      </p>
      <div className="relative mt-2">
        <input
          id="client-search"
          role="combobox"
          aria-expanded={open && list.length > 0}
          aria-controls="client-results"
          aria-autocomplete="list"
          autoComplete="off"
          placeholder="Search by company, name or email"
          value={q}
          onChange={(e) => {
            setQ(e.target.value)
            setOpen(true)
            setActive(0)
          }}
          onFocus={() => setOpen(true)}
          onBlur={() => setOpen(false)}
          onKeyDown={onKey}
          className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
        />
        {open && (
          <ul
            id="client-results"
            role="listbox"
            className="absolute z-10 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-slate-200 bg-white py-1 shadow-lg"
          >
            {list.length === 0 ? (
              <li className="px-3 py-2 text-sm text-slate-500">{results.isFetching ? 'Searching…' : 'No saved client matches.'}</li>
            ) : (
              list.map((c, i) => (
                <li
                  key={c.id}
                  role="option"
                  aria-selected={i === active}
                  // mousedown, so the click lands before the input's blur closes the list
                  onMouseDown={(e) => {
                    e.preventDefault()
                    choose(c)
                  }}
                  onMouseEnter={() => setActive(i)}
                  className={`cursor-pointer px-3 py-2 text-sm ${i === active ? 'bg-slate-100' : ''}`}
                >
                  <span className="block font-medium text-slate-900">{c.company || c.contact_name}</span>
                  <span className="block text-xs text-slate-500">
                    {c.company && c.contact_name ? `${c.contact_name} · ` : ''}
                    {c.email}
                  </span>
                </li>
              ))
            )}
          </ul>
        )}
      </div>
      {fill.isPending && <p className="mt-2 text-xs text-slate-500">Filling in…</p>}
      {fill.isError && <Alert className="mt-2">{fill.error.message}</Alert>}
      {picked && !fill.isPending && (
        <p className="mt-2 text-sm text-emerald-800" role="status">
          Filled in from {picked.client.company || picked.client.contact_name}:{' '}
          {picked.recipient ? 'the client signer’s name and email' : 'nothing for the signers'}
          {details ? `, and ${details} detail${details === 1 ? '' : 's'} below` : ''}. Check them before previewing.
        </p>
      )}
    </Card>
  )
}

function ValueInput({
  input,
  value,
  onChange,
}: {
  input: SenderInput
  value: string | boolean | undefined
  onChange: (v: string | boolean) => void
}) {
  const id = `v-${input.key}`
  const hint = input.count > 1 ? `Fills ${input.count} places in the agreement.` : undefined
  const label = `${input.label}${input.required ? '' : ' (optional)'}`
  if (input.type === 'checkbox') {
    return (
      <label htmlFor={id} className="flex items-start gap-2 text-sm">
        <input id={id} type="checkbox" className="mt-0.5 h-4 w-4" checked={value === true} onChange={(e) => onChange(e.target.checked)} />
        <span>
          {input.label}
          <span className="block text-xs text-slate-500">Ticks this box in the agreement.</span>
        </span>
      </label>
    )
  }
  const text = typeof value === 'string' ? value : ''
  if (input.multiline) {
    return <TextArea id={id} label={label} hint={hint} value={text} maxLength={2000} onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <TextInput
      id={id}
      label={label}
      hint={hint}
      type={input.type === 'date' ? 'date' : 'text'}
      maxLength={2000}
      value={text}
      onChange={(e) => onChange(e.target.value)}
    />
  )
}
