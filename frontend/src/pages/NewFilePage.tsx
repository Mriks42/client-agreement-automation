import { useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router'
import { errorProblems } from '../api/client'
import { useCreateFile, useEditFile, useFile, useMe, useTemplate, useTemplates } from '../api/hooks'
import type { FileInput, Role, SenderInput } from '../api/types'
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
  busy,
  error,
  onSubmit,
}: {
  inputs: SenderInput[]
  roles: Role[]
  initial: { title: string; values: Values; people: People }
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

  return (
    <form onSubmit={submit} className="space-y-4">
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
