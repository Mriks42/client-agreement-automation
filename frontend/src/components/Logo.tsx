import { PRODUCT_NAME } from '../lib/product'

/** Kickoff's mark: a signed page (a white sheet with a folded corner and a check) on the
 * product blue. The same drawing is public/favicon.svg. Fixed colours: it's Kickoff's own mark,
 * not the firm's branding. */
export function LogoMark({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true" focusable="false">
      <rect width="32" height="32" rx="8" fill="#2f5bea" />
      <path d="M10.5 6.5h8.5l5.5 5.5v13.5a1.5 1.5 0 0 1-1.5 1.5h-12.5A1.5 1.5 0 0 1 9 25.5v-17.5a1.5 1.5 0 0 1 1.5-1.5z" fill="#fff" />
      <path d="M19 6.5v4.5a1 1 0 0 0 1 1h4.5z" fill="#bfcdfa" />
      <path d="M12.5 18.5l3 3 5.5-6.5" fill="none" stroke="#2f5bea" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/** The mark with the product name beside it. `markOnlyOnPhone` keeps a crowded phone header
 * to the mark alone. */
export function Wordmark({ size = 'md', markOnlyOnPhone = false }: { size?: 'sm' | 'md'; markOnlyOnPhone?: boolean }) {
  return (
    <span className="inline-flex items-center gap-2">
      <LogoMark className={size === 'sm' ? 'h-6 w-6' : 'h-7 w-7'} />
      <span
        className={`font-semibold tracking-tight text-slate-900 ${size === 'sm' ? 'text-base' : 'text-lg'} ${markOnlyOnPhone ? 'hidden sm:inline' : ''}`}
      >
        {PRODUCT_NAME}
      </span>
    </span>
  )
}
