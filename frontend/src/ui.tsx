// Small shared components. A React component is a function that takes props (an object of
// inputs) and returns JSX (HTML-like markup that compiles to JavaScript).
import { useState } from 'react'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

import { api, errorMessage } from './api'
import type { Action } from './types'

export function Spinner({ label = 'Loading…' }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 text-sm text-slate-500" role="status">
      <span className="size-4 animate-spin rounded-full border-2 border-slate-300 border-t-indigo-500" />
      {label}
    </div>
  )
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <div className="rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-900 dark:bg-red-950 dark:text-red-200">
      {message}
    </div>
  )
}

const TONES = {
  gray: 'bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300',
  green: 'bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300',
  amber: 'bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300',
  red: 'bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300',
  indigo: 'bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300',
}

export function Badge({ tone = 'gray', children }: { tone?: keyof typeof TONES; children: ReactNode }) {
  return <span className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>{children}</span>
}

export function Card({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-white p-4 dark:border-slate-800 dark:bg-slate-900 ${className}`}>
      {children}
    </div>
  )
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: 'primary' | 'secondary' | 'danger' }

export function Button({ variant = 'secondary', className = '', ...props }: ButtonProps) {
  const styles = {
    primary: 'bg-indigo-600 text-white hover:bg-indigo-500',
    secondary: 'border border-slate-300 bg-white hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:hover:bg-slate-800',
    danger: 'border border-red-300 text-red-700 hover:bg-red-50 dark:border-red-900 dark:text-red-300 dark:hover:bg-red-950',
  }[variant]
  return (
    <button
      className={`rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
      {...props}
    />
  )
}

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
      <h1 className="text-xl font-semibold">{title}</h1>
      <div className="flex flex-wrap items-center gap-2">{children}</div>
    </div>
  )
}

const STATUS_TONE: Record<Action['status'], keyof typeof TONES> = {
  proposed: 'amber',
  approved: 'indigo',
  rejected: 'gray',
  executed: 'green',
  failed: 'red',
}

/** A proposed action with Approve / Reject. Nothing runs until the user clicks Approve. */
export function ActionCard({ action, onChange }: { action: Action; onChange?: (a: Action) => void }) {
  const [current, setCurrent] = useState(action)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function decide(approve: boolean) {
    setBusy(true)
    setError(null)
    try {
      const updated = approve ? await api.approve(current.id) : await api.reject(current.id)
      setCurrent(updated)
      onChange?.(updated)
    } catch (e) {
      setError(errorMessage(e))
    } finally {
      setBusy(false)
    }
  }

  const issueUrl = typeof current.result?.issue_url === 'string' ? current.result.issue_url : null
  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3 dark:border-amber-900 dark:bg-amber-950/30">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-sm font-medium">{current.summary}</div>
        <Badge tone={STATUS_TONE[current.status]}>{current.status}</Badge>
      </div>
      {current.status === 'proposed' && (
        <div className="mt-2 flex gap-2">
          <Button variant="primary" disabled={busy} onClick={() => decide(true)}>Approve</Button>
          <Button disabled={busy} onClick={() => decide(false)}>Reject</Button>
        </div>
      )}
      {issueUrl && (
        <a className="mt-1 block text-sm text-indigo-600 underline dark:text-indigo-400" href={issueUrl} target="_blank" rel="noreferrer">
          Open the created issue
        </a>
      )}
      {current.error && <p className="mt-1 text-sm text-red-700 dark:text-red-300">{current.error}</p>}
      <ErrorBanner message={error} />
    </div>
  )
}
