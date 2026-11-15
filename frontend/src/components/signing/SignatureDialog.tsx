import { useEffect, useRef, useState } from 'react'
import SignaturePad from 'signature_pad'
import type { Mark } from '../../api/types'
import { INK, initialsOf, TYPED_STYLE } from '../../lib/signing'
import { Button, Dialog } from '../ui'

type Props = {
  open: boolean
  what: 'signature' | 'initials'
  signerName: string
  current: Mark | null
  onAdopt: (mark: Mark) => void
  onClose: () => void
}

export function SignatureDialog({ open, what, signerName, current, onAdopt, onClose }: Props) {
  const [tab, setTab] = useState<'typed' | 'drawn'>(current?.kind ?? 'typed')
  const [text, setText] = useState(
    current?.kind === 'typed' ? current.text : what === 'initials' ? initialsOf(signerName) : signerName,
  )
  const [empty, setEmpty] = useState(true)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const padRef = useRef<SignaturePad | null>(null)

  useEffect(() => {
    if (!open || tab !== 'drawn' || !canvasRef.current) return
    const canvas = canvasRef.current
    const pad = new SignaturePad(canvas, { penColor: INK, minWidth: 0.9, maxWidth: 2.6, backgroundColor: 'rgba(0,0,0,0)' })
    padRef.current = pad
    const fit = () => {
      const ratio = Math.max(window.devicePixelRatio || 1, 1)
      const data = pad.toData()
      canvas.width = canvas.offsetWidth * ratio
      canvas.height = canvas.offsetHeight * ratio
      canvas.getContext('2d')!.scale(ratio, ratio)
      pad.clear()
      pad.fromData(data)
    }
    fit()
    const onEnd = () => setEmpty(pad.isEmpty())
    pad.addEventListener('endStroke', onEnd)
    window.addEventListener('resize', fit)
    return () => {
      pad.removeEventListener('endStroke', onEnd)
      window.removeEventListener('resize', fit)
      pad.off()
      padRef.current = null
    }
  }, [open, tab])

  const label = what === 'signature' ? 'signature' : 'initials'
  const typedOk = text.trim().length > 0
  const adopt = () => {
    if (tab === 'typed') {
      onAdopt({ kind: 'typed', text: text.trim() })
    } else {
      const canvas = canvasRef.current
      if (!canvas || !padRef.current || padRef.current.isEmpty()) return
      onAdopt({ kind: 'drawn', png: trimmed(canvas) })
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={`Your ${label}`} wide>
      <div role="tablist" className="inline-flex rounded-lg bg-slate-100 p-1 text-sm">
        {(['typed', 'drawn'] as const).map((t) => (
          <button
            key={t}
            role="tab"
            type="button"
            aria-selected={tab === t}
            onClick={() => setTab(t)}
            className={`rounded-md px-3 py-1.5 font-medium ${tab === t ? 'bg-white shadow-sm' : 'text-slate-600'}`}
          >
            {t === 'typed' ? 'Type it' : 'Draw it'}
          </button>
        ))}
      </div>

      {tab === 'typed' ? (
        <div className="mt-4">
          <label htmlFor="typed-mark" className="block text-sm font-medium text-slate-700">
            {what === 'signature' ? 'Your full name' : 'Your initials'}
          </label>
          <input
            id="typed-mark"
            value={text}
            maxLength={what === 'signature' ? 120 : 6}
            autoComplete="off"
            onChange={(e) => setText(e.target.value)}
            className="mt-1 block w-full rounded-lg border border-slate-300 px-3 py-2.5 text-base focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20"
          />
          <div className="mt-3 flex h-24 items-center overflow-hidden rounded-lg border border-dashed border-slate-300 bg-slate-50 px-4">
            <span className="truncate text-4xl" style={TYPED_STYLE}>
              {text.trim() || ' '}
            </span>
          </div>
        </div>
      ) : (
        <div className="mt-4">
          <p className="text-sm text-slate-600">Draw with your finger, mouse or trackpad.</p>
          <div className="relative mt-2">
            <canvas
              ref={canvasRef}
              aria-label={`Draw your ${label}`}
              className="block h-44 w-full touch-none rounded-lg border border-dashed border-slate-300 bg-slate-50"
            />
            <span className="pointer-events-none absolute right-4 bottom-8 left-4 border-b border-slate-300" aria-hidden />
          </div>
          <button
            type="button"
            className="mt-1 text-sm text-slate-500 hover:text-slate-800"
            onClick={() => {
              padRef.current?.clear()
              setEmpty(true)
            }}
          >
            Clear
          </button>
        </div>
      )}

      <p className="mt-4 text-xs text-slate-500">
        By adopting this {label}, you agree it is the electronic equivalent of your handwritten {label} on this
        agreement.
      </p>
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button onClick={adopt} disabled={tab === 'typed' ? !typedOk : empty}>
          Adopt {label}
        </Button>
      </div>
    </Dialog>
  )
}

/** The drawing cropped to its ink (so it fills the signature box), at most 1200 px wide. */
function trimmed(canvas: HTMLCanvasElement): string {
  const ctx = canvas.getContext('2d')!
  const { width, height } = canvas
  const data = ctx.getImageData(0, 0, width, height).data
  let x0 = width
  let y0 = height
  let x1 = -1
  let y1 = -1
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      if (data[(y * width + x) * 4 + 3] > 8) {
        if (x < x0) x0 = x
        if (x > x1) x1 = x
        if (y < y0) y0 = y
        if (y > y1) y1 = y
      }
    }
  }
  if (x1 < 0) return canvas.toDataURL('image/png')
  const pad = 6
  x0 = Math.max(0, x0 - pad)
  y0 = Math.max(0, y0 - pad)
  x1 = Math.min(width - 1, x1 + pad)
  y1 = Math.min(height - 1, y1 + pad)
  const w = x1 - x0 + 1
  const h = y1 - y0 + 1
  const scale = Math.min(1, 1200 / w)
  const out = document.createElement('canvas')
  out.width = Math.round(w * scale)
  out.height = Math.round(h * scale)
  out.getContext('2d')!.drawImage(canvas, x0, y0, w, h, 0, 0, out.width, out.height)
  return out.toDataURL('image/png')
}
