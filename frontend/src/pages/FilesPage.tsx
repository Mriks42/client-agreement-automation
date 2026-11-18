import { useState } from 'react'
import { Link } from 'react-router'
import { useFiles, useMe, useTemplates } from '../api/hooks'
import type { FileSummary } from '../api/types'
import { Alert, FileStatusBadge, FullPageSpinner } from '../components/ui'
import { formatDate } from '../lib/format'

export function FilesPage() {
  const files = useFiles()
  const templates = useTemplates()
  const me = useMe()
  const active = templates.data?.filter((t) => t.status === 'active') ?? []
  const newFileTo = active.length === 1 ? `/files/new?template=${active[0].id}` : '/files/new'
  const [filter, setFilter] = useState<Filter | null>(null)
  const [query, setQuery] = useState('')
  const myEmail = me.data?.user.email
  const all = files.data ?? []
  const q = query.trim().toLowerCase()
  const shown = all.filter(
    (f) =>
      (!filter || matches(f, filter, myEmail)) &&
      (!q ||
        [f.title, f.agreement_name, f.waiting_for?.name ?? '', f.waiting_for?.email ?? ''].some((s) =>
          s.toLowerCase().includes(q),
        )),
  )

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
      {all.length > 0 && <Summary files={all} myEmail={myEmail} filter={filter} onFilter={setFilter} />}
      {all.length > 0 && (
        <div className="mt-4">
          <label htmlFor="files-search" className="sr-only">
            Search files
          </label>
          <input
            id="files-search"
            type="search"
            autoComplete="off"
            placeholder="Search by client, file or agreement"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
          />
        </div>
      )}
      {(filter || q) && (
        <p className="mt-3 text-sm text-slate-600">
          Showing {shown.length} {shown.length === 1 ? 'file' : 'files'}
          {filter ? `: ${FILTER_LABELS[filter].toLowerCase()}` : ''}
          {q ? ` matching “${query.trim()}”` : ''}.{' '}
          <button
            type="button"
            onClick={() => {
              setFilter(null)
              setQuery('')
            }}
            className="font-medium text-brand hover:underline"
          >
            Show all
          </button>
        </p>
      )}
      {all.length > 0 && shown.length > 0 && (
        <ul className="mt-4 divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
          {shown.map((f) => (
            <li key={f.id}>
              <Link
                to={`/files/${f.id}`}
                className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-3 hover:bg-slate-50"
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate font-medium">{f.title}</span>
                  <span className="block truncate text-sm text-slate-500">{f.agreement_name}</span>
                </span>
                <span className="w-56 text-sm text-slate-600">{progress(f, me.data?.user.email)}</span>
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

// ---- the summary above the list -----------------------------------------------------------------
type Filter = 'others' | 'you' | 'week'

const FILTER_LABELS: Record<Filter, string> = {
  others: 'Waiting on others',
  you: 'Waiting on you',
  week: 'Signed this week',
}

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

function isMine(f: FileSummary, myEmail: string | undefined): boolean {
  return !!(f.waiting_for && myEmail && f.waiting_for.email.toLowerCase() === myEmail.toLowerCase())
}

function matches(f: FileSummary, filter: Filter, myEmail: string | undefined): boolean {
  if (filter === 'you') return isMine(f, myEmail)
  if (filter === 'others') return !!f.waiting_for && !isMine(f, myEmail)
  return f.status === 'completed' && !!f.completed_at && Date.now() - Date.parse(f.completed_at) <= WEEK_MS
}

/** Hours from sending to the last signature, averaged over signed files. */
function averageTimeToSign(files: FileSummary[]): string | null {
  const spans = files
    .filter((f) => f.status === 'completed' && f.sent_at && f.completed_at)
    .map((f) => Date.parse(f.completed_at as string) - Date.parse(f.sent_at as string))
    .filter((ms) => ms >= 0)
  if (spans.length === 0) return null
  const hours = spans.reduce((a, b) => a + b, 0) / spans.length / 3_600_000
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`
  if (hours < 48) return `${Math.round(hours)} ${Math.round(hours) === 1 ? 'hour' : 'hours'}`
  return `${(hours / 24).toFixed(1)} days`
}

function Summary({
  files,
  myEmail,
  filter,
  onFilter,
}: {
  files: FileSummary[]
  myEmail: string | undefined
  filter: Filter | null
  onFilter: (f: Filter | null) => void
}) {
  const count = (k: Filter) => files.filter((f) => matches(f, k, myEmail)).length
  const average = averageTimeToSign(files)
  const tile = 'flex flex-col items-start rounded-xl border bg-white px-4 py-3 text-left'
  return (
    <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
      {(['others', 'you', 'week'] as Filter[]).map((k) => {
        const n = count(k)
        const on = filter === k
        return (
          <button
            key={k}
            type="button"
            aria-pressed={on}
            onClick={() => onFilter(on ? null : k)}
            className={`${tile} transition hover:border-slate-400 focus-visible:outline-2 focus-visible:outline-brand ${
              on ? 'border-brand ring-2 ring-brand/20' : 'border-slate-200'
            }`}
          >
            <span className={`text-2xl font-semibold tabular-nums ${k === 'you' && n > 0 ? 'text-brand' : 'text-slate-900'}`}>{n}</span>
            <span className="text-sm text-slate-600">{FILTER_LABELS[k]}</span>
          </button>
        )
      })}
      <div className={`${tile} border-slate-200`}>
        <span className="text-2xl font-semibold tabular-nums text-slate-900">{average ?? '–'}</span>
        <span className="text-sm text-slate-600">{average ? 'Average time to sign' : 'Average time to sign, once a file is signed'}</span>
      </div>
    </div>
  )
}

function progress(f: FileSummary, myEmail: string | undefined): string {
  if (f.waiting_for && myEmail && f.waiting_for.email.toLowerCase() === myEmail.toLowerCase()) {
    return `Waiting for you to sign (${f.signed_count} of ${f.signer_count} signed)`
  }
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
