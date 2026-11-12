// TypeScript mirrors of the backend's Pydantic response models (backend/app/*/schemas.py).
// `interface` describes the shape of an object; `string | null` means "a string or null".

export interface Citation {
  number: number // the [n] in the answer
  chunk_id: string
  document_id: string
  filename: string
  heading: string | null
  page_number: number | null
  text: string
  source_url?: string | null // e.g. the GitHub issue page
}

export interface MemoryCitation {
  number: number // the [Mn] in the answer
  memory_id: string
  kind: string
  content: string
  created_at: string
}

export interface TimelineEvent {
  step: string
  summary: string
  duration_ms: number
  error: string | null
}

export type ActionStatus = 'proposed' | 'approved' | 'rejected' | 'executed' | 'failed'

export interface Action {
  id: string
  kind: 'create_task' | 'create_github_issue'
  status: ActionStatus
  summary: string
  payload: Record<string, unknown>
  conversation_id: string | null
  message_id: string | null
  created_at: string
  decided_at: string | null
  executed_at: string | null
  result: Record<string, unknown> | null
  error: string | null
}

export interface AskResponse {
  conversation_id: string
  question: string
  standalone_question: string | null
  sub_questions: string[]
  answer: string
  citations: Citation[]
  memory_citations: MemoryCitation[]
  sources_considered: number
  input_tokens: number
  output_tokens: number
  timeline: TimelineEvent[]
  proposed_actions: Action[]
}

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface Message {
  id: string
  position: number
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  memory_citations: MemoryCitation[]
  created_at: string
}

export interface ConversationDetail extends Conversation {
  messages: Message[]
}

export type DocumentStatus = 'pending' | 'processing' | 'ready' | 'failed'

export interface DocumentItem {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  status: DocumentStatus
  error: string | null
  created_at: string
  source: 'upload' | 'github' | 'folder'
  source_url: string | null
}

export type MemoryKind = 'fact' | 'decision' | 'preference' | 'task'

export interface MemoryItem {
  id: string
  kind: MemoryKind
  content: string
  created_at: string
  conversation_id: string
  superseded_by_id: string | null
  superseded_at: string | null
  expires_on: string | null
}

export interface Connector {
  id: string
  kind: 'github' | 'folder'
  name: string
  created_at: string
  last_synced_at: string | null
  last_status: 'never' | 'ok' | 'error'
  last_error: string | null
  documents_changed: number
}

export interface AuditEvent {
  id: string
  at: string
  actor: string
  event: string
  target_type: string
  target_id: string
  detail: Record<string, unknown>
}

export interface LlmUsage {
  purpose: string
  calls: number
  errors: number
  input_tokens: number
  output_tokens: number
  p50_ms: number
  p95_ms: number
  estimated_cost_usd: number
}

export interface EndpointUsage {
  method: string
  path: string
  requests: number
  server_errors: number
  p50_ms: number
  p95_ms: number
}

export interface Metrics {
  since: string
  llm: LlmUsage[]
  endpoints: EndpointUsage[]
  total_estimated_cost_usd: number
  recent_errors: { at: string; source: string; error: string }[]
  pricing_note: string
}
