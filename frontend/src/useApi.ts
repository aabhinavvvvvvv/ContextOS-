import { useCallback, useEffect, useState } from 'react'

import { errorMessage } from './api'

// A custom hook: loads data when the component appears (and again when `key` changes or
// reload() is called), and keeps the three states every screen needs: data, loading, error.
export function useApi<T>(load: () => Promise<T>, key: string = '') {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [version, setVersion] = useState(0)

  // Stable identity (no dependencies), so components can safely use it in their own effects.
  const reload = useCallback(() => setVersion((v) => v + 1), [])

  useEffect(() => {
    // If the key changes (or reload is called) before this request finishes, its result is
    // ignored. Without this, a slow older response could overwrite a newer one.
    let cancelled = false
    setLoading(true)
    setError(null)
    load()
      .then((result) => !cancelled && setData(result))
      .catch((e) => !cancelled && setError(errorMessage(e)))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
    // `key` stands for everything `load` depends on (e.g. the selected filters).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version])

  return { data, setData, loading, error, reload }
}
