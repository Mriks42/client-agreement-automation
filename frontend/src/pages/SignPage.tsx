import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useParams } from 'react-router'
import { ApiError, errorProblems } from '../api/client'
import { useConsent, useDecline, useSignView, useSubmitSignature } from '../api/hooks'
import type { Mark, SignField, SignView } from '../api/types'
import { SignatureDialog } from '../components/signing/SignatureDialog'
import { Alert, Button, Dialog, DownloadLink, FullPageSpinner, Spinner, TextArea } from '../components/ui'
import { PRODUCT_NAME } from '../lib/product'
import { INK, signerHint, signerLabel, TYPED_STYLE } from '../lib/signing'

/** /sign/:token — the signer's page. No account: the link is their credential. */
export function SignPage() {
  const { token = '' } = useParams()
  const view = useSignView(token)
  if (view.isPending) return <FullPageSpinner />
  if (view.isError) {
    const invalid = view.error instanceof ApiError && view.error.status === 404
    return (
      <Frame>
        <Panel title={invalid ? 'This link isn’t valid' : 'Something went wrong'}>
          <p>
            {invalid
              ? 'If you were sent a newer email about this agreement, use the link in that one. Otherwise, ask the person who sent it for a new link.'
              : view.error.message}
          </p>
        </Panel>
      </Frame>
    )
  }
  const v = view.data
  if (v.state !== 'ready') return <Closed v={v} token={token} />
  if (!v.consented) return <Consent v={v} token={token} />
  return <Signing v={v} token={token} />
}

// ---- layout ---------------------------------------------------------------------------------
function Frame({ org, children }: { org?: string; children: ReactNode }) {
  return (
    <div className="min-h-full bg-slate-100">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-12 max-w-3xl items-center justify-between px-4">
          <span className="truncate text-sm font-semibold">{org ?? ''}</span>
          <span className="text-xs text-slate-400">Secured by {PRODUCT_NAME}</span>
        </div>
      </header>
      {children}
    </div>
  )
}

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="mx-auto max-w-xl px-4 py-8">
      <div className="rounded-2xl bg-white p-6 shadow-sm">
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        <div className="mt-3 space-y-3 text-sm leading-relaxed text-slate-700">{children}</div>
      </div>
    </main>
  )
}

function Parties({ v }: { v: SignView }) {
  if (v.parties.length < 2) return null
  return (
    <ol className="space-y-1 text-sm">
      {v.parties.map((p, i) => (
        <li key={i} className="flex items-center gap-2">
          <span
            className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
              p.status === 'signed' ? 'bg-emerald-100 text-emerald-800' : p.you ? 'bg-brand text-white' : 'bg-slate-200 text-slate-600'
            }`}
          >
            {p.status === 'signed' ? '✓' : i + 1}
          </span>
          <span>
            {p.name} <span className="text-slate-500">({p.role_label})</span>
            {p.you && <span className="font-medium"> · you</span>}
            {p.status === 'signed' && <span className="text-emerald-700"> · signed</span>}
          </span>
        </li>
      ))}
    </ol>
  )
}

// ---- states where there is nothing to sign --------------------------------------------------
function Closed({ v, token }: { v: SignView; token: string }) {
  const nextUp = v.parties.find((p) => p.status === 'active' && !p.you)
  const content: Record<Exclude<SignView['state'], 'ready'>, { title: string; body: ReactNode }> = {
    signed: {
      title: 'You’ve signed',
      body: (
        <>
          <p>Thank you, {v.signer.name.split(' ')[0]}. Your signature on {v.agreement_name} is recorded.</p>
          <p>
            {nextUp ? `${nextUp.name} (${nextUp.role_label}) signs next. ` : ''}
            When everyone has signed, you’ll get the completed agreement by email.
          </p>
        </>
      ),
    },
    completed: {
      title: 'Everyone has signed',
      body: v.signed_pdf_ready ? (
        <>
          <p>
            {v.agreement_name} is complete. Your signed copy, with its audit certificate, has been emailed to{' '}
            {v.signer.email}. You can also download it here.
          </p>
          <DownloadLink href={`/api/sign/${token}/signed.pdf`} className="w-full sm:w-auto">
            Download signed agreement
          </DownloadLink>
        </>
      ) : (
        <p className="flex items-start gap-2">
          <Spinner className="mt-0.5 h-4 w-4 shrink-0 text-slate-400" />
          <span>
            {v.agreement_name} is complete. We’re preparing the signed copy, with its audit certificate. It will be
            emailed to every party, and you can download it here in a moment.
          </span>
        </p>
      ),
    },
    declined: {
      title: 'You declined to sign',
      body: (
        <p>
          {v.org_name} has been told{v.decline_reason ? `, with your reason: “${v.decline_reason}”` : ''}. If that was a
          mistake, contact {v.sender.name} at {v.sender.email}.
        </p>
      ),
    },
    declined_by_other: {
      title: 'This agreement was declined',
      body: <p>Another party declined it, so it can no longer be signed. {v.org_name} will be in touch.</p>,
    },
    voided: {
      title: `${v.org_name} withdrew this agreement`,
      body: (
        <>
          {v.void_reason && <p className="italic">“{v.void_reason}”</p>}
          <p>There’s nothing for you to sign. Questions? Contact {v.sender.name} at {v.sender.email}.</p>
        </>
      ),
    },
    expired: {
      title: 'This link has expired',
      body: <p>Ask {v.sender.name} at {v.sender.email} to send you a new one.</p>,
    },
    unavailable: {
      title: 'Not ready for your signature',
      body: <p>You’ll get an email when it’s your turn to sign.</p>,
    },
  }
  const c = content[v.state as Exclude<SignView['state'], 'ready'>]
  return (
    <Frame org={v.org_name}>
      <Panel title={c.title}>
        {c.body}
        <Parties v={v} />
      </Panel>
      {(v.state === 'signed' || v.state === 'completed') && v.pages.length > 0 && (
        <details className="mx-auto max-w-3xl px-4 pb-10">
          <summary className="cursor-pointer text-sm font-medium text-brand">See what you signed</summary>
          <div className="mt-3 space-y-3">
            {v.pages.map((p, i) => (
              <PageImage key={i} token={token} v={v} page={i + 1} size={p} />
            ))}
          </div>
        </details>
      )}
    </Frame>
  )
}

// ---- step 1: consent (ESIGN) ------------------------------------------------------------------
function Consent({ v, token }: { v: SignView; token: string }) {
  const consent = useConsent(token)
  const [agree, setAgree] = useState(false)
  const [declining, setDeclining] = useState(false)
  return (
    <Frame org={v.org_name}>
      <Panel title={`${v.org_name} sent you ${v.agreement_name}`}>
        <p>
          Hi {v.signer.name.split(' ')[0]}, please review the agreement and sign it. It takes a couple of minutes, and
          works on your phone.
        </p>
        <Parties v={v} />
        <div className="rounded-lg bg-slate-50 p-3 text-xs leading-relaxed text-slate-600">
          <p className="font-medium text-slate-800">Using electronic records and signatures</p>
          <p className="mt-1">
            By continuing, you agree to receive this agreement and sign it electronically, and that your electronic
            signature has the same legal effect as one in ink. You can ask {v.sender.name} ({v.sender.email}) for a
            paper copy at any time. You can withdraw this consent before you sign by declining. You need a device with a
            current web browser, and an email address to receive your copy of the signed agreement.
          </p>
        </div>
        <label className="flex items-start gap-2.5 text-sm">
          <input type="checkbox" className="mt-0.5 h-5 w-5 shrink-0" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
          <span>I agree to use electronic records and signatures.</span>
        </label>
        {consent.error && <Alert>{consent.error.message}</Alert>}
        <Button className="w-full py-3 text-base" disabled={!agree} busy={consent.isPending} onClick={() => consent.mutate(undefined)}>
          Continue
        </Button>
        <button type="button" className="w-full text-center text-sm text-slate-500 hover:text-slate-800" onClick={() => setDeclining(true)}>
          Decline to sign
        </button>
      </Panel>
      <DeclineDialog token={token} v={v} open={declining} onClose={() => setDeclining(false)} />
    </Frame>
  )
}

// ---- step 2: fill in and sign ---------------------------------------------------------------
type Values = Record<string, string | boolean>

const isMark = (f: SignField) => f.type === 'signature' || f.type === 'initials'
const needed = (f: SignField) =>
  f.type === 'signature' || (f.required && f.type !== 'checkbox' && f.type !== 'date_signed')

function today(): string {
  return new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric' }).format(new Date())
}

function longDate(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  return new Intl.DateTimeFormat('en-US', { month: 'long', day: 'numeric', year: 'numeric' }).format(new Date(y, m - 1, d))
}

function Signing({ v, token }: { v: SignView; token: string }) {
  const submit = useSubmitSignature(token)
  // Reading order, so "Next" walks the document from top to bottom.
  const fields = useMemo(
    () =>
      [...v.fields]
        .sort((a, b) => a.page - b.page || a.rect.y0 - b.rect.y0 || a.rect.x0 - b.rect.x0)
        .map((f) => ({ ...f, label: signerLabel(f.label, v.signer.role_label) })),
    [v.fields, v.signer.role_label],
  )
  const [values, setValues] = useState<Values>(() => {
    const init: Values = {}
    for (const f of fields) if (f.type === 'name') init[f.key] = v.signer.name
    return init
  })
  const [signature, setSignature] = useState<Mark | null>(null)
  const [initials, setInitials] = useState<Mark | null>(null)
  const [editing, setEditing] = useState<SignField | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [declining, setDeclining] = useState(false)
  const date = useMemo(() => today(), [])

  const filled = (f: SignField) => {
    if (f.type === 'signature') return signature !== null
    if (f.type === 'initials') return initials !== null
    if (f.type === 'date_signed') return true
    const value = values[f.key]
    return f.type === 'checkbox' ? value === true : typeof value === 'string' && value.trim() !== ''
  }
  // One step per value: fields sharing a key (or every signature box) are done together.
  const steps = useMemo(() => {
    const seen = new Set<string>()
    return fields.filter((f) => {
      if (!needed(f)) return false
      const id = isMark(f) ? f.type : `k:${f.key}`
      if (seen.has(id)) return false
      seen.add(id)
      return true
    })
  }, [fields])
  const remaining = steps.filter((f) => !filled(f))
  const next = remaining[0] ?? null

  const goTo = (f: SignField) => {
    document.getElementById(`fld-${f.id}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    if (f.type === 'checkbox') return
    setTimeout(() => setEditing(f), 250)
  }
  const tap = (f: SignField) => {
    if (f.type === 'date_signed') return
    if (f.type === 'checkbox') {
      setValues((s) => ({ ...s, [f.key]: s[f.key] !== true }))
      return
    }
    setEditing(f)
  }
  const send = () =>
    submit.mutate(
      {
        values,
        signature,
        initials,
        tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      },
      { onSuccess: () => window.scrollTo({ top: 0 }) },
    )
  const problems = errorProblems(submit.error)

  return (
    <Frame org={v.org_name}>
      <div className="sticky top-0 z-20 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-3 px-4 py-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{v.agreement_name}</p>
            <p className="text-xs text-slate-500">
              {steps.length - remaining.length} of {steps.length} done · {v.signer.role_label}
            </p>
          </div>
          <button type="button" className="shrink-0 text-sm text-slate-500 hover:text-slate-800" onClick={() => setDeclining(true)}>
            Decline
          </button>
        </div>
      </div>

      <main className="mx-auto max-w-3xl px-2 pt-3 pb-36 sm:px-4">
        <p className="mb-3 px-2 text-sm text-slate-600">
          Read the agreement. Tap <span className="font-medium">Next</span> to go to each place that needs you. Boxes
          with a dashed outline are optional.
        </p>
        <div className="space-y-3">
          {v.pages.map((p, i) => (
            <PageImage key={i} token={token} v={v} page={i + 1} size={p}>
              {(scale) =>
                fields
                  .filter((f) => f.page === i + 1)
                  .map((f) => (
                    <FieldBox
                      key={f.id}
                      f={f}
                      scale={scale}
                      size={p}
                      filled={filled(f)}
                      isNext={next !== null && (isMark(next) ? f.type === next.type : f.key === next.key)}
                      display={display(f, values, signature, initials, date)}
                      onTap={() => tap(f)}
                    />
                  ))
              }
            </PageImage>
          ))}
        </div>
      </main>

      <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white px-4 py-3 shadow-[0_-4px_12px_rgba(15,23,42,0.06)]">
        <div className="mx-auto flex max-w-3xl items-center gap-3">
          {next ? (
            <>
              <p className="min-w-0 flex-1 truncate text-sm">
                <span className="text-slate-500">Next:</span> {stepLabel(next)}
              </p>
              <Button className="px-6 py-2.5 text-base" onClick={() => goTo(next)}>
                Next
              </Button>
            </>
          ) : (
            <>
              <p className="min-w-0 flex-1 text-sm text-slate-600">All done. Check it over, then sign.</p>
              <Button className="px-6 py-2.5 text-base" onClick={() => setConfirming(true)}>
                Finish
              </Button>
            </>
          )}
        </div>
      </div>

      {editing && isMark(editing) && (
        <SignatureDialog
          open
          what={editing.type === 'signature' ? 'signature' : 'initials'}
          signerName={v.signer.name}
          current={editing.type === 'signature' ? signature : initials}
          onClose={() => setEditing(null)}
          onAdopt={(m) => {
            if (editing.type === 'signature') setSignature(m)
            else setInitials(m)
            setEditing(null)
          }}
        />
      )}
      {editing && !isMark(editing) && (
        <ValueDialog
          field={editing}
          value={typeof values[editing.key] === 'string' ? (values[editing.key] as string) : ''}
          onClose={() => setEditing(null)}
          onSave={(text) => {
            setValues((s) => ({ ...s, [editing.key]: text }))
            setEditing(null)
          }}
        />
      )}

      <Dialog open={confirming} onClose={() => setConfirming(false)} title="Sign the agreement">
        <p className="text-sm text-slate-700">
          By selecting <span className="font-medium">Sign</span>, you sign {v.agreement_name} electronically, with the
          signature and details shown. {v.org_name} and every party will get a copy once everyone has signed.
        </p>
        {submit.error && (
          <Alert className="mt-3">
            {problems.length ? (
              <ul className="list-disc pl-5">
                {problems.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : (
              submit.error.message
            )}
          </Alert>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirming(false)}>
            Back
          </Button>
          <Button busy={submit.isPending} onClick={send} className="px-6">
            Sign
          </Button>
        </div>
      </Dialog>
      <DeclineDialog token={token} v={v} open={declining} onClose={() => setDeclining(false)} />
    </Frame>
  )
}

function stepLabel(f: SignField): string {
  if (f.type === 'signature') return 'Sign here'
  if (f.type === 'initials') return 'Your initials'
  return f.label
}

type Display = { kind: 'text'; text: string; auto?: boolean } | { kind: 'mark'; mark: Mark } | { kind: 'check' } | null

function display(f: SignField, values: Values, signature: Mark | null, initials: Mark | null, date: string): Display {
  if (f.type === 'signature') return signature ? { kind: 'mark', mark: signature } : null
  if (f.type === 'initials') return initials ? { kind: 'mark', mark: initials } : null
  if (f.type === 'date_signed') return { kind: 'text', text: date, auto: true }
  const v = values[f.key]
  if (f.type === 'checkbox') return v === true ? { kind: 'check' } : null
  if (typeof v !== 'string' || !v.trim()) return null
  return { kind: 'text', text: f.type === 'date' ? longDate(v) : v }
}

function FieldBox({
  f,
  scale,
  size,
  filled,
  isNext,
  display: d,
  onTap,
}: {
  f: SignField
  scale: number
  size: { width: number; height: number }
  filled: boolean
  isNext: boolean
  display: Display
  onTap: () => void
}) {
  const r = f.rect
  const h = r.y1 - r.y0
  const hPx = h * scale
  // Mirror pdf_engine._write, so the value sits where the signed PDF will put it: text at up to
  // 11 pt (72% of the box), shrunk to fit the width; a typed signature at up to 20 pt (80%); the
  // baseline max(1.5 pt, 18% of the height) above the bottom. CSS percentage padding would be a
  // share of the width, not the height, so everything here is in pixels.
  const text = d?.kind === 'text' ? d.text : d?.kind === 'mark' && d.mark.kind === 'typed' ? d.mark.text : ''
  const sig = d?.kind === 'mark'
  let sizePt = sig ? Math.min(h * 0.8, 20) : Math.min(h * 0.72, 11)
  const widthPt = text.length * sizePt * (sig ? 0.42 : 0.5)
  if (widthPt > r.x1 - r.x0 - 3) sizePt = Math.max(4, (sizePt * (r.x1 - r.x0 - 3)) / widthPt)
  const fontPx = Math.max(6, sizePt * scale)
  const lift = Math.max(0, Math.max(1.5, h * 0.18) * scale - fontPx * 0.2)
  const optional = !needed(f) && f.type !== 'date_signed'
  const auto = f.type === 'date_signed'
  const ring = isNext && !filled ? 'animate-pulse border-amber-500 bg-amber-200/60' : ''
  const base = filled
    ? 'border-transparent bg-sky-50/40 hover:border-sky-300'
    : optional
      ? 'border-dashed border-sky-400 bg-sky-50/50'
      : 'border-amber-400 bg-amber-100/70'
  const label =
    f.type === 'signature' ? 'Sign' : f.type === 'initials' ? 'Initial' : f.type === 'checkbox' ? '' : f.label
  return (
    <button
      type="button"
      id={`fld-${f.id}`}
      aria-label={`${stepLabel(f)}${filled ? ' (done)' : ''}`}
      disabled={auto}
      onClick={onTap}
      className={`absolute flex scroll-my-32 items-end overflow-visible rounded-[2px] border text-left leading-none before:absolute before:-inset-1.5 ${auto ? 'cursor-default border-transparent' : `${base} ${ring}`}`}
      style={{
        left: `${(r.x0 / size.width) * 100}%`,
        top: `${(r.y0 / size.height) * 100}%`,
        width: `${((r.x1 - r.x0) / size.width) * 100}%`,
        height: `${((r.y1 - r.y0) / size.height) * 100}%`,
      }}
    >
      {d === null && label && (
        <span className="pointer-events-none truncate px-0.5 font-medium text-amber-900" style={{ fontSize: Math.max(8, Math.min(hPx * 0.7, 12)) }}>
          {label}
        </span>
      )}
      {d?.kind === 'text' && (
        <span
          className={`pointer-events-none overflow-hidden whitespace-nowrap ${d.auto ? 'text-slate-700' : 'text-slate-900'}`}
          style={{ fontSize: fontPx, paddingBottom: lift, paddingLeft: 1.5 * scale, fontFamily: 'Helvetica, Arial, sans-serif' }}
        >
          {d.text}
        </span>
      )}
      {d?.kind === 'check' && (
        <span className="pointer-events-none flex h-full w-full items-center justify-center font-bold" style={{ color: INK, fontSize: hPx * 0.9 }}>
          ✕
        </span>
      )}
      {d?.kind === 'mark' &&
        (d.mark.kind === 'drawn' ? (
          <img src={d.mark.png} alt="" className="pointer-events-none h-full w-full object-contain object-left-bottom" />
        ) : (
          <span
            className="pointer-events-none overflow-hidden whitespace-nowrap"
            style={{ ...TYPED_STYLE, fontSize: fontPx, paddingBottom: lift, paddingLeft: 1.5 * scale }}
          >
            {d.mark.text}
          </span>
        ))}
    </button>
  )
}

function PageImage({
  token,
  v,
  page,
  size,
  children,
}: {
  token: string
  v: SignView
  page: number
  size: { width: number; height: number }
  children?: (scale: number) => ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1)
  const [loaded, setLoaded] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const update = () => setScale(el.clientWidth / size.width)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [size.width])
  return (
    <div ref={ref} className="relative bg-white shadow-sm ring-1 ring-slate-200" style={{ aspectRatio: `${size.width} / ${size.height}` }}>
      <img
        src={`/api/sign/${token}/pages/${page}.png?v=${v.page_rev}`}
        alt={`Page ${page} of ${v.pages.length}`}
        loading={page <= 2 ? 'eager' : 'lazy'}
        onLoad={() => setLoaded(true)}
        draggable={false}
        className="block h-full w-full select-none"
      />
      {!loaded && (
        <div className="absolute inset-0 flex items-center justify-center text-slate-300">
          <Spinner />
        </div>
      )}
      {children?.(scale)}
    </div>
  )
}

function ValueDialog({
  field,
  value,
  onClose,
  onSave,
}: {
  field: SignField
  value: string
  onClose: () => void
  onSave: (text: string) => void
}) {
  const [text, setText] = useState(value)
  const tall = field.rect.y1 - field.rect.y0 >= 22 && field.type === 'text'
  return (
    <Dialog open onClose={onClose} title={field.label}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
          onSave(text)
        }}
      >
        {tall ? (
          <TextArea id="sign-value" label={field.label} value={text} maxLength={2000} onChange={(e) => setText(e.target.value)} />
        ) : (
          <div>
            <label htmlFor="sign-value" className="sr-only">
              {field.label}
            </label>
            <input
              id="sign-value"
              type={field.type === 'date' ? 'date' : 'text'}
              value={text}
              maxLength={2000}
              autoComplete={field.type === 'name' ? 'name' : 'off'}
              onChange={(e) => setText(e.target.value)}
              className="block w-full rounded-lg border border-slate-300 px-3 py-2.5 text-base focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
            />
          </div>
        )}
        {signerHint(field.label) && <p className="mt-1.5 text-sm text-slate-500">{signerHint(field.label)}</p>}
        {!field.required && <p className="mt-1 text-xs text-slate-500">Optional.</p>}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="submit" disabled={field.required && !text.trim()}>
            Done
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function DeclineDialog({ token, v, open, onClose }: { token: string; v: SignView; open: boolean; onClose: () => void }) {
  const decline = useDecline(token)
  const [reason, setReason] = useState('')
  return (
    <Dialog open={open} onClose={onClose} title="Decline to sign?">
      <form
        className="space-y-3"
        onSubmit={(e) => {
          e.preventDefault()
          decline.mutate({ reason }, { onSuccess: onClose })
        }}
      >
        <p className="text-sm text-slate-600">
          {v.org_name} will be told you declined, with your reason. The agreement can’t be signed after that.
        </p>
        <TextArea id="decline-reason" label="Why are you declining?" required value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} />
        {decline.error && <Alert>{errorProblems(decline.error).join(' ') || decline.error.message}</Alert>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Back
          </Button>
          <Button type="submit" variant="danger" busy={decline.isPending} disabled={!reason.trim()}>
            Decline
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
