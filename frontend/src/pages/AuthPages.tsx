import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useSearchParams } from 'react-router'
import { useLogin, useMe, useSignup } from '../api/hooks'
import { Alert, Button, FullPageSpinner, TextInput } from '../components/ui'
import { PRODUCT_NAME } from '../lib/product'

function safeNext(raw: string | null): string {
  // Only same-site paths, so a crafted link can't bounce someone to another site after login.
  return raw && raw.startsWith('/') && !raw.startsWith('//') ? raw : '/templates'
}

function AuthCard({ title, children, footer }: { title: string; children: ReactNode; footer: ReactNode }) {
  return (
    <main className="mx-auto flex min-h-full max-w-sm flex-col justify-center px-4 py-12">
      <Link to="/" className="text-sm font-semibold uppercase tracking-wide text-brand">
        {PRODUCT_NAME}
      </Link>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight">{title}</h1>
      <div className="mt-6 rounded-xl border border-slate-200 bg-white p-5 shadow-xs">{children}</div>
      <p className="mt-4 text-center text-sm text-slate-600">{footer}</p>
    </main>
  )
}

export function LoginPage() {
  const me = useMe()
  const login = useLogin()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')

  if (me.isPending) return <FullPageSpinner />
  if (me.data) return <Navigate to={next} replace />

  const submit = (e: FormEvent) => {
    e.preventDefault()
    login.mutate({ email, password })
  }

  return (
    <AuthCard
      title="Log in"
      footer={
        <>
          New here?{' '}
          <Link className="font-medium text-brand" to="/signup">
            Create your company’s account
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <TextInput label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        <TextInput label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        {login.isError && <Alert>{login.error.message}</Alert>}
        <Button type="submit" className="w-full" busy={login.isPending}>
          Log in
        </Button>
      </form>
    </AuthCard>
  )
}

export function SignupPage() {
  const me = useMe()
  const signup = useSignup()
  const [form, setForm] = useState({ org_name: '', name: '', email: '', password: '' })
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm((f) => ({ ...f, [k]: e.target.value }))

  if (me.isPending) return <FullPageSpinner />
  // One redirect, decided here: a new account goes straight to uploading its agreement.
  if (me.data) return <Navigate to={signup.isIdle ? '/templates' : '/templates/new'} replace />

  const submit = (e: FormEvent) => {
    e.preventDefault()
    signup.mutate(form)
  }

  return (
    <AuthCard
      title="Create your company’s account"
      footer={
        <>
          Already have an account?{' '}
          <Link className="font-medium text-brand" to="/login">
            Log in
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="space-y-4">
        <TextInput
          label="Company name"
          hint="Your clients see this name on emails and the signing page."
          autoComplete="organization"
          required
          maxLength={120}
          value={form.org_name}
          onChange={set('org_name')}
        />
        <TextInput label="Your name" autoComplete="name" required maxLength={120} value={form.name} onChange={set('name')} />
        <TextInput label="Work email" type="email" autoComplete="email" required value={form.email} onChange={set('email')} />
        <TextInput
          label="Password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          hint="At least 8 characters."
          value={form.password}
          onChange={set('password')}
        />
        {signup.isError && <Alert>{signup.error.message}</Alert>}
        <Button type="submit" className="w-full" busy={signup.isPending}>
          Create account
        </Button>
      </form>
    </AuthCard>
  )
}
