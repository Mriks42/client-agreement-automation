// Same dark blue as signer marks in the PDF (pdf_engine._INK), and the same italic serif the PDF
// uses for a typed signature, so what the signer adopts is what the document shows.
export const INK = '#0f2468'
export const TYPED_STYLE = { fontFamily: '"Times New Roman", Times, serif', fontStyle: 'italic' as const, color: INK }

export function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => w[0]!.toUpperCase())
    .join('')
    .slice(0, 4)
}

/** Field labels are written for the firm's reviewer ("Client signer title", "Provider's printed
 * name"). The signer only sees their own fields, so drop the party and say it plainly ("Title"). */
export function signerLabel(label: string, roleLabel: string): string {
  let s = label.trim().replace(/^.{0,60}?\b(signer|signatory|representative)('s)?\s+/i, '')
  const role = roleLabel.trim().toLowerCase()
  if (role && s.toLowerCase().startsWith(`${role} `)) s = s.slice(role.length + 1)
  else if (role && s.toLowerCase().startsWith(`${role}'s `)) s = s.slice(role.length + 3)
  s = s.trim()
  return s ? s[0]!.toUpperCase() + s.slice(1) : label
}

/** A hint under the input for fields people often hesitate over. */
export function signerHint(label: string): string | null {
  if (/\btitle\b/i.test(label)) return 'Your job title, for example Director or Owner.'
  if (/\b(company|entity|organi[sz]ation|business)\b/i.test(label)) return 'The legal name of your company, if you sign for one.'
  return null
}
