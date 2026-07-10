# web - Web-Manus Frontend

React 19 + Vite + TypeScript single-page app. Drives the Manus agent through the FastAPI backend in `app/web/`. Vite dev server runs on port 5173 and proxies `/api` and `/ws` to the backend on `:9000`.

## Stack

- React 19, React DOM 19 (no router; single view in `App.tsx`).
- Vite 8 with `@vitejs/plugin-react` and `@tailwindcss/vite`.
- Tailwind CSS 4 (configured via the Vite plugin, PostCSS/autoprefixer present).
- `lucide-react` for icons.
- TypeScript ~6.0, ESLint 10 with `typescript-eslint`, `eslint-plugin-react-hooks`, `eslint-plugin-react-refresh`.
- No state library; `useState`/`useRef`/`useCallback` only.

## Source Map

| File | Purpose |
|------|---------|
| `src/main.tsx` | React entry, mounts `App`. |
| `src/App.tsx` | The whole UI: sidebar (sessions), task timeline, computer-view tabs (browser/editor/terminal/preview), input box. Largest file. |
| `src/types.ts` | Shared types: `TimelineItem`, `LogEntry`, `Artifact`, `ServerEvent`, `CreateSessionResponse`, `VmHealth`, `BrowserState`, `ComputerView`. |
| `src/hooks/useWebSocket.ts` | WebSocket connection + reconnection (3s backoff), HTTP helpers `createSession`/`sendHttpMessage`/`stopSession`. |
| `src/index.css` | Tailwind import + base styles. |
| `src/vite-env.d.ts` | Vite client types. |

## Event Flow

1. `useWebSocket(sessionId)` opens `ws://<host>/ws/{sessionId}` and buffers incoming `ServerEvent` JSON in `events`.
2. `App.tsx` drains `events` each render through `processEvent(event)`, mapping each `ServerEvent` to a `TimelineItem` or to a state update (browser screenshot, terminal lines, current file/code, progress).
3. User sends a message via `sendHttpMessage` (POST `/api/sessions/{id}/message`), not over the socket.

`ServerEvent.type` values (mirror `app/web/agent_runner.py`): `agent_start`, `agent_ready`, `thought`, `tool_call`, `tool_result`, `final_answer`, `message_complete`, `error`. `agent_ready` is deduped via `readyEventSeenRef` so capabilities render once.

## Layout

Three-column: collapsible sessions sidebar, task timeline (center), computer-view panel (right) with tabs `browser | editor | terminal | preview`. On mobile, `mobileView` toggles between task and computer. State for which tab and which session is all in `App.tsx` local state.

## Config

- `vite.config.ts`: dev port 5173, proxy `/api` -> `http://localhost:9000` and `/ws` -> `ws://localhost:9000`.
- `VITE_API_BASE` env var (defaults to `''`) prepends to API/WS paths; set it when the backend is on a different origin.
- `tsconfig.app.json` + `tsconfig.node.json` referenced by `tsconfig.json` (project references). Build is `tsc -b && vite build`.

## Commands

```bash
npm run dev      # Vite dev server, port 5173
npm run build    # tsc -b && vite build -> dist/
npm run lint     # eslint .
npm run preview  # serve the production build
```

`dist/` is gitignored output; `node_modules/` is gitignored.

## Gotchas

- `App.tsx` is monolithic (all UI + event handling). Splitting it is fine but keep the `processEvent` switch exhaustive over `ServerEvent.type` or events will silently drop.
- The WebSocket reconnects every 3s on close; there is no max-retry. If the backend is down the client keeps retrying.
- `nextIdRef` and `stepCountRef` are refs, not state, so they survive renders without triggering re-renders. Do not move them to state.
- `toolsAvailable`/`tool_calls_enabled` from `agent_ready` gates whether the UI shows tool-related affordances; handle the `false` case explicitly.
- No tests exist for the frontend. Verify changes by running `npm run dev` against a live backend and exercising the event flow.
