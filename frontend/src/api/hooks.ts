import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from './client'
import type { Field, Me, Role, Template, TemplateSummary } from './types'

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
