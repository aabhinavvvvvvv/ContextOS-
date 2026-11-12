// Documents, memories and connectors: the data ContextOS knows about.
import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'

import { api, errorMessage } from '../api'
import type { Connector, DocumentItem, MemoryItem, MemoryKind } from '../types'
import { useApi } from '../useApi'
import { formatDate, isPast } from '../format'
import { Badge, Button, Card, ErrorBanner, PageHeader, Spinner } from '../ui'

const DOC_TONE = { ready: 'green', pending: 'amber', processing: 'amber', failed: 'red' } as const

export function DocumentsPage() {
  const docs = useApi(api.documents)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // While anything is still being ingested, refresh every 3 seconds.
  const busy = docs.data?.some((d) => d.status === 'pending' || d.status === 'processing')
  const reloadDocs = docs.reload // stable (useCallback with no dependencies)
  useEffect(() => {
    if (!busy) return
    const timer = setInterval(reloadDocs, 3000)
    return () => clearInterval(timer) // cleanup: stop polling when done or when leaving
  }, [busy, reloadDocs])

  async function upload(files: FileList | null) {
    if (!files?.length) return
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(files)) await api.upload(file)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setUploading(false)
      void docs.reload()
    }
  }

  return (
    <div>
      <PageHeader title="Documents">
        <label className="cursor-pointer rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-500">
          {uploading ? 'Uploading…' : 'Upload .md, .txt or .pdf'}
          <input type="file" accept=".md,.txt,.pdf" multiple className="hidden" onChange={(e) => void upload(e.target.files)} />
        </label>
      </PageHeader>
      <ErrorBanner message={error ?? docs.error} />
      {docs.loading && !docs.data && <Spinner />}
      <div className="space-y-2">
        {docs.data?.map((d: DocumentItem) => (
          <Card key={d.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
            <div className="min-w-0">
              <div className="truncate text-sm font-medium">
                {d.source_url ? <a className="underline" href={d.source_url} target="_blank" rel="noreferrer">{d.filename}</a> : d.filename}
              </div>
              <div className="text-xs text-slate-500">
                {(d.size_bytes / 1024).toFixed(1)} KB · {formatDate(d.created_at)}
                {d.error && <span className="text-red-600"> · {d.error}</span>}
              </div>
            </div>
            <div className="flex gap-1">
              <Badge>{d.source}</Badge>
              <Badge tone={DOC_TONE[d.status]}>{d.status}</Badge>
            </div>
          </Card>
        ))}
        {docs.data?.length === 0 && <p className="text-sm text-slate-500">No documents yet. Upload one or add a connector.</p>}
      </div>
    </div>
  )
}

const KINDS: MemoryKind[] = ['decision', 'preference', 'task', 'fact']

export function MemoriesPage() {
  const [kind, setKind] = useState<MemoryKind | ''>('')
  const [history, setHistory] = useState(false)
  const memories = useApi(() => api.memories(kind, history), `${kind}-${history}`)

  return (
    <div>
      <PageHeader title="Memories">
        <select value={kind} onChange={(e) => setKind(e.target.value as MemoryKind | '')} className="rounded-lg border border-slate-300 bg-transparent px-2 py-1.5 text-sm dark:border-slate-700">
          <option value="">All kinds</option>
          {KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
        </select>
        <label className="flex items-center gap-1 text-sm">
          <input type="checkbox" checked={history} onChange={(e) => setHistory(e.target.checked)} />
          Show replaced and expired
        </label>
      </PageHeader>
      <p className="mb-3 text-sm text-slate-500">
        What ContextOS learned from your conversations. Changed facts replace old ones; time-bound ones expire.
      </p>
      <ErrorBanner message={memories.error} />
      {memories.loading && !memories.data && <Spinner />}
      <div className="space-y-2">
        {memories.data?.map((m) => <MemoryRow key={m.id} memory={m} onChanged={() => void memories.reload()} />)}
        {memories.data?.length === 0 && <p className="text-sm text-slate-500">Nothing remembered yet.</p>}
      </div>
    </div>
  )
}

function MemoryRow({ memory, onChanged }: { memory: MemoryItem; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(memory.content)
  const [error, setError] = useState<string | null>(null)
  const replaced = memory.superseded_by_id !== null
  const expired = memory.expires_on !== null && isPast(memory.expires_on)

  async function save() {
    try {
      await api.editMemory(memory.id, text)
      setEditing(false)
      onChanged()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function remove() {
    if (!confirm('Forget this memory?')) return
    try {
      await api.deleteMemory(memory.id)
      onChanged()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <Card className={replaced || expired ? 'opacity-60' : ''}>
      <div className="flex flex-wrap items-center gap-1 text-xs">
        <Badge tone="indigo">{memory.kind}</Badge>
        {replaced && <Badge>replaced</Badge>}
        {expired && <Badge>expired</Badge>}
        {memory.expires_on && !expired && <Badge tone="amber">until {memory.expires_on}</Badge>}
        <span className="text-slate-500">{formatDate(memory.created_at)}</span>
      </div>
      {editing ? (
        <div className="mt-2 flex flex-col gap-2 sm:flex-row">
          <input value={text} maxLength={300} onChange={(e) => setText(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-transparent px-2 py-1 text-sm dark:border-slate-700" />
          <div className="flex gap-2">
            <Button variant="primary" onClick={save} disabled={!text.trim()}>Save</Button>
            <Button onClick={() => { setEditing(false); setText(memory.content) }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="mt-1 flex flex-wrap items-start justify-between gap-2">
          <p className="text-sm">{memory.content}</p>
          <div className="flex gap-2">
            <Button onClick={() => setEditing(true)}>Edit</Button>
            <Button variant="danger" onClick={remove}>Delete</Button>
          </div>
        </div>
      )}
      <ErrorBanner message={error} />
    </Card>
  )
}

const CONNECTOR_TONE = { ok: 'green', error: 'red', never: 'gray' } as const

export function ConnectorsPage() {
  const connectors = useApi(api.connectors)
  const [kind, setKind] = useState<Connector['kind']>('github')
  const [name, setName] = useState('')
  const [error, setError] = useState<string | null>(null)

  async function add(event: FormEvent) {
    event.preventDefault()
    setError(null)
    try {
      await api.addConnector(kind, name.trim())
      setName('')
      void connectors.reload()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  async function run(action: () => Promise<unknown>) {
    setError(null)
    try {
      await action()
      void connectors.reload()
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div>
      <PageHeader title="Connectors">
        <Button onClick={() => void connectors.reload()}>Refresh</Button>
      </PageHeader>
      <Card className="mb-4">
        <form onSubmit={add} className="flex flex-col gap-2 sm:flex-row">
          <select value={kind} onChange={(e) => setKind(e.target.value as Connector['kind'])} className="rounded-lg border border-slate-300 bg-transparent px-2 py-1.5 text-sm dark:border-slate-700">
            <option value="github">GitHub repository</option>
            <option value="folder">Local folder</option>
          </select>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder={kind === 'github' ? 'owner/repo' : 'folder inside FOLDER_ROOT, e.g. notes'} className="min-w-0 flex-1 rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 text-sm dark:border-slate-700" />
          <Button variant="primary" type="submit" disabled={!name.trim()}>Connect</Button>
        </form>
        <p className="mt-2 text-xs text-slate-500">Synced into documents now and every 15 minutes by the worker. Issues, pull requests and commits for GitHub.</p>
      </Card>
      <ErrorBanner message={error ?? connectors.error} />
      {connectors.loading && !connectors.data && <Spinner />}
      <div className="space-y-2">
        {connectors.data?.map((c) => (
          <Card key={c.id} className="flex flex-wrap items-center justify-between gap-2">
            <div className="min-w-0">
              <div className="text-sm font-medium">{c.name} <Badge>{c.kind}</Badge></div>
              <div className="text-xs text-slate-500">
                Last sync: {formatDate(c.last_synced_at)} · {c.documents_changed} changed
                {c.last_error && <span className="text-red-600"> · {c.last_error}</span>}
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Badge tone={CONNECTOR_TONE[c.last_status]}>{c.last_status}</Badge>
              <Button onClick={() => void run(() => api.syncConnector(c.id))}>Sync now</Button>
              <Button variant="danger" onClick={() => confirm(`Remove ${c.name} and everything it synced?`) && void run(() => api.deleteConnector(c.id))}>Remove</Button>
            </div>
          </Card>
        ))}
        {connectors.data?.length === 0 && <p className="text-sm text-slate-500">No connectors yet.</p>}
      </div>
    </div>
  )
}
