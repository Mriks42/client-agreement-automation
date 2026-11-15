import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router'
import { ApiError, errorProblems } from '../api/client'
import {
  useCopyLink,
  useDeleteFile,
  useEditRecipient,
  useFile,
  useRetryFinalize,
  useSendFile,
  useVoidFile,
} from '../api/hooks'
import type { FileDetail, FileEvent, Recipient } from '../api/types'
import {
  Alert,
  Button,
  Card,
  Dialog,
  DownloadLink,
  FileStatusBadge,
  FullPageSpinner,
  Spinner,
  TextArea,
  TextInput,
} from '../components/ui'
import { ordinal } from '../lib/fields'
import { formatDate } from '../lib/format'
import { NotFoundPage } from './NotFoundPage'

export function FilePage() {
  const { id = '' } = useParams()
  const file = useFile(id)
  if (file.isPending) return <FullPageSpinner />
  if (file.isError) {
    if (file.error instanceof ApiError && file.error.status === 404) return <NotFoundPage />
    return (
      <main className="mx-auto max-w-2xl px-4 py-8">
        <Alert>{file.error.message}</Alert>
      </main>
    )
  }
  const f = file.data
  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      <Link to="/files" className="text-sm text-slate-500 hover:text-slate-800">
        ← Files
      </Link>
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{f.title}</h1>
        <FileStatusBadge status={f.status} />
      </div>
      <p className="mt-1 text-sm text-slate-600">
        {f.template.name} · version {f.template.version}
      </p>
      {f.status === 'draft' ? <Draft file={f} /> : <Tracking file={f} />}
    </main>
  )
}

// ---- before sending: the exact PDF, then confirm --------------------------------------------
function Draft({ file: f }: { file: FileDetail }) {
  const send = useSendFile(f.id)
  const del = useDeleteFile(f.id)
  const navigate = useNavigate()
  const first = f.recipients[0]
  const conflict = send.error instanceof ApiError && send.error.status === 409
  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 space-y-3">
        <Alert tone="info" title="Check the agreement, then send it">
          These pages are exactly what {first?.name ?? 'the first signer'} will receive. Nothing has been sent yet.
        </Alert>
        {f.missing.length > 0 && (
          <Alert tone="warning" title={`${f.missing.length} blank${f.missing.length === 1 ? ' is' : 's are'} empty`}>
            <p>These will stay blank in the agreement:</p>
            <ul className="mt-1 list-disc pl-5">
              {f.missing.slice(0, 12).map((m, i) => (
                <li key={`${m}-${i}`}>{m}</li>
              ))}
            </ul>
            {f.missing.length > 12 && <p className="mt-1">and {f.missing.length - 12} more.</p>}
            <Link to={`/files/${f.id}/edit`} className="mt-2 inline-block font-medium underline">
              Fill them in
            </Link>
          </Alert>
        )}
        <Pages file={f} />
      </div>
      <aside className="space-y-4 lg:sticky lg:top-20 lg:self-start">
        <Card>
          <h2 className="text-sm font-semibold">Signers, in order</h2>
          <ol className="mt-2 space-y-2 text-sm">
            {f.recipients.map((r) => (
              <li key={r.id}>
                <span className="text-slate-500">{ordinal(r.order)}:</span> {r.name}{' '}
                <span className="text-slate-500">({r.role_label})</span>
                <span className="block truncate text-slate-500">{r.email}</span>
              </li>
            ))}
          </ol>
          <Button className="mt-4 w-full" busy={send.isPending} onClick={() => send.mutate({ revision: f.revision })}>
            Confirm and send
          </Button>
          <p className="mt-2 text-xs text-slate-500">
            {first?.name} gets an email with a signing link. Each signer after them is emailed when it’s their turn.
          </p>
          {send.error && (
            <Alert className="mt-3">
              {send.error.message}
              {conflict && (
                <Button variant="secondary" className="mt-2" onClick={() => window.location.reload()}>
                  Reload
                </Button>
              )}
            </Alert>
          )}
        </Card>
        <Card className="space-y-2">
          <Link to={`/files/${f.id}/edit`} className="block text-sm font-medium text-brand hover:underline">
            Edit details
          </Link>
          <a href={`/api/files/${f.id}/preview.pdf`} target="_blank" rel="noreferrer" className="block text-sm font-medium text-brand hover:underline">
            Open as PDF
          </a>
          <button
            type="button"
            className="block text-sm font-medium text-red-700 hover:underline"
            onClick={() => {
              if (window.confirm('Delete this draft? Nothing has been sent.')) {
                del.mutate(undefined, { onSuccess: () => navigate('/files') })
              }
            }}
          >
            Delete draft
          </button>
          {del.error && <Alert>{del.error.message}</Alert>}
        </Card>
      </aside>
    </div>
  )
}

// ---- after sending -------------------------------------------------------------------------
function Tracking({ file: f }: { file: FileDetail }) {
  const [voiding, setVoiding] = useState(false)
  const live = f.status === 'sent' || f.status === 'partially_signed'
  return (
    <div className="mt-6 grid gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
      <div className="min-w-0 space-y-4">
        <StatusBanner file={f} />
        <Card>
          <h2 className="text-sm font-semibold">Signers</h2>
          <ol className="mt-2 divide-y divide-slate-100">
            {f.recipients.map((r) => (
              <RecipientRow key={r.id} file={f} r={r} />
            ))}
          </ol>
        </Card>
        <details className="rounded-xl border border-slate-200 bg-white p-4">
          <summary className="cursor-pointer text-sm font-semibold">
            The agreement{f.signed_count > 0 ? ', with signatures so far' : ''}
          </summary>
          <div className="mt-3">
            <a href={`/api/files/${f.id}/preview.pdf`} target="_blank" rel="noreferrer" className="text-sm font-medium text-brand hover:underline">
              Open as PDF
            </a>
            <div className="mt-3">
              <Pages file={f} />
            </div>
          </div>
        </details>
      </div>
      <aside className="space-y-4">
        <Card>
          <h2 className="text-sm font-semibold">Timeline</h2>
          <Timeline events={f.events} />
        </Card>
        {live && (
          <Card>
            <h2 className="text-sm font-semibold">Stop this file</h2>
            <p className="mt-1 text-xs text-slate-500">Voiding withdraws the agreement. Its signing links stop working.</p>
            <Button variant="danger" className="mt-3" onClick={() => setVoiding(true)}>
              Void…
            </Button>
          </Card>
        )}
      </aside>
      <VoidDialog file={f} open={voiding} onClose={() => setVoiding(false)} />
    </div>
  )
}

function StatusBanner({ file: f }: { file: FileDetail }) {
  const active = f.recipients.find((r) => r.status === 'active')
  if (f.status === 'completed') return <Completed file={f} />

  if (f.status === 'declined') {
    const r = f.recipients.find((x) => x.status === 'declined')
    return (
      <Alert tone="error" title={`${r?.name ?? 'A signer'} declined to sign`}>
        {r?.decline_reason && <p className="italic">“{r.decline_reason}”</p>}
        <p className="mt-1">To try again, open a new file from the same agreement.</p>
      </Alert>
    )
  }
  if (f.status === 'voided') {
    return <Alert tone="warning" title="Voided">{f.void_reason}</Alert>
  }
  if (f.status === 'expired') {
    return <Alert tone="warning" title="Expired unsigned">The signing link ran out before everyone signed. Open a new file to try again.</Alert>
  }
  if (!active) return null
  const failed = active.last_email?.status === 'failed'
  if (failed) {
    return (
      <Alert tone="error" title={`We couldn’t email ${active.name}`}>
        <p>
          Sending to {active.last_email?.to} failed{active.last_email?.error ? `: ${active.last_email.error}` : '.'}
        </p>
        <p className="mt-1">Correct their email below, or copy their signing link and send it yourself.</p>
      </Alert>
    )
  }
  const sentAt = active.last_email?.at
  return (
    <Alert tone="info" title={`Waiting for ${active.name} (${active.role_label}) to sign`}>
      {sentAt ? (
        <p>
          Emailed to {active.last_email?.to} {formatDate(sentAt)}. If it hasn’t arrived in a few minutes, ask them to
          check their spam folder, or copy their signing link below and send it yourself.
        </p>
      ) : (
        <p>Their signing email is on its way.</p>
      )}
      <p className="mt-1 text-xs">Reminders go out automatically on day 2 and day 5{f.expires_at ? `. The link expires ${formatDate(f.expires_at)}` : ''}.</p>
    </Alert>
  )
}

function Completed({ file: f }: { file: FileDetail }) {
  const retry = useRetryFinalize(f.id)
  const when = f.completed_at ? ` ${formatDate(f.completed_at)}` : ''
  const failedCopies = f.events.filter((e) => e.type === 'email_failed' && e.data.reason === 'signed_copy')
  if (f.finalize_error && !f.finalized_at) {
    return (
      <Alert tone="error" title="Everyone signed, but we couldn’t make the signed PDF">
        <p>
          The signatures are safe. Making the signed copy with its audit certificate failed: {f.finalize_error}
        </p>
        <Button className="mt-2" busy={retry.isPending} onClick={() => retry.mutate()}>
          Try again
        </Button>
        {retry.error && <p className="mt-2">{retry.error.message}</p>}
      </Alert>
    )
  }
  if (!f.finalized_at) {
    return (
      <Alert tone="success" title="Signed: ready to start work">
        <p className="flex items-start gap-2">
          <Spinner className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            Everyone signed{when}. The signed copy, with its audit certificate, is being prepared. It will be emailed to
            every signer and to you.
          </span>
        </p>
      </Alert>
    )
  }
  return (
    <div className="space-y-3">
      <Alert tone="success" title="Signed: ready to start work">
        <p>
          Everyone signed{when}. The signed agreement, with its audit certificate, was emailed to every signer and to
          you.
        </p>
        <DownloadLink href={`/api/files/${f.id}/signed.pdf`} className="mt-3">
          Download signed agreement
        </DownloadLink>
        {f.final_sha256 && (
          <p className="mt-3 text-xs">
            SHA-256 of the signed PDF:{' '}
            <span className="font-mono break-all select-all">{f.final_sha256}</span>
          </p>
        )}
      </Alert>
      {failedCopies.length > 0 && (
        <Alert tone="warning" title="Some copies couldn’t be emailed">
          <ul className="list-disc pl-5">
            {failedCopies.map((e, i) => (
              <li key={i}>{String(e.data.to ?? '')}</li>
            ))}
          </ul>
          <p className="mt-1">Download the signed agreement above and send it to them yourself.</p>
        </Alert>
      )}
    </div>
  )
}

function RecipientRow({ file: f, r }: { file: FileDetail; r: Recipient }) {
  const live = f.status === 'sent' || f.status === 'partially_signed'
  const [editing, setEditing] = useState(false)
  const copy = useCopyLink(f.id)
  const [copied, setCopied] = useState<string | null>(null)
  const status = (() => {
    if (r.status === 'signed') return `Signed ${formatDate(r.signed_at)}`
    if (r.status === 'declined') return `Declined ${formatDate(r.declined_at)}`
    if (r.status === 'active') {
      if (r.viewed_at) return `Opened it ${formatDate(r.viewed_at)}, not signed yet`
      return 'Their turn to sign'
    }
    return 'Not their turn yet'
  })()
  const doCopy = () =>
    copy.mutate(r.id, {
      onSuccess: async ({ url }) => {
        setCopied(url)
        try {
          await navigator.clipboard.writeText(url)
        } catch {
          /* shown in the box below to copy by hand */
        }
      },
    })
  return (
    <li className="py-3 first:pt-1 last:pb-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <p className="text-sm">
          <span className="text-slate-500">{ordinal(r.order)}:</span> <span className="font-medium">{r.name}</span>{' '}
          <span className="text-slate-500">({r.role_label})</span>
        </p>
        <p className={`text-sm ${r.status === 'signed' ? 'text-emerald-700' : r.status === 'declined' ? 'text-red-700' : 'text-slate-600'}`}>
          {status}
        </p>
      </div>
      <p className="truncate text-sm text-slate-500">{r.email}</p>
      {live && (r.status === 'active' || r.status === 'pending') && (
        <div className="mt-2 flex flex-wrap gap-2">
          {r.status === 'active' && (
            <>
              <Button variant="secondary" busy={copy.isPending} onClick={doCopy}>
                Copy signing link
              </Button>
              <Button variant="secondary" onClick={() => setEditing(true)}>
                Resend
              </Button>
            </>
          )}
          {r.status === 'pending' && (
            <Button variant="ghost" onClick={() => setEditing(true)}>
              Change email
            </Button>
          )}
        </div>
      )}
      {copied && (
        <div className="mt-2 rounded-lg bg-slate-50 p-2.5 text-xs text-slate-600">
          <p>
            Copied. Paste it into a message to {r.name.split(' ')[0]}. It’s their personal link, so send it only to them.
          </p>
          <input
            readOnly
            aria-label="Signing link"
            value={copied}
            onFocus={(e) => e.currentTarget.select()}
            className="mt-1.5 w-full rounded border border-slate-200 bg-white px-2 py-1 font-mono text-[11px]"
          />
        </div>
      )}
      {copy.error && <Alert className="mt-2">{copy.error.message}</Alert>}
      <RecipientDialog file={f} r={r} open={editing} onClose={() => setEditing(false)} />
    </li>
  )
}

function RecipientDialog({ file: f, r, open, onClose }: { file: FileDetail; r: Recipient; open: boolean; onClose: () => void }) {
  const edit = useEditRecipient(f.id)
  const [name, setName] = useState(r.name)
  const [email, setEmail] = useState(r.email)
  const active = r.status === 'active'
  const problems = errorProblems(edit.error)
  return (
    <Dialog open={open} onClose={onClose} title={active ? `Resend to ${r.name}` : `Change ${r.name}’s details`}>
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          edit.mutate({ recipientId: r.id, name, email, resend: active }, { onSuccess: onClose })
        }}
      >
        <TextInput id={`rn-${r.id}`} label="Name" value={name} required onChange={(e) => setName(e.target.value)} />
        <TextInput id={`re-${r.id}`} label="Email" type="email" value={email} required onChange={(e) => setEmail(e.target.value)} />
        {active && email.trim().toLowerCase() !== r.email && (
          <p className="text-xs text-amber-800">Links sent to {r.email} will stop working.</p>
        )}
        {edit.error && <Alert>{problems.length ? problems.join(' ') : edit.error.message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" busy={edit.isPending}>
            {active ? 'Send it again' : 'Save'}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function VoidDialog({ file: f, open, onClose }: { file: FileDetail; open: boolean; onClose: () => void }) {
  const voidFile = useVoidFile(f.id)
  const [reason, setReason] = useState('')
  return (
    <Dialog open={open} onClose={onClose} title="Void this file?">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          voidFile.mutate({ reason }, { onSuccess: onClose })
        }}
      >
        <p className="text-sm text-slate-600">
          The agreement is withdrawn and its signing links stop working. Signatures already given stay in the audit trail.
        </p>
        <TextArea id="void-reason" label="Reason (signers who open their link see this)" required value={reason} onChange={(e) => setReason(e.target.value)} />
        {voidFile.error && <Alert>{errorProblems(voidFile.error).join(' ') || voidFile.error.message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Keep it
          </Button>
          <Button type="submit" variant="danger" busy={voidFile.isPending} disabled={!reason.trim()}>
            Void the file
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function describe(e: FileEvent): string {
  const who = e.recipient?.name ?? 'The signer'
  const by = e.actor.name ?? 'Someone'
  const d = e.data as Record<string, string | undefined>
  switch (e.type) {
    case 'created':
      return `${by} opened the file`
    case 'sent':
      return `${by} sent it for signature`
    case 'email_sent':
      return d.reason === 'resend' ? `Signing email sent again to ${who} (${d.to})` : `Signing email sent to ${who} (${d.to})`
    case 'reminded':
      return `Reminder sent to ${who}`
    case 'email_failed':
      return d.reason === 'signed_copy' ? `The signed copy couldn’t be emailed to ${d.to}` : `Email to ${d.to} failed`
    case 'finalized':
      return 'Signed PDF with audit certificate made, and emailed to everyone'
    case 'finalize_failed':
      return 'Making the signed PDF failed'
    case 'finalize_retried':
      return `${by} asked to make the signed PDF again`
    case 'viewed':
      return `${who} opened the agreement`
    case 'consented':
      return `${who} agreed to sign electronically`
    case 'signed':
      return `${who} signed`
    case 'declined':
      return `${who} declined: “${d.reason ?? ''}”`
    case 'completed':
      return 'Everyone has signed'
    case 'link_copied':
      return `${by} copied the signing link for ${who}, to share it directly`
    case 'email_corrected':
      return `${by} changed ${who}’s email from ${d.from} to ${d.to}`
    case 'name_corrected':
      return `${by} changed a signer’s name from ${d.from} to ${d.to}`
    case 'resend_requested':
      return `${by} resent the signing email to ${who}`
    case 'voided':
      return `${by} voided the file: “${d.reason ?? ''}”`
    case 'expired':
      return 'The signing link expired'
    default:
      return e.type.replace(/_/g, ' ')
  }
}

function Timeline({ events }: { events: FileEvent[] }) {
  return (
    <ol className="mt-3 space-y-3 border-l border-slate-200 pl-4">
      {[...events].reverse().map((e, i) => (
        <li key={`${e.at}-${i}`} className="relative text-sm">
          <span
            className={`absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full ring-2 ring-white ${
              e.type === 'signed' || e.type === 'completed' || e.type === 'finalized'
                ? 'bg-emerald-500'
                : e.type === 'declined' || e.type === 'email_failed' || e.type === 'voided' || e.type === 'finalize_failed'
                  ? 'bg-red-500'
                  : 'bg-slate-300'
            }`}
            aria-hidden
          />
          <p>{describe(e)}</p>
          <p className="text-xs text-slate-400">
            {formatDate(e.at)}
            {e.ip && e.actor.kind === 'recipient' ? ` · IP ${e.ip}` : ''}
          </p>
        </li>
      ))}
    </ol>
  )
}

function Pages({ file: f }: { file: FileDetail }) {
  return (
    <div className="space-y-3">
      {f.template.pages.map((p, i) => (
        <figure key={i}>
          <img
            src={`/api/files/${f.id}/pages/${i + 1}.png?v=${f.page_rev}`}
            alt={`Page ${i + 1}`}
            loading={i < 2 ? 'eager' : 'lazy'}
            className="w-full rounded bg-white shadow-sm ring-1 ring-slate-200"
            style={{ aspectRatio: `${p.width} / ${p.height}` }}
          />
          <figcaption className="mt-1 text-xs text-slate-400">
            Page {i + 1} of {f.template.page_count}
          </figcaption>
        </figure>
      ))}
    </div>
  )
}
