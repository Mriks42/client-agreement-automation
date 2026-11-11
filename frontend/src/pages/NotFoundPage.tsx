import { Link } from 'react-router'

export function NotFoundPage() {
  return (
    <main className="mx-auto flex min-h-full max-w-md flex-col items-center justify-center px-4 text-center">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-slate-600">The link may be old or mistyped.</p>
      <Link to="/" className="mt-6 rounded-lg bg-brand px-4 py-2 font-medium text-brand-ink">
        Go home
      </Link>
    </main>
  )
}
