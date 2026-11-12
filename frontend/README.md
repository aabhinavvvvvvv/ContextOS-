# ContextOS frontend

React 19 + TypeScript + Tailwind CSS 4, built with Vite.

```bash
npm install
npm run dev      # http://localhost:5173 ; /api/* is proxied to FastAPI on :8000
npm run build    # type-check (tsc) + production bundle in dist/
npm run lint     # oxlint
```

| File | Purpose |
|---|---|
| `src/api.ts` | the only module that calls the backend (`/api/...`), adds the API token, turns errors into messages |
| `src/types.ts` | TypeScript mirrors of the backend's response models |
| `src/useApi.ts` | data / loading / error state for a request; ignores stale responses |
| `src/ui.tsx` | shared components (buttons, badges, cards, the action approval card) |
| `src/pages/ChatPage.tsx` | conversations, answers with citations, memory citations, agent timeline, inline approvals |
| `src/pages/DataPages.tsx` | documents (upload, ingestion status), memories (edit, delete, history), connectors |
| `src/pages/OpsPages.tsx` | actions inbox + history + audit log, metrics, settings (API token) |
| `src/App.tsx` | layout and hash routing (`#/memories`) |
