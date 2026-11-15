import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './client'
import type {
  Field,
  FileDetail,
  FileInput,
  FileSummary,
  Mark,
  Me,
  Role,
  SignView,
  Template,
  TemplateSummary,
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
    // While it's out for signature, keep the timeline current.
    refetchInterval: (q) => (q.state.data && LIVE.has(q.state.data.status) ? 5000 : false),
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

export function useEditRecipient(id: string) {
  return useFileMutation(
    (vars: { recipientId: string; name?: string; email?: string; resend?: boolean }) => {
      const { recipientId, ...body } = vars
      return api.patch<FileDetail>(`/api/files/${id}/recipients/${recipientId}`, body)
    },
  )
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
