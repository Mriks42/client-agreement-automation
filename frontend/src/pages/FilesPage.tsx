import { Link } from 'react-router'
import { useFiles, useTemplates } from '../api/hooks'
import type { FileSummary } from '../api/types'
import { Alert, FileStatusBadge, FullPageSpinner } from '../components/ui'
import { formatDate } from '../lib/format'

export function FilesPage() {
  const files = useFiles()
  const templates = useTemplates()
  const active = templates.data?.filter((t) => t.status === 'active') ?? []
  const newFileTo = active.length === 1 ? `/files/new?template=${active[0].id}` : '/files/new'

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Files</h1>
          <p className="mt-1 text-sm text-slate-600">
            Each file is one job for one client. Open one and its agreement goes out for signature.
          </p>
        </div>
        {active.length > 0 && (
          <Link
            to={newFileTo}
            className="rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink hover:brightness-110"
          >
            New file
          </Link>
        )}
      </div>

      {(files.isPending || templates.isPending) && <FullPageSpinner />}
      {files.isError && <Alert className="mt-6">{files.error.message}</Alert>}
      {files.data?.length === 0 && templates.data && <Empty hasAgreement={active.length > 0} to={newFileTo} />}
      {files.data && files.data.length > 0 && (
        <ul className="mt-6 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {files.data.map((f) => (
            <li key={f.id}>
              <Link
                to={`/files/${f.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-slate-50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{f.title}</span>
                  <span className="block truncate text-sm text-slate-500">{f.agreement_name}</span>
                </span>
                <span className="w-56 text-sm text-slate-600">{progress(f)}</span>
                <FileStatusBadge status={f.status} />
                <span className="w-28 text-right text-sm text-slate-400">{formatDate(f.updated_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}

function progress(f: FileSummary): string {
  if (f.waiting_for) return `Waiting for ${f.waiting_for.name} (${f.signed_count} of ${f.signer_count} signed)`
  if (f.status === 'completed') return 'Signed: ready to start work'
  if (f.status === 'draft') return 'Not sent yet'
  return ''
}

function Empty({ hasAgreement, to }: { hasAgreement: boolean; to: string }) {
  return (
    <div className="mt-8 rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
      {hasAgreement ? (
        <>
          <h2 className="text-lg font-medium">No files yet</h2>
          <p className="mt-1 text-sm text-slate-600">
            Open a file for your next client. You fill in their details once, check the agreement,
            and send it.
          </p>
          <Link to={to} className="mt-5 inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-ink">
            Open your first file
          </Link>
        </>
      ) : (
        <>
          <h2 className="text-lg font-medium">First, set up your agreement</h2>
          <p className="mt-1 text-sm text-slate-600">
            Upload the agreement your clients sign and approve its fields. Then every new file uses it.
          </p>
          <Link to="/templates" className="mt-5 inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-ink">
            Go to agreements
          </Link>
        </>
      )}
    </div>
  )
}
