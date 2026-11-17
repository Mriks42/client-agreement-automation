import { useState, type FormEvent, type ReactNode } from 'react'
import { Link, Navigate, useNavigate, useParams, useSearchParams } from 'react-router'
import { useInvitePreview, useJoin, useLogin, useMe, useSignup } from '../api/hooks'
import { Alert, Button, FullPageSpinner, TextInput } from '../components/ui'
import { PRODUCT_NAME } from '../lib/product'

function safeNext(raw: string | null): string {
  // Only same-site paths, so a crafted link can't bounce someone to another site after login.
  return raw && raw.startsWith('/') && !raw.startsWith('//') ? raw : '/files'
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
  if (me.data) return <Navigate to={signup.isIdle ? '/files' : '/templates/new'} replace />

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

/** /join/:token : accept a colleague's invitation and choose a password. The link is the credential. */
export function JoinPage() {
  const { token = '' } = useParams()
  const me = useMe()
  const invite = useInvitePreview(token)
  const join = useJoin()
  const navigate = useNavigate()
  const [name, setName] = useState<string | null>(null)
  const [password, setPassword] = useState('')

  if (me.isPending || invite.isPending) return <FullPageSpinner />
  const loginLink = (
    <Link className="font-medium text-brand" to="/login">
      Log in
    </Link>
  )
  if (invite.isError) {
    return (
      <AuthCard title="This invitation isn’t valid" footer={<>Already have a login? {loginLink}</>}>
        <p className="text-sm text-slate-700">{invite.error.message}</p>
      </AuthCard>
    )
  }
  const inv = invite.data
  if (me.data && !join.isSuccess) {
    return (
      <AuthCard title={`Join ${inv.org_name}`} footer={<Link className="font-medium text-brand" to="/files">Back to your files</Link>}>
        <p className="text-sm text-slate-700">
          You’re logged in as {me.data.user.email}. To accept this invitation for {inv.email}, log out first, then open
          the link again.
        </p>
      </AuthCard>
    )
  }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    join.mutate({ token, name: (name ?? inv.name ?? '').trim(), password }, { onSuccess: () => navigate('/files', { replace: true }) })
  }

  return (
    <AuthCard title={`Join ${inv.org_name}`} footer={<>Already have a login? {loginLink}</>}>
      <p className="mb-4 text-sm text-slate-600">
        {inv.invited_by_name} invited you. You’ll see the same agreements and files as the rest of the team.
      </p>
      <form onSubmit={submit} className="space-y-4">
        <TextInput label="Email" type="email" value={inv.email} readOnly disabled hint="Your login. It’s the address you were invited at." />
        <TextInput
          label="Your name"
          autoComplete="name"
          required
          maxLength={120}
          value={name ?? inv.name ?? ''}
          onChange={(e) => setName(e.target.value)}
        />
        <TextInput
          label="Choose a password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          hint="At least 8 characters."
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {join.isError && <Alert>{join.error.message}</Alert>}
        <Button type="submit" className="w-full" busy={join.isPending}>
          Join {inv.org_name}
        </Button>
      </form>
    </AuthCard>
  )
}
