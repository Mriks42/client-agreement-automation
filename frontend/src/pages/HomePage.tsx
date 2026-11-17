import type { ReactNode } from 'react'
import { Link, Navigate } from 'react-router'
import { useMe } from '../api/hooks'
import { Wordmark } from '../components/Logo'
import { PRODUCT_TAGLINE } from '../lib/product'

const REPO = 'https://github.com/Mriks42/client-agreement-automation'

const STEPS: { title: string; text: string }[] = [
  { title: 'Upload your agreement once.', text: 'AI finds every blank and signature line, and a person checks it.' },
  { title: 'Open a file for each client.', text: 'Fill in their details, and it goes to each signer in turn, with reminders.' },
  { title: 'Know when work can start.', text: 'Everyone gets the signed PDF with an audit certificate, and the file says “ready to start work”.' },
]

const TRUST = [
  'Audit certificate and SHA-256 on every signed PDF',
  'Consent to sign electronically (ESIGN and UETA)',
  'A person approves what the AI proposes',
]

export function HomePage() {
  const me = useMe()
  if (me.data) return <Navigate to="/files" replace />

  return (
    <div className="flex min-h-full flex-col">
      <header className="mx-auto flex w-full max-w-6xl items-center justify-between px-4 py-5">
        <Link to="/" aria-label="Kickoff home">
          <Wordmark />
        </Link>
        <Link to="/login" className="rounded-md px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100">
          Log in
        </Link>
      </header>

      <main className="mx-auto grid w-full max-w-6xl flex-1 items-center gap-12 px-4 py-10 lg:grid-cols-[1.1fr_1fr] lg:gap-16 lg:py-16">
        <div className="min-w-0">
          <h1 className="text-4xl font-semibold tracking-tight text-balance text-slate-900 sm:text-5xl">
            Get your client agreement signed on every file.
          </h1>
          <p className="mt-4 text-lg text-slate-600">{PRODUCT_TAGLINE}</p>

          <ol className="mt-8 space-y-4">
            {STEPS.map((s, i) => (
              <li key={s.title} className="flex gap-3">
                <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand/10 text-xs font-semibold text-brand">
                  {i + 1}
                </span>
                <p className="text-slate-700">
                  <span className="font-semibold text-slate-900">{s.title}</span> {s.text}
                </p>
              </li>
            ))}
          </ol>

          <div className="mt-8 flex flex-wrap gap-3">
            <Link to="/signup" className="rounded-lg bg-brand px-4 py-2.5 font-medium text-brand-ink hover:brightness-110">
              Create your company’s account
            </Link>
            <Link to="/login" className="rounded-lg border border-slate-300 bg-white px-4 py-2.5 font-medium text-slate-800 hover:bg-slate-50">
              Log in
            </Link>
          </div>

          <ul className="mt-8 space-y-1.5 text-sm text-slate-600">
            {TRUST.map((t) => (
              <li key={t} className="flex items-start gap-2">
                <Check className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                {t}
              </li>
            ))}
          </ul>
        </div>

        <AgreementIllustration />
      </main>

      <footer className="border-t border-slate-200">
        <div className="mx-auto flex w-full max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-5 text-sm text-slate-500">
          <Link to="/verify" className="hover:text-slate-800">
            Check a signed agreement
          </Link>
          <a href="/api/docs" className="hover:text-slate-800">
            API docs
          </a>
          <a href={REPO} target="_blank" rel="noreferrer" className="hover:text-slate-800">
            GitHub
          </a>
        </div>
      </footer>
    </div>
  )
}

function Check({ className }: { className: string }) {
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden="true">
      <path d="M5 10.5l3.2 3.2L15 6.8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** A drawing of what Kickoff does, not a screenshot: an agreement with the client's details
 * filled in once, signed by both parties, and marked ready to start work. Test names only. */
function AgreementIllustration() {
  return (
    <div
      role="img"
      aria-label="An agreement with the client’s details filled in, signed by the client and the firm, and marked Signed: ready to start work"
      className="relative mx-auto w-full max-w-md px-2 pb-8 sm:px-4"
    >
      {/* The next page of the agreement, peeking out above, for depth. */}
      <div aria-hidden="true" className="absolute inset-x-8 -top-3 h-24 rounded-2xl border border-slate-200 bg-white shadow-sm sm:inset-x-10" />
      <div aria-hidden="true" className="relative rounded-2xl bg-white p-6 shadow-xl ring-1 ring-slate-200 sm:p-7">
        <div className="flex items-center justify-between">
          <p className="text-sm font-semibold text-slate-900">Client Services Agreement</p>
          <span className="text-[11px] text-slate-400">Page 1 of 3</span>
        </div>
        <Lines widths={['w-full', 'w-11/12', 'w-4/5']} />
        <div className="mt-4 space-y-2">
          <Filled label="Client" value="Acme Test Co" />
          <Filled label="Monthly fee" value="$5,000" />
          <Filled label="Start date" value="December 1, 2026" />
        </div>
        <Lines widths={['w-full', 'w-10/12', 'w-full', 'w-2/3']} />

        <div className="mt-6 grid grid-cols-2 gap-5">
          <SignatureLine name="Jane Client" role="Client" path="M4 26c6-14 12-20 14-12s-6 14 2 10 10-16 14-12-2 12 6 8 12-10 16-8 4 6 10 4 12-6 16-6" />
          <SignatureLine name="Alex Partner" role="Firm" path="M4 22c4-10 10-16 12-10s-8 12 0 10 8-12 14-12 0 10 8 8 10-8 14-6-4 8 4 6 14-6 18-4" />
        </div>

        <div className="mt-6 flex items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800 ring-1 ring-emerald-200">
          <Check className="h-4 w-4 shrink-0 text-emerald-600" />
          Signed: ready to start work
        </div>
      </div>

      <div
        aria-hidden="true"
        className="absolute right-0 bottom-0 flex items-center gap-2.5 rounded-xl bg-white px-3.5 py-2.5 shadow-lg ring-1 ring-slate-200 sm:-right-2"
      >
        <svg viewBox="0 0 24 24" className="h-6 w-6 shrink-0 text-brand" aria-hidden="true">
          <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" fill="currentColor" opacity=".12" />
          <path d="M12 3l7 3v5c0 4.5-3 8.2-7 10-4-1.8-7-5.5-7-10V6z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
          <path d="M8.8 12.2l2.2 2.2 4.2-4.6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <div className="leading-tight">
          <p className="text-xs font-semibold text-slate-900">Audit certificate</p>
          <p className="font-mono text-[11px] text-slate-500">SHA-256 f415e3…10e4</p>
        </div>
      </div>
    </div>
  )
}

function Lines({ widths }: { widths: string[] }) {
  return (
    <div className="mt-4 space-y-2">
      {widths.map((w, i) => (
        <div key={i} className={`h-1.5 rounded-full bg-slate-200 ${w}`} />
      ))}
    </div>
  )
}

function Filled({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center gap-3 text-sm">
      <span className="w-24 shrink-0 text-slate-500">{label}</span>
      <span className="min-w-0 flex-1 truncate rounded-md bg-brand/10 px-2 py-1 font-medium text-slate-900 ring-1 ring-brand/25">{value}</span>
    </div>
  )
}

function SignatureLine({ name, role, path }: { name: string; role: string; path: string }) {
  return (
    <div className="min-w-0">
      <svg viewBox="0 0 96 32" className="h-9 w-full text-slate-800" aria-hidden="true">
        <path d={path} fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <div className="border-t border-slate-300 pt-1.5">
        <p className="truncate text-xs font-medium text-slate-800">{name}</p>
        <p className="text-[11px] text-slate-500">{role}</p>
      </div>
    </div>
  )
}
