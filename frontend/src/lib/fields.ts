import type { Field, FieldType, Rect, Role } from '../api/types'

export const SENDER = 'sender'

export const TYPE_LABELS: Record<FieldType, string> = {
  signature: 'Signature',
  initials: 'Initials',
  date_signed: 'Date signed',
  name: 'Printed name',
  text: 'Text',
  date: 'Date',
  checkbox: 'Checkbox',
}

export const FIELD_TYPES = Object.keys(TYPE_LABELS) as FieldType[]

/** Only a signer can fill these (the sender can't sign or date on someone's behalf). */
export const SIGNER_ONLY: ReadonlySet<FieldType> = new Set(['signature', 'initials', 'date_signed'])

/** Default size in PDF points for a field placed with a single click. */
export const DEFAULT_SIZE: Record<FieldType, [number, number]> = {
  signature: [180, 30],
  initials: [60, 22],
  date_signed: [110, 16],
  name: [160, 16],
  text: [160, 16],
  date: [110, 16],
  checkbox: [12, 12],
}

const ROLE_COLORS = ['#2563eb', '#059669', '#7c3aed', '#db2777', '#0891b2', '#65a30d']
// Dark orange: stays visible on documents that highlight their own blanks in yellow.
const SENDER_COLOR = '#c2410c'

export function colorFor(filledBy: string, roles: Role[]): string {
  if (filledBy === SENDER) return SENDER_COLOR
  const i = [...roles].sort((a, b) => a.order - b.order).findIndex((r) => r.key === filledBy)
  return i < 0 ? '#64748b' : ROLE_COLORS[i % ROLE_COLORS.length]
}

export function fillerLabel(filledBy: string, roles: Role[]): string {
  if (filledBy === SENDER) return 'You, per file'
  return roles.find((r) => r.key === filledBy)?.label ?? 'Unknown signer'
}

/** Mirrors the server's approval rule (services/templates.approval_problems). */
export function approvalProblems(roles: Role[], fields: Field[]): string[] {
  if (roles.length === 0) return ['Add at least one signer.']
  const signs = new Set(fields.filter((f) => f.type === 'signature').map((f) => f.filled_by))
  return [...roles]
    .sort((a, b) => a.order - b.order)
    .filter((r) => !signs.has(r.key))
    .map((r) => `${r.label} has nowhere to sign. Add a signature field for them.`)
}

export function clampRect(r: Rect, page: { width: number; height: number }, min = 4): Rect {
  let { x0, y0, x1, y1 } = r
  if (x1 < x0) [x0, x1] = [x1, x0]
  if (y1 < y0) [y0, y1] = [y1, y0]
  const w = Math.min(Math.max(x1 - x0, min), page.width)
  const h = Math.min(Math.max(y1 - y0, min), page.height)
  x0 = Math.min(Math.max(0, x0), page.width - w)
  y0 = Math.min(Math.max(0, y0), page.height - h)
  return { x0: round(x0), y0: round(y0), x1: round(x0 + w), y1: round(y0 + h) }
}

function round(n: number): number {
  return Math.round(n * 10) / 10
}

export function newFieldId(): string {
  const rand = crypto.getRandomValues(new Uint32Array(2))
  return `fld_m_${rand[0].toString(36)}${rand[1].toString(36)}`
}

export function newRoleKey(roles: Role[]): string {
  for (let i = roles.length + 1; ; i++) {
    const key = `signer_${i}`
    if (!roles.some((r) => r.key === key)) return key
  }
}

export function ordinal(n: number): string {
  const s = ['th', 'st', 'nd', 'rd']
  const v = n % 100
  return n + (s[(v - 20) % 10] || s[v] || s[0])
}
