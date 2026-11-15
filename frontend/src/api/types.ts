// Shapes returned by the API. Coordinates are PDF points, top-left origin, 1-based pages.

export type Me = {
  user: { id: string; name: string; email: string }
  org: { id: string; name: string }
}

export type FieldType =
  | 'signature'
  | 'initials'
  | 'date_signed'
  | 'name'
  | 'text'
  | 'date'
  | 'checkbox'

export type Rect = { x0: number; y0: number; x1: number; y1: number }

export type Field = {
  id: string
  key: string
  label: string
  type: FieldType
  filled_by: string // 'sender' or a role key
  page: number
  rect: Rect
  required: boolean
  source: 'ai' | 'heuristic' | 'manual'
  slot_id?: string | null
}

export type Role = {
  key: string
  label: string
  kind: 'firm' | 'client' | 'other'
  order: number
}

export type TemplateStatus = 'analyzing' | 'analysis_failed' | 'needs_review' | 'active'

export type TemplateSummary = {
  id: string
  name: string
  status: TemplateStatus
  version: number
  page_count: number
  field_count: number
  created_at: string
  updated_at: string
  approved_at: string | null
}

export type Template = TemplateSummary & {
  revision: number
  source_format: 'pdf' | 'docx'
  filename: string
  pdf_rev: string
  pages: { width: number; height: number }[]
  roles: Role[]
  fields: Field[]
  warnings: string[]
  detector: 'ai' | 'heuristic' | null
  fallback_reason: string | null
  appended_signature_page: boolean
  error: string | null
  problems: string[]
  analysis_started_at: string | null
}
