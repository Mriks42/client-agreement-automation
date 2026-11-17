import { useState, type FormEvent, type ReactNode } from 'react'
import { errorProblems } from '../api/client'
import {
  useApiKeys,
  useBranding,
  useCopyInviteLink,
  useCreateApiKey,
  useDeleteWebhook,
  useInviteMember,
  useRemoveLogo,
  useRemoveMember,
  useRevokeInvite,
  useRetryDelivery,
  useRevokeApiKey,
  useSaveWebhook,
  useSetBrandColor,
  useTeam,
  useTestWebhook,
  useUploadLogo,
  useWebhook,
  useWebhookDeliveries,
} from '../api/hooks'
import type { NewApiKey, WebhookDelivery } from '../api/types'
import { Alert, Button, Card, Spinner, TextInput } from '../components/ui'
import { formatDate } from '../lib/format'

/** /settings : connect the firm's other systems. They open files with an API key and hear back
 * through a webhook. */
export function SettingsPage() {
  return (
    <main className="mx-auto max-w-3xl space-y-6 px-4 py-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-slate-600">
          Your team, your logo and colour, and your other systems: a CRM or practice manager can open a file with an
          API key, and a webhook tells it when the agreement is signed so work can start.
        </p>
      </div>
      <TeamCard />
      <BrandingCard />
      <ApiKeys />
      <Webhook />
      <QuickStart />
    </main>
  )
}

function copy(text: string) {
  void navigator.clipboard?.writeText(text).catch(() => undefined)
}

function Secret({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false)
  return (
    <div className="flex gap-2">
      <input
        readOnly
        aria-label={label}
        value={value}
        onFocus={(e) => e.target.select()}
        className="block w-full min-w-0 rounded-lg border border-slate-300 bg-slate-50 px-3 py-2 font-mono text-xs"
      />
      <Button
        variant="secondary"
        onClick={() => {
          copy(value)
          setCopied(true)
        }}
      >
        {copied ? 'Copied' : 'Copy'}
      </Button>
    </div>
  )
}

// ---- team ---------------------------------------------------------------------------------------
function TeamCard() {
  const team = useTeam()
  const inviteMember = useInviteMember()
  const copyLink = useCopyInviteLink()
  const revoke = useRevokeInvite()
  const remove = useRemoveMember()
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [copied, setCopied] = useState<{ id: string; url: string } | null>(null)
  const [sentTo, setSentTo] = useState<string | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const to = email.trim()
    inviteMember.mutate(
      { email: to, name: name.trim() || undefined },
      {
        onSuccess: () => {
          setSentTo(to)
          setEmail('')
          setName('')
        },
      },
    )
  }
  const problems = errorProblems(inviteMember.error)

  return (
    <Card>
      <h2 className="text-base font-semibold">Team</h2>
      <p className="mt-1 text-sm text-slate-600">
        Everyone here has their own login and sees the same agreements and files. Files they send go out in their name.
      </p>
      {team.isPending ? (
        <Spinner className="mt-4 h-5 w-5 text-slate-400" />
      ) : (
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {team.data?.members.map((m) => (
            <li key={m.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className="font-medium">
                  {m.name} {m.you && <span className="font-normal text-slate-500">(you)</span>}
                </p>
                <p className="truncate text-xs text-slate-500">
                  {m.email} · joined {formatDate(m.joined_at)}
                </p>
              </div>
              {!m.you && (
                <Button
                  variant="ghost"
                  busy={remove.isPending && remove.variables === m.id}
                  onClick={() => {
                    if (window.confirm(`Remove ${m.name}? They’re logged out at once, and the API keys they made stop working.`)) {
                      remove.mutate(m.id)
                    }
                  }}
                >
                  Remove
                </Button>
              )}
            </li>
          ))}
          {team.data?.invites.map((i) => (
            <li key={i.id} className="py-2.5">
              <div className="flex items-center justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-medium">
                    {i.name ?? i.email} <span className="font-normal text-amber-700">invited</span>
                  </p>
                  <p className="truncate text-xs text-slate-500">
                    {i.email} · by {i.invited_by_name} · link works until {formatDate(i.expires_at)}
                  </p>
                </div>
                <div className="flex shrink-0 gap-1">
                  <Button
                    variant="secondary"
                    busy={copyLink.isPending && copyLink.variables === i.id}
                    onClick={() =>
                      copyLink.mutate(i.id, {
                        onSuccess: ({ url }) => {
                          setCopied({ id: i.id, url })
                          copy(url)
                        },
                      })
                    }
                  >
                    Copy invite link
                  </Button>
                  <Button variant="ghost" busy={revoke.isPending && revoke.variables === i.id} onClick={() => revoke.mutate(i.id)}>
                    Revoke
                  </Button>
                </div>
              </div>
              {copied?.id === i.id && (
                <div className="mt-2">
                  <Secret value={copied.url} label="Invite link" />
                  <p className="mt-1 text-xs text-slate-500">Copied. Send it to {i.email} yourself; it’s just for them.</p>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {sentTo && <Alert tone="success" className="mt-3">Invitation emailed to {sentTo}. If it doesn’t arrive, use Copy invite link.</Alert>}
      <form onSubmit={submit} className="mt-4 grid gap-2 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <TextInput id="invite-name" label="Name (optional)" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
        <TextInput id="invite-email" label="Email" type="email" required maxLength={254} value={email} onChange={(e) => setEmail(e.target.value)} />
        <Button type="submit" busy={inviteMember.isPending}>
          Invite
        </Button>
      </form>
      {inviteMember.error && <Alert className="mt-3">{problems.length ? problems.join(' ') : inviteMember.error.message}</Alert>}
      {(remove.error || revoke.error || copyLink.error) && (
        <Alert className="mt-3">{(remove.error ?? revoke.error ?? copyLink.error)?.message}</Alert>
      )}
    </Card>
  )
}

// ---- API keys -----------------------------------------------------------------------------------
const DEFAULT_BRAND = '#2f5bea'
const HEX = /^#[0-9a-f]{6}$/

function luminance(hex: string): number {
  const ch = (i: number) => {
    const s = parseInt(hex.slice(i, i + 2), 16) / 255
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * ch(1) + 0.7152 * ch(3) + 0.0722 * ch(5)
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x)
  return (hi + 0.05) / (lo + 0.05)
}

/** Same rule as the server (services/branding.ink): white, else near-black, else black. */
function inkFor(hex: string): string {
  return ['#ffffff', '#0f172a'].find((c) => contrast(hex, c) >= 4.5) ?? '#000000'
}

function normalizeHex(value: string): string {
  let v = value.trim().toLowerCase()
  if (!v.startsWith('#')) v = `#${v}`
  if (/^#[0-9a-f]{3}$/.test(v)) v = `#${[...v.slice(1)].map((c) => c + c).join('')}`
  return v
}

function BrandingCard() {
  const branding = useBranding()
  const upload = useUploadLogo()
  const removeLogo = useRemoveLogo()
  const setColor = useSetBrandColor()
  const [draft, setDraft] = useState<string | null>(null)
  const saved = branding.data?.color ?? null
  const typed = draft ?? saved ?? DEFAULT_BRAND
  const hex = normalizeHex(typed)
  const valid = HEX.test(hex)
  const logo = branding.data?.logo_url

  return (
    <Card>
      <h2 className="text-base font-semibold">Branding</h2>
      <p className="mt-1 text-sm text-slate-600">
        Your logo and colour on the emails signers get and on the signing page. “Secured by Kickoff” stays. Without
        them, signers see the standard look with your company name.
      </p>
      {branding.isPending ? (
        <Spinner className="mt-4 h-5 w-5 text-slate-400" />
      ) : (
        <div className="mt-4 grid gap-6 sm:grid-cols-2">
          <div>
            <h3 className="text-sm font-medium text-slate-800">Logo</h3>
            {logo ? (
              <img src={logo} alt="Your logo" className="mt-2 h-12 w-auto max-w-[220px] rounded border border-slate-200 bg-white object-contain p-1" />
            ) : (
              <p className="mt-2 text-sm text-slate-500">No logo yet.</p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-slate-300 bg-white px-3.5 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-brand">
                {upload.isPending && <Spinner className="h-4 w-4" />}
                {logo ? 'Replace logo' : 'Upload logo'}
                <input
                  type="file"
                  accept="image/png,image/jpeg"
                  className="sr-only"
                  disabled={upload.isPending}
                  onChange={(e) => {
                    const file = e.target.files?.[0]
                    if (file) upload.mutate(file)
                    e.target.value = ''
                  }}
                />
              </label>
              {logo && (
                <Button variant="ghost" busy={removeLogo.isPending} onClick={() => removeLogo.mutate()}>
                  Remove
                </Button>
              )}
            </div>
            <p className="mt-2 text-xs text-slate-500">PNG or JPG, up to 512 KB. A wide logo about 80 pixels tall looks best.</p>
            {upload.isError && <Alert className="mt-2">{upload.error.message}</Alert>}
          </div>
          <div>
            <h3 className="text-sm font-medium text-slate-800">Colour</h3>
            <div className="mt-2 flex items-center gap-2">
              <input
                type="color"
                aria-label="Pick a colour"
                value={valid ? hex : DEFAULT_BRAND}
                onChange={(e) => setDraft(e.target.value)}
                className="h-9 w-12 cursor-pointer rounded border border-slate-300 bg-white p-0.5"
              />
              <input
                aria-label="Colour as a hex value"
                value={typed}
                maxLength={7}
                onChange={(e) => setDraft(e.target.value)}
                className="block w-28 rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
              />
            </div>
            <p className="mt-3 text-xs text-slate-500">How buttons look:</p>
            <span
              className="mt-1 inline-block rounded-lg px-4 py-2 text-sm font-semibold"
              style={valid ? { background: hex, color: inkFor(hex) } : { background: DEFAULT_BRAND, color: '#ffffff' }}
            >
              Review and sign
            </span>
            {!valid && <p className="mt-1 text-xs text-red-700">Enter a hex value, like #1e40af.</p>}
            <div className="mt-3 flex flex-wrap gap-2">
              <Button
                disabled={!valid || hex === saved || (!saved && hex === DEFAULT_BRAND)}
                busy={setColor.isPending && setColor.variables !== null}
                onClick={() => setColor.mutate(hex, { onSuccess: () => setDraft(null) })}
              >
                Save colour
              </Button>
              {saved && (
                <Button
                  variant="ghost"
                  busy={setColor.isPending && setColor.variables === null}
                  onClick={() => setColor.mutate(null, { onSuccess: () => setDraft(null) })}
                >
                  Use the standard colour
                </Button>
              )}
            </div>
            <p className="mt-2 text-xs text-slate-500">The text on it is white or dark, whichever is easier to read.</p>
            {setColor.isError && <Alert className="mt-2">{setColor.error.message}</Alert>}
          </div>
        </div>
      )}
    </Card>
  )
}

function ApiKeys() {
  const keys = useApiKeys()
  const create = useCreateApiKey()
  const revoke = useRevokeApiKey()
  const [name, setName] = useState('')
  const [made, setMade] = useState<NewApiKey | null>(null)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    create.mutate(name.trim() || 'API key', {
      onSuccess: (k) => {
        setMade(k)
        setName('')
      },
    })
  }
  const live = keys.data?.filter((k) => !k.revoked_at) ?? []
  const revoked = keys.data?.filter((k) => k.revoked_at) ?? []

  return (
    <Card>
      <h2 className="text-base font-semibold">API keys</h2>
      <p className="mt-1 text-sm text-slate-600">
        A key lets another system open files for you. Files it opens wait as drafts: you check the preview and send
        them, as with any file.
      </p>
      {made && (
        <Alert tone="success" title={`Your new key, “${made.name}”`} className="mt-3">
          <p className="mb-2">Copy it now and keep it somewhere safe. It won’t be shown again.</p>
          <Secret value={made.key} label="New API key" />
        </Alert>
      )}
      <form onSubmit={submit} className="mt-4 flex items-end gap-2">
        <TextInput
          id="key-name"
          label="Name"
          placeholder="e.g. CRM"
          maxLength={80}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="flex-1"
        />
        <Button type="submit" busy={create.isPending}>
          Create key
        </Button>
      </form>
      {create.error && <Alert className="mt-3">{create.error.message}</Alert>}
      {keys.isPending ? (
        <Spinner className="mt-4 h-5 w-5 text-slate-400" />
      ) : live.length + revoked.length === 0 ? (
        <p className="mt-4 text-sm text-slate-500">No keys yet.</p>
      ) : (
        <ul className="mt-4 divide-y divide-slate-100 text-sm">
          {[...live, ...revoked].map((k) => (
            <li key={k.id} className="flex items-center justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p className={`font-medium ${k.revoked_at ? 'text-slate-400 line-through' : ''}`}>{k.name}</p>
                <p className="truncate text-xs text-slate-500">
                  <span className="font-mono">{k.prefix}…</span> · created {formatDate(k.created_at)} by{' '}
                  {k.created_by_name}
                  {k.last_used_at ? ` · last used ${formatDate(k.last_used_at)}` : ' · never used'}
                  {k.revoked_at ? ` · revoked ${formatDate(k.revoked_at)}` : ''}
                </p>
              </div>
              {!k.revoked_at && (
                <Button
                  variant="ghost"
                  busy={revoke.isPending && revoke.variables === k.id}
                  onClick={() => {
                    if (window.confirm(`Revoke “${k.name}”? Anything using it stops working at once.`)) {
                      revoke.mutate(k.id)
                    }
                  }}
                >
                  Revoke
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}

// ---- webhook ------------------------------------------------------------------------------------
function Webhook() {
  const hook = useWebhook()
  const save = useSaveWebhook()
  const remove = useDeleteWebhook()
  const test = useTestWebhook()
  const endpoint = hook.data?.endpoint ?? null
  const [url, setUrl] = useState<string | null>(null)
  const [showSecret, setShowSecret] = useState(false)
  const value = url ?? endpoint?.url ?? ''
  const problems = errorProblems(save.error)

  const submit = (e: FormEvent) => {
    e.preventDefault()
    save.mutate({ url: value }, { onSuccess: () => setUrl(null) })
  }

  return (
    <>
      <Card>
        <h2 className="text-base font-semibold">Webhook</h2>
        <p className="mt-1 text-sm text-slate-600">
          We POST an event to this address when a file is sent, each time someone signs, when the signed PDF is ready
          (<span className="font-mono text-xs">file.completed</span>), and if a file is declined, voided or expires.
          Failed deliveries are retried for about an hour and a half.
        </p>
        {hook.isPending ? (
          <Spinner className="mt-4 h-5 w-5 text-slate-400" />
        ) : (
          <form onSubmit={submit} className="mt-4 space-y-3">
            <div className="flex items-end gap-2">
              <TextInput
                id="webhook-url"
                label="Endpoint address"
                type="url"
                required
                placeholder="https://example.com/hooks/kickoff"
                maxLength={500}
                value={value}
                onChange={(e) => setUrl(e.target.value)}
                className="flex-1"
              />
              <Button type="submit" busy={save.isPending} disabled={!!endpoint && value === endpoint.url}>
                Save
              </Button>
            </div>
            {save.error && (
              <Alert>{problems.length ? problems.join(' ') : save.error.message}</Alert>
            )}
          </form>
        )}
        {endpoint && (
          <div className="mt-4 space-y-3 border-t border-slate-100 pt-4">
            <div>
              <p className="text-sm font-medium text-slate-700">Signing secret</p>
              <p className="text-xs text-slate-500">
                Use it to check each delivery came from us (see “Using the API” below).
              </p>
              <div className="mt-1.5">
                {showSecret ? (
                  <Secret value={endpoint.secret} label="Signing secret" />
                ) : (
                  <Button variant="secondary" onClick={() => setShowSecret(true)}>
                    Show secret
                  </Button>
                )}
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="secondary" busy={test.isPending} onClick={() => test.mutate()}>
                Send a test event
              </Button>
              <Button
                variant="ghost"
                busy={save.isPending}
                onClick={() => {
                  if (window.confirm('Make a new signing secret? The old one stops working at once.')) {
                    save.mutate({ url: endpoint.url, rotate_secret: true }, { onSuccess: () => setShowSecret(true) })
                  }
                }}
              >
                New secret
              </Button>
              <Button
                variant="ghost"
                className="text-red-700"
                busy={remove.isPending}
                onClick={() => {
                  if (window.confirm('Remove the webhook? Nothing more will be sent to it.')) remove.mutate()
                }}
              >
                Remove
              </Button>
            </div>
            {test.isSuccess && <p className="text-sm text-slate-600">Test event queued. It shows below in a moment.</p>}
          </div>
        )}
      </Card>
      {endpoint && <Deliveries />}
    </>
  )
}

function Deliveries() {
  const deliveries = useWebhookDeliveries(true)
  const retry = useRetryDelivery()
  return (
    <Card>
      <h2 className="text-base font-semibold">Recent deliveries</h2>
      {deliveries.isPending ? (
        <Spinner className="mt-4 h-5 w-5 text-slate-400" />
      ) : !deliveries.data?.length ? (
        <p className="mt-2 text-sm text-slate-500">Nothing sent yet. Send a test event, or send a file.</p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-100 text-sm">
          {deliveries.data.map((d) => (
            <li key={d.id} className="flex items-start justify-between gap-3 py-2.5">
              <div className="min-w-0">
                <p>
                  <span className="font-mono text-xs">{d.event_type}</span>{' '}
                  <span className="text-xs text-slate-500">{formatDate(d.created_at)}</span>
                </p>
                <p className="text-xs">{describe(d)}</p>
              </div>
              {d.status === 'dead' && (
                <Button
                  variant="secondary"
                  busy={retry.isPending && retry.variables === d.id}
                  onClick={() => retry.mutate(d.id)}
                >
                  Retry
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {retry.error && <Alert className="mt-3">{retry.error.message}</Alert>}
    </Card>
  )
}

function describe(d: WebhookDelivery): ReactNode {
  if (d.status === 'done') return <span className="text-emerald-700">Delivered{d.status_code ? ` (HTTP ${d.status_code})` : ''}</span>
  if (d.status === 'dead') return <span className="text-red-700">Gave up after {d.attempts} attempts. {d.last_error}</span>
  if (d.attempts === 0 || d.status === 'running') return <span className="text-slate-500">Sending…</span>
  return (
    <span className="text-amber-800">
      {d.last_error} Trying again{d.next_attempt_at ? ` at ${formatDate(d.next_attempt_at)}` : ''}.
    </span>
  )
}

// ---- how to use it ------------------------------------------------------------------------------
function QuickStart() {
  const base = window.location.origin
  const open = `curl ${base}/api/v1/files \\
  -H "Authorization: Bearer $KICKOFF_KEY" \\
  -H "Idempotency-Key: matter-1042" \\
  -H "Content-Type: application/json" \\
  -d '{
    "template_id": "<from GET /api/v1/templates>",
    "signers": [{"role": "client", "name": "Jane Client", "email": "jane@example.com"}],
    "values": {"client_name": "Acme Test Co"},
    "metadata": {"matter_id": "1042"}
  }'`
  const verify = `# X-Kickoff-Signature: t=<unix time>,v1=<signature>
expected = HMAC_SHA256(secret, t + "." + raw_request_body)  # hex
accept if expected == v1 and t is within 5 minutes of now`
  return (
    <Card>
      <h2 className="text-base font-semibold">Using the API</h2>
      <ol className="mt-2 list-decimal space-y-1.5 pl-5 text-sm text-slate-700">
        <li>
          <span className="font-mono text-xs">GET /api/v1/templates</span> lists your approved agreements, with each
          signer’s role and the details a file fills in.
        </li>
        <li>
          <span className="font-mono text-xs">POST /api/v1/files</span> opens a file. It waits as a draft until someone
          here checks the preview and sends it. The response’s <span className="font-mono text-xs">url</span> is where
          they do that.
        </li>
        <li>
          Webhook events carry the file, including your <span className="font-mono text-xs">metadata</span>. On{' '}
          <span className="font-mono text-xs">file.completed</span>, download the signed PDF from{' '}
          <span className="font-mono text-xs">data.file.signed_pdf.url</span> with your key.
        </li>
      </ol>
      <pre className="mt-3 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">{open}</pre>
      <p className="mt-3 text-sm font-medium text-slate-700">Checking a webhook came from us</p>
      <pre className="mt-1 overflow-x-auto rounded-lg bg-slate-900 p-3 text-xs leading-relaxed text-slate-100">{verify}</pre>
      <p className="mt-3 text-sm text-slate-600">
        Full reference:{' '}
        <a href="/api/docs" target="_blank" rel="noreferrer" className="font-medium text-brand hover:underline">
          /api/docs
        </a>
        .
      </p>
    </Card>
  )
}
