import { Link } from 'react-router'
import { useTemplates } from '../api/hooks'
import { Alert, FullPageSpinner, StatusBadge } from '../components/ui'
import { formatDate } from '../lib/format'

export function TemplatesPage() {
  const templates = useTemplates()

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Agreements</h1>
          <p className="mt-1 text-sm text-slate-600">
            Upload your client agreement once. After you approve its fields, every new file uses it.
          </p>
        </div>
        <Link
          to="/templates/new"
          className="rounded-lg bg-brand px-3.5 py-2 text-sm font-medium text-brand-ink hover:brightness-110"
        >
          Upload an agreement
        </Link>
      </div>

      {templates.isPending && <FullPageSpinner />}
      {templates.isError && <Alert className="mt-6">{templates.error.message}</Alert>}
      {templates.data?.length === 0 && (
        <div className="mt-8 rounded-xl border border-dashed border-slate-300 bg-white p-10 text-center">
          <h2 className="text-lg font-medium">No agreements yet</h2>
          <p className="mt-1 text-sm text-slate-600">
            Upload the agreement your clients sign (PDF or Word). We’ll find the blanks and
            signature lines for you to check.
          </p>
          <Link
            to="/templates/new"
            className="mt-5 inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-ink"
          >
            Upload your agreement
          </Link>
        </div>
      )}
      {templates.data && templates.data.length > 0 && (
        <ul className="mt-6 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {templates.data.map((t) => (
            <li key={t.id}>
              <Link
                to={`/templates/${t.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-slate-50"
              >
                <span className="min-w-0 flex-1 truncate font-medium">{t.name}</span>
                <StatusBadge status={t.status} />
                <span className="w-40 text-sm text-slate-500">
                  {t.page_count} page{t.page_count === 1 ? '' : 's'}
                  {t.status !== 'analyzing' && ` · ${t.field_count} fields`}
                </span>
                <span className="w-32 text-right text-sm text-slate-400">{formatDate(t.updated_at)}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  )
}
