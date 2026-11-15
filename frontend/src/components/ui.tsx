import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode } from 'react'
import type { TemplateStatus } from '../api/types'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost'
  busy?: boolean
}

const VARIANTS = {
  primary: 'bg-brand text-brand-ink hover:brightness-110 disabled:opacity-50',
  secondary:
    'border border-slate-300 bg-white text-slate-800 hover:bg-slate-50 disabled:opacity-50',
  danger: 'border border-red-200 bg-white text-red-700 hover:bg-red-50 disabled:opacity-50',
  ghost: 'text-slate-600 hover:bg-slate-100 disabled:opacity-50',
}

export function Button({ variant = 'primary', busy, className = '', children, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-3.5 py-2 text-sm font-medium transition focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand disabled:cursor-not-allowed ${VARIANTS[variant]} ${className}`}
    >
      {busy && <Spinner className="h-4 w-4" />}
      {children}
    </button>
  )
}

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className}`} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path d="M22 12a10 10 0 0 0-10-10" stroke="currentColor" strokeWidth="4" strokeLinecap="round" />
    </svg>
  )
}

type InputProps = InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }

export function TextInput({ label, hint, id, className = '', ...rest }: InputProps) {
  const inputId = id ?? `in-${label.replace(/\W+/g, '-').toLowerCase()}`
  return (
    <div className={className}>
      <label htmlFor={inputId} className="block text-sm font-medium text-slate-700">
        {label}
      </label>
      <input
        id={inputId}
        {...rest}
        className="mt-1 block w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm shadow-xs focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
      />
      {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
    </div>
  )
}

export function Alert({
  tone = 'error',
  title,
  children,
  className = '',
}: {
  tone?: 'error' | 'warning' | 'info' | 'success'
  title?: ReactNode
  children?: ReactNode
  className?: string
}) {
  const tones = {
    error: 'border-red-200 bg-red-50 text-red-800',
    warning: 'border-amber-200 bg-amber-50 text-amber-900',
    info: 'border-sky-200 bg-sky-50 text-sky-900',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  }
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={`rounded-lg border p-3 text-sm ${tones[tone]} ${className}`}>
      {title && <p className="font-medium">{title}</p>}
      {children && <div className={title ? 'mt-1' : ''}>{children}</div>}
    </div>
  )
}

const STATUS: Record<TemplateStatus, { label: string; cls: string }> = {
  analyzing: { label: 'Analyzing', cls: 'bg-sky-100 text-sky-800' },
  analysis_failed: { label: 'Needs setup', cls: 'bg-red-100 text-red-800' },
  needs_review: { label: 'Needs review', cls: 'bg-amber-100 text-amber-900' },
  active: { label: 'Approved', cls: 'bg-emerald-100 text-emerald-800' },
}

export function StatusBadge({ status }: { status: TemplateStatus }) {
  const s = STATUS[status]
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${s.cls}`}>
      {s.label}
    </span>
  )
}

export function FullPageSpinner() {
  return (
    <div className="flex min-h-[50vh] items-center justify-center text-slate-400">
      <Spinner className="h-6 w-6" />
    </div>
  )
}
