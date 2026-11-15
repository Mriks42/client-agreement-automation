import { useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import type { Field, FieldType, Rect, Role } from '../../api/types'
import { clampRect, colorFor, DEFAULT_SIZE, fillerLabel, TYPE_LABELS } from '../../lib/fields'

type Corner = 'nw' | 'ne' | 'sw' | 'se'
type Drag =
  | { kind: 'move' | Corner; id: string; startX: number; startY: number; orig: Rect; rect: Rect; moved: boolean }
  | { kind: 'draw'; startX: number; startY: number; rect: Rect }

type Props = {
  templateId: string
  pdfRev: string
  page: number
  size: { width: number; height: number }
  fields: Field[]
  roles: Role[]
  selectedId: string | null
  addType: FieldType | null // set while the reviewer is placing a new field
  onSelect: (id: string | null) => void
  onMove: (id: string, rect: Rect) => void
  onAdd: (page: number, rect: Rect) => void
}

const pct = (n: number) => `${n * 100}%`

/** One page: the rendered image, with fields overlaid. Field positions are percentages of the
 * page size in PDF points, so they scale with the image at any width. */
export function PageCanvas(props: Props) {
  const { templateId, pdfRev, page, size, fields, roles, selectedId, addType } = props
  const ref = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<Drag | null>(null)

  const toPts = (clientX: number, clientY: number) => {
    const b = ref.current!.getBoundingClientRect()
    return { x: ((clientX - b.left) * size.width) / b.width, y: ((clientY - b.top) * size.height) / b.height }
  }

  const startFieldDrag = (e: ReactPointerEvent, f: Field, kind: 'move' | Corner) => {
    if (addType || e.button !== 0) return
    e.stopPropagation()
    e.preventDefault()
    ref.current?.setPointerCapture(e.pointerId)
    props.onSelect(f.id)
    const p = toPts(e.clientX, e.clientY)
    setDrag({ kind, id: f.id, startX: p.x, startY: p.y, orig: f.rect, rect: f.rect, moved: false })
  }

  const onBackgroundDown = (e: ReactPointerEvent) => {
    if (e.button !== 0) return
    if (!addType) {
      props.onSelect(null)
      return
    }
    e.preventDefault()
    ref.current?.setPointerCapture(e.pointerId)
    const p = toPts(e.clientX, e.clientY)
    setDrag({ kind: 'draw', startX: p.x, startY: p.y, rect: { x0: p.x, y0: p.y, x1: p.x, y1: p.y } })
  }

  const onMoveEvt = (e: ReactPointerEvent) => {
    if (!drag) return
    const p = toPts(e.clientX, e.clientY)
    if (drag.kind === 'draw') {
      setDrag({ ...drag, rect: { x0: drag.startX, y0: drag.startY, x1: p.x, y1: p.y } })
      return
    }
    const dx = p.x - drag.startX
    const dy = p.y - drag.startY
    const o = drag.orig
    let r: Rect
    switch (drag.kind) {
      case 'move':
        r = { x0: o.x0 + dx, y0: o.y0 + dy, x1: o.x1 + dx, y1: o.y1 + dy }
        break
      case 'nw':
        r = { ...o, x0: o.x0 + dx, y0: o.y0 + dy }
        break
      case 'ne':
        r = { ...o, x1: o.x1 + dx, y0: o.y0 + dy }
        break
      case 'sw':
        r = { ...o, x0: o.x0 + dx, y1: o.y1 + dy }
        break
      case 'se':
        r = { ...o, x1: o.x1 + dx, y1: o.y1 + dy }
        break
    }
    setDrag({ ...drag, rect: clampRect(r, size), moved: drag.moved || Math.abs(dx) + Math.abs(dy) > 1 })
  }

  const onUp = (e: ReactPointerEvent) => {
    if (!drag) return
    ref.current?.releasePointerCapture(e.pointerId)
    if (drag.kind === 'draw') {
      const r = drag.rect
      const tiny = Math.abs(r.x1 - r.x0) < 6 || Math.abs(r.y1 - r.y0) < 4
      if (addType) {
        const [w, h] = DEFAULT_SIZE[addType]
        // A click places a default-sized field with its bottom-left at the click point
        // (where text sits on a line); a drag uses the drawn box.
        const placed = tiny ? { x0: r.x0, y0: r.y0 - h, x1: r.x0 + w, y1: r.y0 } : r
        props.onAdd(page, clampRect(placed, size))
      }
    } else if (drag.moved) {
      props.onMove(drag.id, drag.rect)
    }
    setDrag(null)
  }

  return (
    <div className="relative bg-white shadow-sm ring-1 ring-slate-200" style={{ aspectRatio: `${size.width} / ${size.height}` }}>
      <img
        src={`/api/templates/${templateId}/pages/${page}.png?v=${pdfRev}`}
        alt={`Page ${page}`}
        loading="lazy"
        draggable={false}
        className="block h-full w-full select-none"
      />
      <div
        ref={ref}
        className={`absolute inset-0 ${addType ? 'cursor-crosshair touch-none' : ''}`}
        onPointerDown={onBackgroundDown}
        onPointerMove={onMoveEvt}
        onPointerUp={onUp}
        onPointerCancel={() => setDrag(null)}
      >
        {fields.map((f) => {
          const r = drag && drag.kind !== 'draw' && drag.id === f.id ? drag.rect : f.rect
          const color = colorFor(f.filled_by, roles)
          const selected = f.id === selectedId
          return (
            <div
              key={f.id}
              data-field={f.id}
              title={`${f.label} · ${TYPE_LABELS[f.type]} · ${fillerLabel(f.filled_by, roles)}`}
              onPointerDown={(e) => startFieldDrag(e, f, 'move')}
              className={`absolute touch-none overflow-visible border-[1.5px] ${addType ? 'pointer-events-none' : 'cursor-move'} ${selected ? 'z-20' : 'z-10'}`}
              style={{
                left: pct(r.x0 / size.width),
                top: pct(r.y0 / size.height),
                width: pct((r.x1 - r.x0) / size.width),
                height: pct((r.y1 - r.y0) / size.height),
                borderColor: color,
                backgroundColor: `${color}${selected ? '38' : '1f'}`,
                outline: selected ? `2px solid ${color}` : undefined,
                outlineOffset: 1,
              }}
            >
              {/* Only the selected field shows its label, above the box: labels on every field
                  would cover the document text the reviewer is checking. */}
              {selected && (
                <span
                  className="pointer-events-none absolute bottom-full left-0 mb-1 max-w-64 truncate rounded px-1.5 py-0.5 text-[11px] font-medium whitespace-nowrap text-white shadow-sm"
                  style={{ background: color }}
                >
                  {f.label} · {TYPE_LABELS[f.type]}
                </span>
              )}
              {selected &&
                (['nw', 'ne', 'sw', 'se'] as Corner[]).map((c) => (
                  <span
                    key={c}
                    onPointerDown={(e) => startFieldDrag(e, f, c)}
                    className="absolute h-2.5 w-2.5 rounded-full border-2 bg-white"
                    style={{
                      borderColor: color,
                      left: c.endsWith('w') ? -5 : undefined,
                      right: c.endsWith('e') ? -5 : undefined,
                      top: c.startsWith('n') ? -5 : undefined,
                      bottom: c.startsWith('s') ? -5 : undefined,
                      cursor: `${c}-resize`,
                    }}
                  />
                ))}
            </div>
          )
        })}
        {drag?.kind === 'draw' && (
          <div
            className="pointer-events-none absolute border-2 border-dashed border-brand bg-brand/10"
            style={{
              left: pct(Math.min(drag.rect.x0, drag.rect.x1) / size.width),
              top: pct(Math.min(drag.rect.y0, drag.rect.y1) / size.height),
              width: pct(Math.abs(drag.rect.x1 - drag.rect.x0) / size.width),
              height: pct(Math.abs(drag.rect.y1 - drag.rect.y0) / size.height),
            }}
          />
        )}
      </div>
    </div>
  )
}
