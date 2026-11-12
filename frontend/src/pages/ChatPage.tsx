import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'

import { api, errorMessage } from '../api'
import type { AskResponse, Citation, MemoryCitation } from '../types'
import { useApi } from '../useApi'
import { ActionCard, Badge, Button, ErrorBanner, Spinner } from '../ui'

// One message on screen. `response` is only there for answers given in this session: the
// conversation history from the API has citations but not the timeline or proposals.
interface ChatMessage {
  role: 'user' | 'assistant'
  content: string
  citations: Citation[]
  memoryCitations: MemoryCitation[]
  response?: AskResponse
}

export function ChatPage() {
  const conversations = useApi(api.conversations)
  const [activeId, setActiveId] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [question, setQuestion] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => bottom.current?.scrollIntoView({ behavior: 'smooth' }), [messages, sending])

  async function openConversation(id: string) {
    setActiveId(id)
    setError(null)
    try {
      const detail = await api.conversation(id)
      setMessages(
        detail.messages.map((m) => ({
          role: m.role,
          content: m.content,
          citations: m.citations,
          memoryCitations: m.memory_citations,
        })),
      )
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  function newConversation() {
    setActiveId(null)
    setMessages([])
    setError(null)
  }

  async function send(event?: FormEvent) {
    event?.preventDefault()
    const text = question.trim()
    if (!text || sending) return
    setQuestion('')
    setError(null)
    setSending(true)
    // Show the question immediately ("optimistic update"); the answer is appended later.
    setMessages((prev) => [...prev, { role: 'user', content: text, citations: [], memoryCitations: [] }])
    try {
      const response = await api.ask(text, activeId)
      setActiveId(response.conversation_id)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: response.answer,
          citations: response.citations,
          memoryCitations: response.memory_citations,
          response,
        },
      ])
      void conversations.reload()
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setSending(false)
    }
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // Enter sends, Shift+Enter adds a new line.
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      void send()
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-4 md:flex-row">
      <aside className="flex max-h-48 shrink-0 flex-col gap-2 md:max-h-none md:w-64">
        <Button variant="primary" onClick={newConversation}>New conversation</Button>
        <div className="min-h-0 flex-1 overflow-y-auto">
          {conversations.loading && !conversations.data && <Spinner />}
          <ErrorBanner message={conversations.error} />
          {conversations.data?.map((c) => (
            <button
              key={c.id}
              onClick={() => openConversation(c.id)}
              className={`block w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-800 ${
                c.id === activeId ? 'bg-slate-200 font-medium dark:bg-slate-800' : ''
              }`}
            >
              {c.title}
            </button>
          ))}
        </div>
      </aside>

      <section className="flex min-h-0 flex-1 flex-col rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">
          {messages.length === 0 && (
            <div className="mx-auto max-w-md pt-12 text-center text-slate-500">
              <p className="text-lg font-medium text-slate-700 dark:text-slate-300">Ask ContextOS</p>
              <p className="mt-2 text-sm">
                Questions are answered from your documents, GitHub data and memories, with sources.
                Try: “What did we decide about the API, what tasks are unfinished, and prepare a plan for today?”
              </p>
            </div>
          )}
          {messages.map((m, i) => (m.role === 'user' ? <UserBubble key={i} text={m.content} /> : <Answer key={i} message={m} />))}
          {sending && <Spinner label="Thinking…" />}
          <ErrorBanner message={error} />
          <div ref={bottom} />
        </div>
        <form onSubmit={send} className="flex gap-2 border-t border-slate-200 p-3 dark:border-slate-800">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={1000}
            placeholder="Ask a question, or “Add a task to…”"
            className="min-w-0 flex-1 resize-none rounded-lg border border-slate-300 bg-transparent px-3 py-2 text-sm outline-none focus:border-indigo-500 dark:border-slate-700"
          />
          <Button variant="primary" type="submit" disabled={sending || !question.trim()}>Send</Button>
        </form>
      </section>
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl bg-indigo-600 px-4 py-2 text-sm text-white">{text}</div>
    </div>
  )
}

function Answer({ message }: { message: ChatMessage }) {
  const r = message.response
  return (
    <div className="max-w-[95%] space-y-3">
      <div className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</div>

      {r?.sub_questions && r.sub_questions.length > 1 && (
        <div className="flex flex-wrap gap-1">
          {r.sub_questions.map((q) => <Badge key={q} tone="indigo">{q}</Badge>)}
        </div>
      )}

      {r && r.proposed_actions.length > 0 && (
        <div className="space-y-2">{r.proposed_actions.map((a) => <ActionCard key={a.id} action={a} />)}</div>
      )}

      {(message.citations.length > 0 || message.memoryCitations.length > 0) && (
        <div className="space-y-1 text-xs">
          {message.citations.map((c) => (
            <details key={`s${c.number}`} className="rounded-lg bg-slate-50 px-2 py-1 dark:bg-slate-800/60">
              <summary className="cursor-pointer">
                <span className="font-semibold">[{c.number}]</span> {c.filename}
                {c.heading ? ` · ${c.heading}` : c.page_number ? ` · page ${c.page_number}` : ''}
                {c.source_url && (
                  <a className="ml-2 text-indigo-600 underline dark:text-indigo-400" href={c.source_url} target="_blank" rel="noreferrer">
                    open
                  </a>
                )}
              </summary>
              <p className="mt-1 whitespace-pre-wrap text-slate-600 dark:text-slate-400">{c.text}</p>
            </details>
          ))}
          {message.memoryCitations.map((c) => (
            <div key={`m${c.number}`} className="rounded-lg bg-emerald-50 px-2 py-1 dark:bg-emerald-950/40">
              <span className="font-semibold">[M{c.number}]</span> {c.content}{' '}
              <span className="text-slate-500">({c.kind}, {new Date(c.created_at).toLocaleDateString()})</span>
            </div>
          ))}
        </div>
      )}

      {r && r.timeline.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">
            Agent steps ({r.timeline.length}) · {r.input_tokens + r.output_tokens} tokens
          </summary>
          <ol className="mt-1 space-y-0.5 border-l border-slate-200 pl-3 dark:border-slate-700">
            {r.timeline.map((e, i) => (
              <li key={i} className={e.error ? 'text-red-600 dark:text-red-400' : ''}>
                <span className="font-mono">{e.step}</span> · {e.duration_ms} ms · {e.summary}
                {e.error && ` (${e.error})`}
              </li>
            ))}
          </ol>
        </details>
      )}
    </div>
  )
}
