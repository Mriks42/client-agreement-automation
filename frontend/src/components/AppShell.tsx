import { Link, Navigate, NavLink, Outlet, useLocation, useNavigate } from 'react-router'
import { useLogout, useMe } from '../api/hooks'
import { PRODUCT_NAME } from '../lib/product'
import { FullPageSpinner } from './ui'

/** Signed-in layout. Sends visitors without a session to the login page. */
export function AppShell() {
  const me = useMe()
  const logout = useLogout()
  const navigate = useNavigate()
  const location = useLocation()

  if (me.isPending) return <FullPageSpinner />
  if (me.isError) {
    return (
      <p className="p-8 text-center text-red-700">
        Can’t reach the server. Check your connection and reload the page.
      </p>
    )
  }
  if (!me.data) {
    const next = encodeURIComponent(location.pathname + location.search)
    return <Navigate to={`/login?next=${next}`} replace />
  }

  const navCls = ({ isActive }: { isActive: boolean }) =>
    `rounded-md px-2.5 py-1.5 text-sm font-medium ${isActive ? 'bg-slate-100 text-slate-900' : 'text-slate-600 hover:text-slate-900'}`

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-30 border-b border-slate-200 bg-white/95 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4">
          <Link to="/templates" className="text-sm font-semibold uppercase tracking-wide text-brand">
            {PRODUCT_NAME}
          </Link>
          <nav className="flex items-center gap-1">
            <NavLink to="/templates" className={navCls}>
              Agreements
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="hidden text-slate-500 sm:inline">
              {me.data.org.name} · {me.data.user.name}
            </span>
            <button
              type="button"
              className="rounded-md px-2 py-1 text-slate-600 hover:bg-slate-100"
              onClick={() => logout.mutate(undefined, { onSettled: () => navigate('/login') })}
            >
              Log out
            </button>
          </div>
        </div>
      </header>
      <div className="flex-1">
        <Outlet />
      </div>
    </div>
  )
}
