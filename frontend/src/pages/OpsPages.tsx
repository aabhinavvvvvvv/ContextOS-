// Actions (approval inbox + audit log), metrics and settings.
import { useState } from 'react'
import type { ReactNode } from 'react'

import { api, errorMessage, getToken, setToken } from '../api'
import type { Action } from '../types'
import { useApi } from '../useApi'
import { formatDate } from '../format'
import { ActionCard, Badge, Button, Card, ErrorBanner, PageHeader, Spinner } from '../ui'

export function ActionsPage() {
  const [tab, setTab] = useState<'pending' | 'history' | 'audit'>('pending')
  const pending = useApi(() => api.actions('proposed'), `p-${tab}`)
  const all = useApi(() => api.actions(), `a-${tab}`)
  const audit = useApi(api.audit, `l-${tab}`)

  return (
    <div>
      <PageHeader title="Actions">
        {(['pending', 'history', 'audit'] as const).map((t) => (
          <Button key={t} variant={tab === t ? 'primary' : 'secondary'} onClick={() => setTab(t)}>
            {t === 'pending' ? `Waiting for you${pending.data ? ` (${pending.data.length})` : ''}` : t === 'history' ? 'History' : 'Audit log'}
          </Button>
        ))}
      </PageHeader>
      <p className="mb-3 text-sm text-slate-500">The agent only proposes. Nothing changes until you approve, and each action runs at most once.</p>

      {tab === 'pending' && (
        <div className="space-y-2">
          <ErrorBanner message={pending.error} />
          {pending.loading && !pending.data && <Spinner />}
          {pending.data?.map((a: Action) => <ActionCard key={a.id} action={a} onChange={() => void pending.reload()} />)}
          {pending.data?.length === 0 && <p className="text-sm text-slate-500">Nothing waiting for approval.</p>}
        </div>
      )}

      {tab === 'history' && (
        <Table
          loading={all.loading && !all.data}
          error={all.error}
          head={['Created', 'Action', 'Status', 'Result']}
          rows={(all.data ?? []).map((a) => [
            formatDate(a.created_at),
            a.summary,
            <Badge key="s" tone={a.status === 'executed' ? 'green' : a.status === 'failed' ? 'red' : a.status === 'proposed' ? 'amber' : 'gray'}>{a.status}</Badge>,
            a.error ?? (a.result ? JSON.stringify(a.result) : '—'),
          ])}
        />
      )}

      {tab === 'audit' && (
        <Table
          loading={audit.loading && !audit.data}
          error={audit.error}
          head={['When', 'Actor', 'Event', 'Target']}
          rows={(audit.data ?? []).map((e) => [formatDate(e.at), e.actor, e.event, `${e.target_type} ${e.target_id.slice(0, 8)}`])}
        />
      )}
    </div>
  )
}

export function MetricsPage() {
  const [days, setDays] = useState(7)
  const metrics = useApi(() => api.metrics(days), String(days))
  const m = metrics.data

  return (
    <div>
      <PageHeader title="Metrics">
        <select value={days} onChange={(e) => setDays(Number(e.target.value))} className="rounded-lg border border-slate-300 bg-transparent px-2 py-1.5 text-sm dark:border-slate-700">
          {[1, 7, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? 's' : ''}</option>)}
        </select>
        <Button onClick={() => void metrics.reload()}>Refresh</Button>
      </PageHeader>
      <ErrorBanner message={metrics.error} />
      {metrics.loading && !m && <Spinner />}
      {m && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="LLM calls" value={m.llm.reduce((n, u) => n + u.calls, 0)} />
            <Stat label="LLM errors" value={m.llm.reduce((n, u) => n + u.errors, 0)} />
            <Stat label="Tokens" value={m.llm.reduce((n, u) => n + u.input_tokens + u.output_tokens, 0)} />
            <Stat label="Est. cost" value={`$${m.total_estimated_cost_usd.toFixed(4)}`} />
          </div>
          <p className="text-xs text-slate-500">{m.pricing_note}</p>
          <h2 className="font-medium">LLM calls by purpose</h2>
          <Table head={['Purpose', 'Calls', 'Errors', 'Tokens in / out', 'p50', 'p95', 'Cost']} rows={m.llm.map((u) => [
            u.purpose, u.calls, u.errors, `${u.input_tokens} / ${u.output_tokens}`, `${Math.round(u.p50_ms)} ms`, `${Math.round(u.p95_ms)} ms`, `$${u.estimated_cost_usd.toFixed(5)}`,
          ])} />
          <h2 className="font-medium">Endpoints</h2>
          <Table head={['Endpoint', 'Requests', '5xx', 'p50', 'p95']} rows={m.endpoints.map((e) => [
            `${e.method} ${e.path}`, e.requests, e.server_errors, `${Math.round(e.p50_ms)} ms`, `${Math.round(e.p95_ms)} ms`,
          ])} />
          <h2 className="font-medium">Recent errors</h2>
          <Table head={['When', 'Where', 'Error']} rows={m.recent_errors.map((e) => [formatDate(e.at), e.source, e.error])} />
        </div>
      )}
    </div>
  )
}

export function SettingsPage() {
  const [token, setTokenValue] = useState(getToken())
  const [status, setStatus] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function test() {
    setStatus(null)
    setError(null)
    try {
      const health = await api.health()
      await api.documents() // needs the token when API_TOKEN is set
      setStatus(`Connected to ContextOS ${health.version}.`)
    } catch (e) {
      setError(errorMessage(e))
    }
  }

  return (
    <div className="max-w-xl">
      <PageHeader title="Settings" />
      <Card className="space-y-3">
        <label className="block text-sm font-medium" htmlFor="token">API token</label>
        <p className="text-xs text-slate-500">Only needed when the server sets API_TOKEN. Stored in this browser only.</p>
        <input id="token" type="password" value={token} onChange={(e) => setTokenValue(e.target.value)} className="w-full rounded-lg border border-slate-300 bg-transparent px-3 py-1.5 text-sm dark:border-slate-700" />
        <div className="flex gap-2">
          <Button variant="primary" onClick={() => { setToken(token.trim()); void test() }}>Save and test</Button>
          <Button onClick={() => { setTokenValue(''); setToken('') }}>Clear</Button>
        </div>
        {status && <p className="text-sm text-emerald-700 dark:text-emerald-400">{status}</p>}
        <ErrorBanner message={error} />
      </Card>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <Card className="py-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="text-lg font-semibold">{value}</div>
    </Card>
  )
}

function Table({ head, rows, loading, error }: { head: string[]; rows: ReactNode[][]; loading?: boolean; error?: string | null }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-slate-200 dark:border-slate-800">
      <ErrorBanner message={error ?? null} />
      {loading ? (
        <div className="p-3"><Spinner /></div>
      ) : (
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-100 text-xs uppercase text-slate-500 dark:bg-slate-800">
            <tr>{head.map((h) => <th key={h} className="px-3 py-2 font-medium">{h}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-t border-slate-200 dark:border-slate-800">
                {row.map((cell, j) => <td key={j} className="px-3 py-2 align-top">{cell}</td>)}
              </tr>
            ))}
            {rows.length === 0 && <tr><td className="px-3 py-3 text-slate-500" colSpan={head.length}>Nothing yet.</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  )
}
