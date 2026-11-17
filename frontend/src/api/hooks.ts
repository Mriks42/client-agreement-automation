import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './client'
import type {
  ApiKey,
  Field,
  FileDetail,
  FileInput,
  FileSummary,
  InvitePreview,
  Mark,
  Me,
  NewApiKey,
  Role,
  SignView,
  Team,
  Template,
  TemplateSummary,
  WebhookDelivery,
  WebhookSettings,
} from './types'

// ---- session --------------------------------------------------------------------------------
export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: async (): Promise<Me | null> => {
      try {
        return await api.get<Me>('/api/auth/me')
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null
        throw e
      }
    },
    staleTime: 60_000,
  })
}

export function useLogin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; password: string }) =>
      api.post<Me>('/api/auth/login', body),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

export function useSignup() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { org_name: string; name: string; email: string; password: string }) =>
      api.post<Me>('/api/auth/signup', body),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}

export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<null>('/api/auth/logout'),
    onSettled: () => {
      qc.clear()
      qc.setQueryData(['me'], null)
    },
  })
}

// ---- templates ------------------------------------------------------------------------------
export function useTemplates() {
  return useQuery({
    queryKey: ['templates'],
    queryFn: () => api.get<TemplateSummary[]>('/api/templates'),
  })
}

export function useTemplate(id: string) {
  return useQuery({
    queryKey: ['template', id],
    queryFn: () => api.get<Template>(`/api/templates/${id}`),
    // Poll while the background analysis runs.
    refetchInterval: (q) => (q.state.data?.status === 'analyzing' ? 2500 : false),
    refetchOnWindowFocus: (q) => q.state.data?.status === 'analyzing',
  })
}

function useTemplateMutation<V>(fn: (vars: V) => Promise<Template>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (t) => {
      qc.setQueryData(['template', t.id], t)
      void qc.invalidateQueries({ queryKey: ['templates'] })
    },
  })
}

export function useUploadTemplate() {
  return useTemplateMutation((vars: { file: File; name?: string }) => {
    const form = new FormData()
    form.append('file', vars.file)
    if (vars.name) form.append('name', vars.name)
    return api.post<Template>('/api/templates', form)
  })
}

export function useSaveTemplate(id: string) {
  return useTemplateMutation(
    (vars: { revision: number; roles: Role[]; fields: Field[]; name?: string }) =>
      api.put<Template>(`/api/templates/${id}`, vars),
  )
}

export function useApproveTemplate(id: string) {
  return useTemplateMutation((vars: { revision: number }) =>
    api.post<Template>(`/api/templates/${id}/approve`, vars),
  )
}

export function useRetryAnalysis(id: string) {
  return useTemplateMutation(() => api.post<Template>(`/api/templates/${id}/retry-analysis`))
}

export function usePlaceManually(id: string) {
  return useTemplateMutation(() => api.post<Template>(`/api/templates/${id}/manual`))
}

// ---- files ----------------------------------------------------------------------------------
const LIVE: ReadonlySet<string> = new Set(['sent', 'partially_signed'])

export function useFiles() {
  return useQuery({
    queryKey: ['files'],
    queryFn: () => api.get<FileSummary[]>('/api/files'),
    refetchInterval: (q) => (q.state.data?.some((f) => LIVE.has(f.status)) ? 15_000 : false),
  })
}

export function useFile(id: string) {
  return useQuery({
    queryKey: ['file', id],
    queryFn: () => api.get<FileDetail>(`/api/files/${id}`),
    // While it's out for signature, keep the timeline current; once signed, until the signed
    // PDF is ready (or making it failed).
    refetchInterval: (q) => {
      const f = q.state.data
      if (!f) return false
      if (LIVE.has(f.status)) return 5000
      if (f.status === 'completed' && !f.finalized_at && !f.finalize_error) return 3000
      return false
    },
  })
}

function useFileMutation<V>(fn: (vars: V) => Promise<FileDetail>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (f) => {
      qc.setQueryData(['file', f.id], f)
      void qc.invalidateQueries({ queryKey: ['files'] })
    },
  })
}

export function useCreateFile() {
  return useFileMutation((vars: FileInput & { template_id: string }) =>
    api.post<FileDetail>('/api/files', vars),
  )
}

export function useEditFile(id: string) {
  return useFileMutation((vars: FileInput & { revision: number }) =>
    api.put<FileDetail>(`/api/files/${id}`, vars),
  )
}

export function useSendFile(id: string) {
  return useFileMutation((vars: { revision: number }) =>
    api.post<FileDetail>(`/api/files/${id}/send`, vars),
  )
}

export function useVoidFile(id: string) {
  return useFileMutation((vars: { reason: string }) =>
    api.post<FileDetail>(`/api/files/${id}/void`, vars),
  )
}

export function useRetryFinalize(id: string) {
  return useFileMutation(() => api.post<FileDetail>(`/api/files/${id}/finalize`))
}

export function useEditRecipient(id: string) {
  return useFileMutation(
    (vars: { recipientId: string; name?: string; email?: string; resend?: boolean }) => {
      const { recipientId, ...body } = vars
      return api.patch<FileDetail>(`/api/files/${id}/recipients/${recipientId}`, body)
    },
  )
}

/** For the logged-in person who is the file's current signer: their signing page, no email. */
export function useSignNow(id: string) {
  return useMutation({
    mutationFn: (recipientId: string) => api.post<{ url: string }>(`/api/files/${id}/recipients/${recipientId}/sign-now`),
  })
}

export function useCopyLink(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (recipientId: string) =>
      api.post<{ url: string }>(`/api/files/${id}/recipients/${recipientId}/link`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['file', id] }),
  })
}

export function useDeleteFile(id: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete<null>(`/api/files/${id}`),
    onSuccess: () => {
      qc.removeQueries({ queryKey: ['file', id] })
      void qc.invalidateQueries({ queryKey: ['files'] })
    },
  })
}

// ---- signing (public, token in the path) ----------------------------------------------------
export function useSignView(token: string) {
  return useQuery({
    queryKey: ['sign', token],
    queryFn: () => api.get<SignView>(`/api/sign/${token}`),
    staleTime: 60_000,
    refetchOnWindowFocus: false,
    // Right after the last signature, the signed PDF takes a few seconds to make. Stop after
    // about 3 minutes: if it failed, the sender sees that and the copy arrives by email.
    refetchInterval: (q) =>
      q.state.data?.state === 'completed' && !q.state.data.signed_pdf_ready && q.state.dataUpdateCount < 60
        ? 3000
        : false,
  })
}

function useSignMutation<V>(token: string, fn: (vars: V) => Promise<SignView | null>) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: (view) => {
      if (view) qc.setQueryData(['sign', token], view)
      else void qc.invalidateQueries({ queryKey: ['sign', token] })
    },
  })
}

export function useConsent(token: string) {
  return useSignMutation(token, async () => {
    await api.post<null>(`/api/sign/${token}/consent`)
    return null
  })
}

export function useSubmitSignature(token: string) {
  return useSignMutation(
    token,
    (vars: { values: Record<string, string | boolean>; signature: Mark | null; initials: Mark | null; tz: string }) =>
      api.post<SignView>(`/api/sign/${token}/submit`, vars),
  )
}

export function useDecline(token: string) {
  return useSignMutation(token, (vars: { reason: string }) =>
    api.post<SignView>(`/api/sign/${token}/decline`, vars),
  )
}

// ---- settings: API keys and webhooks --------------------------------------------------------
export function useApiKeys() {
  return useQuery({ queryKey: ['api-keys'], queryFn: () => api.get<ApiKey[]>('/api/settings/api-keys') })
}

export function useCreateApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (name: string) => api.post<NewApiKey>('/api/settings/api-keys', { name }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['api-keys'] }),
  })
}

export function useRevokeApiKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<ApiKey>(`/api/settings/api-keys/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['api-keys'] }),
  })
}

export function useWebhook() {
  return useQuery({ queryKey: ['webhook'], queryFn: () => api.get<WebhookSettings>('/api/settings/webhook') })
}

export function useSaveWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { url: string; rotate_secret?: boolean }) =>
      api.put<WebhookSettings>('/api/settings/webhook', body),
    onSuccess: (data) => qc.setQueryData(['webhook'], data),
  })
}

export function useDeleteWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.delete<null>('/api/settings/webhook'),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['webhook'] }),
  })
}

export function useTestWebhook() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ queued: boolean }>('/api/settings/webhook/test'),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['webhook-deliveries'] }),
  })
}

export function useWebhookDeliveries(enabled: boolean) {
  return useQuery({
    queryKey: ['webhook-deliveries'],
    queryFn: () => api.get<WebhookDelivery[]>('/api/settings/webhook/deliveries'),
    enabled,
    // Keep the log moving while anything is still being delivered.
    refetchInterval: (q) => (q.state.data?.some((d) => d.status === 'pending' || d.status === 'running') ? 4000 : 30000),
  })
}

export function useRetryDelivery() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.post<{ queued: boolean }>(`/api/settings/webhook/deliveries/${id}/retry`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['webhook-deliveries'] }),
  })
}

// ---- team -----------------------------------------------------------------------------------
export function useTeam() {
  return useQuery({ queryKey: ['team'], queryFn: () => api.get<Team>('/api/settings/team') })
}

export function useInviteMember() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { email: string; name?: string }) => api.post<Team>('/api/settings/team/invites', body),
    onSuccess: (team) => qc.setQueryData(['team'], team),
  })
}

export function useCopyInviteLink() {
  return useMutation({
    mutationFn: (id: string) => api.post<{ url: string }>(`/api/settings/team/invites/${id}/link`),
  })
}

export function useRevokeInvite() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<null>(`/api/settings/team/invites/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['team'] }),
  })
}

export function useRemoveMember() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => api.delete<null>(`/api/settings/team/members/${id}`),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ['team'] }),
  })
}

export function useInvitePreview(token: string) {
  return useQuery({
    queryKey: ['invite', token],
    queryFn: () => api.get<InvitePreview>(`/api/auth/invites/${token}`),
    retry: false,
  })
}

export function useJoin() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (body: { token: string; name: string; password: string }) => api.post<Me>('/api/auth/join', body),
    onSuccess: (me) => qc.setQueryData(['me'], me),
  })
}
