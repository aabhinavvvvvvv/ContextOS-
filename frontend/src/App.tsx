import { useEffect, useState } from 'react'

import { ChatPage } from './pages/ChatPage'
import { ConnectorsPage, DocumentsPage, MemoriesPage } from './pages/DataPages'
import { ActionsPage, MetricsPage, SettingsPage } from './pages/OpsPages'

const PAGES = {
  chat: { label: 'Chat', render: () => <ChatPage /> },
  documents: { label: 'Documents', render: () => <DocumentsPage /> },
  memories: { label: 'Memories', render: () => <MemoriesPage /> },
  actions: { label: 'Actions', render: () => <ActionsPage /> },
  connectors: { label: 'Connectors', render: () => <ConnectorsPage /> },
  metrics: { label: 'Metrics', render: () => <MetricsPage /> },
  settings: { label: 'Settings', render: () => <SettingsPage /> },
}
type PageKey = keyof typeof PAGES

// Hash routing (#/memories): the page survives reloads and the back button works, with no
// router library and no server configuration.
function currentPage(): PageKey {
  const key = window.location.hash.replace(/^#\/?/, '')
  return key in PAGES ? (key as PageKey) : 'chat'
}

export default function App() {
  const [page, setPage] = useState<PageKey>(currentPage)

  useEffect(() => {
    const onHashChange = () => setPage(currentPage())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  return (
    <div className="flex h-dvh flex-col md:flex-row">
      <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-slate-200 bg-white p-2 md:w-52 md:flex-col md:border-r md:border-b-0 dark:border-slate-800 dark:bg-slate-900">
        <div className="hidden px-3 py-2 md:block">
          <div className="text-lg font-semibold">ContextOS</div>
          <div className="text-xs text-slate-500">Agentic RAG + memory</div>
        </div>
        {(Object.keys(PAGES) as PageKey[]).map((key) => (
          <a
            key={key}
            href={`#/${key}`}
            className={`whitespace-nowrap rounded-lg px-3 py-2 text-sm ${
              page === key ? 'bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300' : 'hover:bg-slate-100 dark:hover:bg-slate-800'
            }`}
          >
            {PAGES[key].label}
          </a>
        ))}
      </nav>
      <main className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">{PAGES[page].render()}</main>
    </div>
  )
}
