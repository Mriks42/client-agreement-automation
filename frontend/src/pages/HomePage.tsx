import { useQuery } from '@tanstack/react-query'
import { PRODUCT_NAME, PRODUCT_TAGLINE } from '../lib/product'

type Health = {
  status: 'ok' | 'degraded'
  version: string
  checks: Record<string, { ok: boolean; error?: string; backend?: string }>
}

export function HomePage() {
  // /api/health answers 503 when degraded but still has a useful body, so read it either way.
  const health = useQuery({
    queryKey: ['health'],
    queryFn: async () => {
      const res = await fetch('/api/health')
      return (await res.json()) as Health
    },
    refetchInterval: 30_000,
  })

  return (
    <main className="mx-auto flex min-h-full max-w-3xl flex-col justify-center px-4 py-16">
      <p className="text-sm font-semibold uppercase tracking-wide text-brand">{PRODUCT_NAME}</p>
      <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">
        Get your client agreement signed on every file.
      </h1>
      <p className="mt-4 text-lg text-slate-600">{PRODUCT_TAGLINE}</p>

      <section className="mt-10 rounded-xl border border-slate-200 bg-white p-5">
        <h2 className="text-sm font-medium text-slate-500">System status</h2>
        {health.isPending && <p className="mt-2 text-slate-500">Checking…</p>}
        {health.isError && <p className="mt-2 text-red-600">Can’t reach the server.</p>}
        {health.data && (
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {Object.entries(health.data.checks).map(([name, check]) => (
              <li key={name} className="flex items-center gap-2 text-sm">
                <span
                  className={`h-2 w-2 rounded-full ${check.ok ? 'bg-emerald-500' : 'bg-amber-500'}`}
                  aria-hidden
                />
                <span className="capitalize">{name}</span>
                <span className="text-slate-400">{check.ok ? 'ok' : check.error ?? 'not ready'}</span>
              </li>
            ))}
          </ul>
        )}
        {health.data && (
          <p className="mt-4 text-xs text-slate-400">version {health.data.version}</p>
        )}
      </section>
    </main>
  )
}
