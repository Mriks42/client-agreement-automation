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
