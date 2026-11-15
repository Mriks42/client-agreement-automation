import type { Field, FieldType, Role } from '../../api/types'
import {
  colorFor,
  FIELD_TYPES,
  fillerLabel,
  newRoleKey,
  ordinal,
  SENDER,
  SIGNER_ONLY,
  TYPE_LABELS,
} from '../../lib/fields'
import { Button } from '../ui'

const SOURCE: Record<Field['source'], string> = {
  ai: 'Proposed by AI',
  heuristic: 'Proposed by keyword rules',
  manual: 'Added by hand',
}

const selectCls =
  'mt-1 block w-full rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-sm focus:border-brand focus:outline-none focus:ring-2 focus:ring-brand/20'
const inputCls = selectCls

export function FillerSelect({
  value,
  roles,
  type,
  onChange,
  id,
}: {
  value: string
  roles: Role[]
  type: FieldType
  onChange: (v: string) => void
  id?: string
}) {
  return (
    <select id={id} className={selectCls} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value={SENDER} disabled={SIGNER_ONLY.has(type)}>
        You, on each new file
      </option>
      {[...roles]
        .sort((a, b) => a.order - b.order)
        .map((r) => (
          <option key={r.key} value={r.key}>
            {r.label} (signer)
          </option>
        ))}
    </select>
  )
}

export function FieldPanel({
  field,
  roles,
  sharedWith,
  onChange,
  onDelete,
}: {
  field: Field
  roles: Role[]
  sharedWith: number
  onChange: (patch: Partial<Field>) => void
  onDelete: () => void
}) {
  const setType = (type: FieldType) => {
    const patch: Partial<Field> = { type }
    // A signature can't be filled by the sender: hand it to the first signer.
    if (SIGNER_ONLY.has(type) && field.filled_by === SENDER) {
      const first = [...roles].sort((a, b) => a.order - b.order)[0]
      if (!first) return
      patch.filled_by = first.key
    }
    onChange(patch)
  }
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <span className="h-3 w-3 rounded-sm" style={{ background: colorFor(field.filled_by, roles) }} aria-hidden />
        <h3 className="text-sm font-semibold">Selected field</h3>
        <span className="ml-auto text-xs text-slate-400">Page {field.page}</span>
      </div>
      <div>
        <label htmlFor="fld-label" className="text-xs font-medium text-slate-600">
          Label
        </label>
        <input
          id="fld-label"
          className={inputCls}
          value={field.label}
          maxLength={200}
          onChange={(e) => onChange({ label: e.target.value })}
        />
      </div>
      <div className="grid grid-cols-2 gap-2">
        <div>
          <label htmlFor="fld-type" className="text-xs font-medium text-slate-600">
            Type
          </label>
          <select id="fld-type" className={selectCls} value={field.type} onChange={(e) => setType(e.target.value as FieldType)}>
            {FIELD_TYPES.map((t) => (
              <option key={t} value={t}>
                {TYPE_LABELS[t]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="fld-filler" className="text-xs font-medium text-slate-600">
            Filled in by
          </label>
          <FillerSelect id="fld-filler" value={field.filled_by} roles={roles} type={field.type} onChange={(v) => onChange({ filled_by: v })} />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={field.required} onChange={(e) => onChange({ required: e.target.checked })} />
        Required
      </label>
      <p className="text-xs text-slate-500">
        {SOURCE[field.source]}.
        {sharedWith > 0 &&
          ` Shares one value with ${sharedWith} other field${sharedWith === 1 ? '' : 's'}, so it’s typed once.`}
      </p>
      <div className="flex items-center gap-2">
        <Button variant="danger" onClick={onDelete}>
          Delete field
        </Button>
        <span className="text-xs text-slate-400">or press Delete</span>
      </div>
    </div>
  )
}

export function RolesPanel({
  roles,
  fields,
  onChange,
}: {
  roles: Role[]
  fields: Field[]
  onChange: (roles: Role[], fields?: Field[]) => void
}) {
  const sorted = [...roles].sort((a, b) => a.order - b.order)
  const renumber = (list: Role[]) => list.map((r, i) => ({ ...r, order: i + 1 }))

  const move = (i: number, dir: -1 | 1) => {
    const list = [...sorted]
    const j = i + dir
    if (j < 0 || j >= list.length) return
    ;[list[i], list[j]] = [list[j], list[i]]
    onChange(renumber(list))
  }
  const update = (key: string, patch: Partial<Role>) =>
    onChange(roles.map((r) => (r.key === key ? { ...r, ...patch } : r)))
  const remove = (role: Role) => {
    const owned = fields.filter((f) => f.filled_by === role.key)
    if (
      owned.length &&
      !window.confirm(`Remove ${role.label} and the ${owned.length} field(s) they fill in?`)
    )
      return
    onChange(
      renumber(sorted.filter((r) => r.key !== role.key)),
      fields.filter((f) => f.filled_by !== role.key),
    )
  }
  const add = () =>
    onChange([
      ...sorted,
      { key: newRoleKey(roles), label: `Signer ${roles.length + 1}`, kind: 'other', order: roles.length + 1 },
    ])

  return (
    <div>
      <h3 className="text-sm font-semibold">Signers, in signing order</h3>
      <p className="mt-0.5 text-xs text-slate-500">Each signs after the one above has finished.</p>
      <ul className="mt-2 space-y-2">
        {sorted.map((r, i) => {
          const count = fields.filter((f) => f.filled_by === r.key).length
          return (
            <li key={r.key} className="rounded-lg border border-slate-200 p-2">
              <div className="flex items-center gap-2">
                <span className="h-3 w-3 shrink-0 rounded-sm" style={{ background: colorFor(r.key, roles) }} aria-hidden />
                <span className="text-xs font-medium text-slate-500">{ordinal(i + 1)}</span>
                <input
                  aria-label={`Name of the ${ordinal(i + 1)} signer`}
                  className="min-w-0 flex-1 rounded border border-transparent px-1 py-0.5 text-sm hover:border-slate-200 focus:border-brand focus:outline-none"
                  value={r.label}
                  maxLength={80}
                  onChange={(e) => update(r.key, { label: e.target.value })}
                />
              </div>
              <div className="mt-1.5 flex items-center gap-1 pl-5 text-xs text-slate-500">
                <select
                  aria-label="Who this signer is"
                  className="rounded border border-slate-200 bg-white px-1 py-0.5"
                  value={r.kind}
                  onChange={(e) => update(r.key, { kind: e.target.value as Role['kind'] })}
                >
                  <option value="client">Client</option>
                  <option value="firm">Your firm</option>
                  <option value="other">Other</option>
                </select>
                <span className="ml-1">{count} field{count === 1 ? '' : 's'}</span>
                <span className="ml-auto flex">
                  <button type="button" className="rounded px-1.5 hover:bg-slate-100 disabled:opacity-30" disabled={i === 0} onClick={() => move(i, -1)} aria-label="Sign earlier">
                    ↑
                  </button>
                  <button type="button" className="rounded px-1.5 hover:bg-slate-100 disabled:opacity-30" disabled={i === sorted.length - 1} onClick={() => move(i, 1)} aria-label="Sign later">
                    ↓
                  </button>
                  <button type="button" className="rounded px-1.5 text-red-600 hover:bg-red-50" onClick={() => remove(r)} aria-label={`Remove ${r.label}`}>
                    ✕
                  </button>
                </span>
              </div>
            </li>
          )
        })}
      </ul>
      {roles.length < 10 && (
        <button type="button" className="mt-2 text-sm font-medium text-brand" onClick={add}>
          + Add a signer
        </button>
      )}
    </div>
  )
}

export function FieldList({
  fields,
  roles,
  selectedId,
  onSelect,
}: {
  fields: Field[]
  roles: Role[]
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const fillers = [SENDER, ...[...roles].sort((a, b) => a.order - b.order).map((r) => r.key)]
  return (
    <div className="space-y-3">
      {fillers.map((who) => {
        const list = fields
          .filter((f) => f.filled_by === who)
          .sort((a, b) => a.page - b.page || a.rect.y0 - b.rect.y0 || a.rect.x0 - b.rect.x0)
        if (!list.length) return null
        return (
          <div key={who}>
            <p className="flex items-center gap-1.5 text-xs font-medium text-slate-600">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: colorFor(who, roles) }} aria-hidden />
              {fillerLabel(who, roles)} · {list.length}
            </p>
            <ul className="mt-1">
              {list.map((f) => (
                <li key={f.id}>
                  <button
                    type="button"
                    onClick={() => onSelect(f.id)}
                    className={`flex w-full items-baseline gap-2 rounded px-1.5 py-0.5 text-left text-xs hover:bg-slate-100 ${f.id === selectedId ? 'bg-slate-100 font-medium' : ''}`}
                  >
                    <span className="min-w-0 flex-1 truncate">{f.label}</span>
                    <span className="shrink-0 text-slate-400">
                      {TYPE_LABELS[f.type]} · p{f.page}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )
      })}
    </div>
  )
}
