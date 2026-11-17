import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { errorProblems } from '../api/client'
import { useClients, useDeleteClient, useEditClient } from '../api/hooks'
import type { SavedClient } from '../api/types'
import { Alert, Button, Card, Dialog, Spinner, TextInput } from '../components/ui'
import { formatDate } from '../lib/format'

/** /clients : the saved clients, to correct or delete. Clients are saved when a file is sent. */
export function ClientsPage() {
  const [q, setQ] = useState('')
  const [term, setTerm] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setTerm(q.trim()), 200)
    return () => clearTimeout(t)
  }, [q])
  const clients = useClients(term, 50)
  const [editing, setEditing] = useState<SavedClient | null>(null)
  const [deleting, setDeleting] = useState<SavedClient | null>(null)
  const list = clients.data ?? []

  return (
    <main className="mx-auto max-w-3xl space-y-4 px-4 py-8">
      <div>
        <Link to="/settings" className="text-sm text-slate-500 hover:text-slate-800">
          ← Settings
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">Saved clients</h1>
        <p className="mt-1 text-sm text-slate-600">
          A client is saved each time you send them a file. On <b>New file</b>, pick them in the Client box to fill in
          their details. Correct or delete them here; files already sent don’t change.
        </p>
      </div>
      <TextInput
        id="clients-search"
        label="Search"
        placeholder="Company, name or email"
        autoComplete="off"
        value={q}
        onChange={(e) => setQ(e.target.value)}
      />
      {clients.isPending ? (
        <Spinner className="h-5 w-5 text-slate-400" />
      ) : clients.isError ? (
        <Alert>{clients.error.message}</Alert>
      ) : list.length === 0 ? (
        <Card>
          <p className="text-sm text-slate-600">
            {term ? 'No saved client matches.' : 'No saved clients yet. They’re added when you send a file.'}
          </p>
        </Card>
      ) : (
        <ul className="space-y-3">
          {list.map((c) => (
            <li key={c.id}>
              <Card className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium text-slate-900">{c.company || c.contact_name}</p>
                  <p className="text-sm break-words text-slate-600">
                    {c.company && c.contact_name ? `${c.contact_name}${c.title ? `, ${c.title}` : ''} · ` : ''}
                    {c.email}
                  </p>
                  {(c.address || c.phone) && (
                    <p className="text-sm break-words text-slate-500">{[c.address, c.phone].filter(Boolean).join(' · ')}</p>
                  )}
                  <p className="mt-1 text-xs text-slate-400">
                    {c.files_sent} file{c.files_sent === 1 ? '' : 's'} sent
                    {c.last_used_at ? ` · last ${formatDate(c.last_used_at)}` : ''}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => setEditing(c)}>
                    Edit
                  </Button>
                  <Button variant="danger" onClick={() => setDeleting(c)}>
                    Delete
                  </Button>
                </div>
              </Card>
            </li>
          ))}
        </ul>
      )}
      {editing && <EditDialog key={editing.id} client={editing} onClose={() => setEditing(null)} />}
      {deleting && <DeleteDialog key={deleting.id} client={deleting} onClose={() => setDeleting(null)} />}
    </main>
  )
}

const FIELDS: { key: keyof SavedClient; label: string; type?: string }[] = [
  { key: 'company', label: 'Company' },
  { key: 'contact_name', label: 'Contact name' },
  { key: 'email', label: 'Email', type: 'email' },
  { key: 'title', label: 'Title' },
  { key: 'phone', label: 'Phone' },
  { key: 'address', label: 'Address' },
]

function EditDialog({ client, onClose }: { client: SavedClient; onClose: () => void }) {
  const edit = useEditClient()
  const [form, setForm] = useState<Record<string, string>>(() =>
    Object.fromEntries(FIELDS.map((f) => [f.key, String(client[f.key] ?? '')])),
  )
  const problems = errorProblems(edit.error)
  return (
    <Dialog open onClose={onClose} title={`Edit ${client.company || client.contact_name}`}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          edit.mutate({ id: client.id, ...form }, { onSuccess: onClose })
        }}
      >
        {FIELDS.map((f) => (
          <TextInput
            key={f.key}
            id={`client-${f.key}`}
            label={f.label}
            type={f.type ?? 'text'}
            maxLength={300}
            required={f.key === 'email'}
            value={form[f.key]}
            onChange={(e) => setForm((s) => ({ ...s, [f.key]: e.target.value }))}
          />
        ))}
        <p className="text-xs text-slate-500">The next file for this client fills in these details.</p>
        {edit.error && <Alert>{problems.length ? problems.join(' ') : edit.error.message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={edit.isPending}>
            Save
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function DeleteDialog({ client, onClose }: { client: SavedClient; onClose: () => void }) {
  const remove = useDeleteClient()
  return (
    <Dialog open onClose={onClose} title={`Delete ${client.company || client.contact_name}?`}>
      <p className="text-sm text-slate-600">
        They won’t appear in the Client box any more. Files already sent to them don’t change, and sending them another
        file saves them again.
      </p>
      {remove.error && <Alert className="mt-3">{remove.error.message}</Alert>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button variant="danger" busy={remove.isPending} onClick={() => remove.mutate(client.id, { onSuccess: onClose })}>
          Delete
        </Button>
      </div>
    </Dialog>
  )
}
