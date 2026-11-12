// The only module that talks to the backend. Every call goes to /api/... (proxied to FastAPI).
import type {
  Action,
  ActionStatus,
  AskResponse,
  AuditEvent,
  Connector,
  Conversation,
  ConversationDetail,
  DocumentItem,
  MemoryItem,
  MemoryKind,
  Metrics,
} from './types'

const TOKEN_KEY = 'contextos.apiToken'

// localStorage can throw (private windows, blocked storage), so reads and writes are guarded.
export function getToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? ''
  } catch {
    return ''
  }
}

export function setToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    // storage unavailable: the token lasts only for this page load
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

// <T> is a type parameter: request<AskResponse>(...) returns a Promise of an AskResponse.
async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  const token = getToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)
  if (init.body && !(init.body instanceof FormData)) headers.set('Content-Type', 'application/json')

  const response = await fetch(`/api${path}`, { ...init, headers })
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      // not JSON: keep the status text
    }
    throw new ApiError(response.status, detail)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const api = {
  health: () => request<{ status: string; version: string }>('/health'),

  ask: (question: string, conversationId: string | null) =>
    post<AskResponse>('/ask', { question, conversation_id: conversationId }),
  conversations: () => request<Conversation[]>('/conversations'),
  conversation: (id: string) => request<ConversationDetail>(`/conversations/${id}`),

  documents: () => request<DocumentItem[]>('/documents?limit=200'),
  upload: (file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DocumentItem>('/documents', { method: 'POST', body: form })
  },

  memories: (kind: MemoryKind | '', includeHistory: boolean) =>
    request<MemoryItem[]>(`/memories?limit=200&include_history=${includeHistory}${kind ? `&kind=${kind}` : ''}`),
  editMemory: (id: string, content: string) =>
    request<MemoryItem>(`/memories/${id}`, { method: 'PATCH', body: JSON.stringify({ content }) }),
  deleteMemory: (id: string) => request<void>(`/memories/${id}`, { method: 'DELETE' }),

  actions: (status?: ActionStatus) => request<Action[]>(`/actions${status ? `?status=${status}` : ''}`),
  approve: (id: string) => post<Action>(`/actions/${id}/approve`),
  reject: (id: string) => post<Action>(`/actions/${id}/reject`),
  audit: () => request<AuditEvent[]>('/audit?limit=200'),

  connectors: () => request<Connector[]>('/connectors'),
  addConnector: (kind: Connector['kind'], name: string) => post<Connector>('/connectors', { kind, name }),
  syncConnector: (id: string) => post<{ queued: boolean }>(`/connectors/${id}/sync`),
  deleteConnector: (id: string) => request<void>(`/connectors/${id}`, { method: 'DELETE' }),

  metrics: (days: number) => request<Metrics>(`/metrics?days=${days}`),
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return 'Not authorized: set the API token in Settings.'
    return `${error.message} (HTTP ${error.status})`
  }
  return error instanceof Error ? error.message : String(error)
}
