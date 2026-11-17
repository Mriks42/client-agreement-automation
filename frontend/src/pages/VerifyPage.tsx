import { useState, type DragEvent } from 'react'
import { Link } from 'react-router'
import { api } from '../api/client'
import { Alert, Button, Card, Spinner } from '../components/ui'
import { Wordmark } from '../components/Logo'
import { formatDate } from '../lib/format'
import { PRODUCT_NAME } from '../lib/product'

type Verdict =
  | { match: false }
  | {
      match: true
      agreement: string
      title: string
      firm: string
      completed_at: string
      signers: { name: string; role: string }[]
    }

type State =
  | { step: 'idle' }
  | { step: 'working'; name: string }
  | { step: 'done'; name: string; sha256: string; verdict: Verdict }
  | { step: 'error'; message: string }

async function sha256Hex(file: File): Promise<string> {
  if (!globalThis.crypto?.subtle) throw new Error('This browser can’t compute a fingerprint here. Try another browser.')
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, '0')).join('')
}

/** /verify : public. Is this PDF the signed original? The PDF never leaves the device: the
 * browser computes its SHA-256 and only that is looked up. */
export function VerifyPage() {
  const [state, setState] = useState<State>({ step: 'idle' })
  const [dragging, setDragging] = useState(false)

  const check = async (file: File | undefined) => {
    if (!file) return
    if (file.type !== 'application/pdf' && !file.name.toLowerCase().endsWith('.pdf')) {
      setState({ step: 'error', message: 'Choose the signed agreement as a PDF file.' })
      return
    }
    setState({ step: 'working', name: file.name })
    try {
      const sha256 = await sha256Hex(file)
      const verdict = await api.get<Verdict>(`/api/verify/${sha256}`)
      setState({ step: 'done', name: file.name, sha256, verdict })
    } catch (e) {
      setState({ step: 'error', message: e instanceof Error ? e.message : 'Couldn’t check it. Try again.' })
    }
  }
  const onDrop = (e: DragEvent) => {
    e.preventDefault()
    setDragging(false)
    void check(e.dataTransfer.files[0])
  }

  return (
    <div className="min-h-full bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-2xl items-center px-4 py-3">
          <Link to="/" aria-label={`${PRODUCT_NAME} home`}>
            <Wordmark size="sm" />
          </Link>
        </div>
      </header>
      <main className="mx-auto max-w-2xl px-4 py-8">
        <h1 className="text-2xl font-semibold tracking-tight">Check a signed agreement</h1>
        <p className="mt-2 text-sm text-slate-600">
          Choose a signed PDF to see whether it’s the original, unchanged since everyone signed it. Your browser works
          out the PDF’s fingerprint (its SHA-256) and only that is checked. The PDF itself never leaves your device.
        </p>

        {state.step === 'done' ? (
          <div className="mt-6 space-y-4">
            <Result verdict={state.verdict} />
            <Card>
              <p className="text-xs text-slate-500">
                File checked: <span className="font-medium text-slate-700">{state.name}</span>
              </p>
              <p className="mt-1 text-xs text-slate-500">
                SHA-256: <span className="font-mono break-all text-slate-700 select-all">{state.sha256}</span>
              </p>
            </Card>
            <Button variant="secondary" onClick={() => setState({ step: 'idle' })}>
              Check another PDF
            </Button>
          </div>
        ) : (
          <label
            htmlFor="verify-file"
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={`mt-6 flex cursor-pointer flex-col items-center justify-center rounded-xl border-2 border-dashed px-4 py-12 text-center transition ${
              dragging ? 'border-brand bg-brand/5' : 'border-slate-300 bg-white hover:border-slate-400'
            }`}
          >
            {state.step === 'working' ? (
              <>
                <Spinner className="h-6 w-6" />
                <span className="mt-3 text-sm text-slate-600">Checking {state.name}…</span>
              </>
            ) : (
              <>
                <span className="text-sm font-medium text-slate-900">Choose the signed PDF</span>
                <span className="mt-1 text-xs text-slate-500">or drop it here</span>
              </>
            )}
            <input
              id="verify-file"
              type="file"
              accept="application/pdf,.pdf"
              className="sr-only"
              disabled={state.step === 'working'}
              onChange={(e) => {
                void check(e.target.files?.[0])
                e.target.value = ''
              }}
            />
          </label>
        )}
        {state.step === 'error' && <Alert className="mt-4">{state.message}</Alert>}

        <p className="mt-8 text-xs text-slate-500">
          Every agreement signed with {PRODUCT_NAME} ends with an audit certificate, and its SHA-256 is in the email
          that delivered it. A copy that has been edited, re-saved or printed to PDF gets a different fingerprint, so it
          won’t match.
        </p>
      </main>
    </div>
  )
}

function Result({ verdict }: { verdict: Verdict }) {
  if (!verdict.match) {
    return (
      <Alert tone="warning" title="Not a match">
        This copy was changed, or wasn’t signed with {PRODUCT_NAME}.
      </Alert>
    )
  }
  return (
    <Alert tone="success" title="Match: this is the signed original">
      <p>It hasn’t changed since everyone signed it.</p>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-emerald-900/70">Agreement</dt>
        <dd className="font-medium">{verdict.agreement}</dd>
        <dt className="text-emerald-900/70">File</dt>
        <dd className="font-medium">{verdict.title}</dd>
        <dt className="text-emerald-900/70">Firm</dt>
        <dd className="font-medium">{verdict.firm}</dd>
        <dt className="text-emerald-900/70">Completed</dt>
        <dd className="font-medium">{formatDate(verdict.completed_at)}</dd>
        <dt className="text-emerald-900/70">Signed by</dt>
        <dd className="font-medium">
          {verdict.signers.map((s) => (
            <span key={`${s.role}-${s.name}`} className="block">
              {s.name} <span className="font-normal text-emerald-900/70">({s.role})</span>
            </span>
          ))}
        </dd>
      </dl>
    </Alert>
  )
}
