// Thin typed wrapper over fetch. Same-origin, cookie-authenticated (no tokens in JS).

export class ApiError extends Error {
  readonly status: number
  readonly requestId: string | null
  readonly body: unknown

  constructor(status: number, message: string, requestId: string | null, body: unknown) {
    super(message)
    this.status = status
    this.requestId = requestId
    this.body = body
  }
}

type Json = Record<string, unknown> | unknown[]

async function request<T>(method: string, path: string, body?: Json | FormData): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  let payload: BodyInit | undefined
  if (body instanceof FormData) {
    payload = body
  } else if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    payload = JSON.stringify(body)
  }
  const res = await fetch(path, { method, headers, body: payload, credentials: 'same-origin' })
  const requestId = res.headers.get('x-request-id')
  const text = await res.text()
  const data: unknown = text ? safeJson(text) : null
  if (!res.ok) {
    throw new ApiError(res.status, errorMessage(data, res.status), requestId, data)
  }
  return data as T
}

function safeJson(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function errorMessage(data: unknown, status: number): string {
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail) && detail[0]?.msg) return String(detail[0].msg)
  }
  if (status >= 500) return 'Something went wrong on our side. Please try again.'
  return `Request failed (${status})`
}

export const api = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: Json | FormData) => request<T>('POST', path, body),
  patch: <T>(path: string, body?: Json) => request<T>('PATCH', path, body),
  put: <T>(path: string, body?: Json) => request<T>('PUT', path, body),
  delete: <T>(path: string) => request<T>('DELETE', path),
}
