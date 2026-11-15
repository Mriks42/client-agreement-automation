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
  sender_inputs: SenderInput[]
  analysis_started_at: string | null
}

/** One input on the new-file form: fields that share a key share it. */
export type SenderInput = {
  key: string
  label: string
  type: FieldType
  required: boolean
  multiline: boolean
  page: number
  count: number
}

export type FileStatus =
  | 'draft'
  | 'awaiting_approval'
  | 'sent'
  | 'partially_signed'
  | 'completed'
  | 'declined'
  | 'expired'
  | 'voided'

export type RecipientStatus = 'pending' | 'active' | 'signed' | 'declined'

export type Recipient = {
  id: string
  role: string
  role_label: string
  order: number
  name: string
  email: string
  status: RecipientStatus
  activated_at: string | null
  viewed_at: string | null
  consented_at: string | null
  signed_at: string | null
  declined_at: string | null
  decline_reason: string | null
  emails_sent: number | null
  resends: number | null
  last_email: {
    status: 'sent' | 'failed'
    at: string
    to: string
    reason: string
    error?: string | null
  } | null
}

export type FileEvent = {
  type: string
  at: string
  actor: { kind: 'user' | 'recipient' | 'system'; name: string | null }
  recipient: { name: string | null; email: string | null; role_label: string | null } | null
  ip: string | null
  data: Record<string, unknown>
}

export type FileSummary = {
  id: string
  title: string
  status: FileStatus
  agreement_name: string
  template_id: string
  signer_count: number
  signed_count: number
  waiting_for: { name: string; role_label: string } | null
  created_at: string
  updated_at: string
  sent_at: string | null
  completed_at: string | null
}

export type FileDetail = FileSummary & {
  revision: number
  template: {
    id: string
    name: string
    version: number
    page_count: number
    pages: { width: number; height: number }[]
    roles: Role[]
  }
  sender_inputs: SenderInput[]
  values: Record<string, string | boolean>
  recipients: Recipient[]
  missing: string[]
  page_rev: string
  prepared_sha256: string | null
  expires_at: string | null
  declined_at: string | null
  voided_at: string | null
  void_reason: string | null
  finalized_at: string | null
  events: FileEvent[]
}

export type FileInput = {
  title?: string
  values: Record<string, string | boolean>
  recipients: { role: string; name: string; email: string }[]
}

// ---- signing (public) -------------------------------------------------------------------------
export type SignState =
  | 'ready'
  | 'signed'
  | 'completed'
  | 'declined'
  | 'declined_by_other'
  | 'voided'
  | 'expired'
  | 'unavailable'

export type SignField = Pick<Field, 'id' | 'key' | 'label' | 'type' | 'page' | 'rect' | 'required'>

export type SignView = {
  state: SignState
  org_name: string
  agreement_name: string
  file_title: string
  signer: { name: string; email: string; role_label: string }
  sender: { name: string; email: string }
  consented: boolean
  disclosure_version: string
  pages: { width: number; height: number }[]
  page_rev: string
  fields: SignField[]
  parties: { role_label: string; name: string; status: RecipientStatus; you: boolean }[]
  expires_at: string | null
  signed_at: string | null
  decline_reason: string | null
  void_reason: string | null
}

export type Mark = { kind: 'typed'; text: string } | { kind: 'drawn'; png: string }
