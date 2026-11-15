import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router'
import { ApiError } from '../api/client'
import { usePlaceManually, useRetryAnalysis, useTemplate } from '../api/hooks'
import type { Template } from '../api/types'
import { ReviewEditor } from '../components/editor/ReviewEditor'
import { Alert, Button, FullPageSpinner, Spinner } from '../components/ui'
import { NotFoundPage } from './NotFoundPage'

export function TemplatePage() {
  const { id = '' } = useParams()
  const template = useTemplate(id)

  if (template.isPending) return <FullPageSpinner />
  if (template.isError) {
    if (template.error instanceof ApiError && template.error.status === 404) return <NotFoundPage />
    return (
      <main className="mx-auto max-w-2xl px-4 py-8">
        <Alert>{template.error.message}</Alert>
      </main>
    )
  }
  const t = template.data
  if (t.status === 'analyzing') return <Analyzing template={t} />
  if (t.status === 'analysis_failed') return <Failed template={t} />
  return <ReviewEditor key={t.id} template={t} />
}

function useElapsed(since: string | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return since ? Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000)) : 0
}

function Analyzing({ template }: { template: Template }) {
  const elapsed = useElapsed(template.analysis_started_at)
  const minutes = Math.floor(elapsed / 60)
  const seconds = String(elapsed % 60).padStart(2, '0')
  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <Link to="/templates" className="text-sm text-slate-500 hover:text-slate-800">
        ← Agreements
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{template.name}</h1>
      <div className="mt-6 rounded-xl border border-slate-200 bg-white p-6">
        <div className="flex items-start gap-4">
          <Spinner className="mt-0.5 h-6 w-6 shrink-0 text-brand" />
          <div>
            <p className="font-medium">Reading your agreement…</p>
            <p className="mt-1 text-sm text-slate-600">
              We’re finding every blank and signature line and working out who fills each one. This
              usually takes 1–3 minutes; long agreements take a little longer. You can leave this
              page and come back.
            </p>
            <p className="mt-2 text-xs tabular-nums text-slate-400">
              {minutes}:{seconds} elapsed
            </p>
            {elapsed > 360 && (
              <Alert tone="warning" className="mt-3">
                This is taking longer than usual. If something went wrong, it’s retried
                automatically, and you’ll be offered other options here if it can’t finish.
              </Alert>
            )}
          </div>
        </div>
      </div>
      <PagePreview template={template} />
    </main>
  )
}

function Failed({ template }: { template: Template }) {
  const retry = useRetryAnalysis(template.id)
  const manual = usePlaceManually(template.id)
  const error = retry.error ?? manual.error
  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <Link to="/templates" className="text-sm text-slate-500 hover:text-slate-800">
        ← Agreements
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{template.name}</h1>
      <div className="mt-6 rounded-xl border border-red-200 bg-white p-6">
        <p className="font-medium text-red-800">{template.error ?? 'We couldn’t finish reading this document.'}</p>
        <p className="mt-1 text-sm text-slate-600">Choose how to continue:</p>
        <div className="mt-4 flex flex-wrap gap-3">
          <Button onClick={() => retry.mutate(undefined)} busy={retry.isPending}>
            Try again
          </Button>
          <Button variant="secondary" onClick={() => manual.mutate(undefined)} busy={manual.isPending}>
            Place the fields myself
          </Button>
          <Link
            to="/templates/new"
            className="inline-flex items-center rounded-lg px-3.5 py-2 text-sm font-medium text-slate-600 hover:bg-slate-100"
          >
            Upload a different file
          </Link>
        </div>
        {template.source_format === 'docx' && (
          <p className="mt-3 text-sm text-slate-500">
            Tip: Word documents sometimes convert imperfectly. Saving it as a PDF in Word (File →
            Save As → PDF) and uploading that often works better.
          </p>
        )}
        {error && <Alert className="mt-3">{error.message}</Alert>}
      </div>
      <PagePreview template={template} />
    </main>
  )
}

function PagePreview({ template }: { template: Template }) {
  const shown = template.pages.slice(0, 8)
  return (
    <div className="mt-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
      {shown.map((p, i) => (
        <img
          key={i}
          src={`/api/templates/${template.id}/pages/${i + 1}.png?v=${template.pdf_rev}`}
          alt={`Page ${i + 1}`}
          loading="lazy"
          className="w-full rounded bg-white shadow-sm ring-1 ring-slate-200"
          style={{ aspectRatio: `${p.width} / ${p.height}` }}
        />
      ))}
      {template.page_count > shown.length && (
        <p className="col-span-full text-sm text-slate-500">
          and {template.page_count - shown.length} more page(s)
        </p>
      )}
    </div>
  )
}
