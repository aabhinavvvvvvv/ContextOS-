// Plain helpers (no components), kept apart so React fast refresh works for ui.tsx.

export const formatDate = (iso: string | null) => (iso ? new Date(iso).toLocaleString() : '—')

/** True if a YYYY-MM-DD date is before today (in the browser's time zone). */
export function isPast(day: string): boolean {
  const now = new Date()
  const today = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
  return day < today
}
