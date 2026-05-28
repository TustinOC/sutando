# `@sutando/client`

Sutando's React frontend. Hosts the **conversation** page — the chat shell that fronts the voice agent, task stream, and dynamic content panels. Other Sutando surfaces (core-cli, dashboard, settings) live elsewhere: the Python dashboard at `:7844`, the macOS panes in `src/Sutando/UnifiedMainWindow.swift`.

`GET /` serves this React bundle. `GET /v2[/*]` is an alias kept for old bookmarks. The legacy inline-HTML client (`/legacy`, `src/web-client-html.ts`) was removed once the React tree burned in.

## Architecture

Follows `CLAUDE.md § Frontend Conventions`:

| Layer | Purpose | Size budget |
|-------|---------|-------------|
| `pages/<page>/` | Thin orchestration; one per route | n/a |
| `components/atoms/` | Pure presentational | < 70 lines |
| `components/molecules/` | Composed of atoms | 70–150 lines |
| `components/organisms/` | Feature-complete sections | > 150 lines |
| `contexts/` | React Context providers | – |
| `hooks/` | Data fetching + business logic | – |
| `utils/` | Pure functions | – |
| `const-values/` | Copy + static config | – |
| `lib/` | Infrastructure (`config`, `api`, `sse`) | – |

**Rules:**
- Components render UI only. No `fetch` in components — hooks own that.
- No hardcoded strings. Copy lives in `const-values/`.
- One file ≤ ~150 lines. Split before it grows past that.
- `===` not `==`. `const` over `let`. Early returns over nesting.

## Routing

Only one route today (`conversation`), so there's no `react-router`. `src/lib/config.ts` parses `?page=` into `initialRouteId` (default `conversation`) so the shell is ready when additional panes land; until then `App.tsx` mounts `ConversationPage` unconditionally.

## Server-agnostic config

`src/lib/config.ts` resolves the WebSocket URL + API origin at runtime by:

1. `?ws=` / `?api=` query string (highest priority).
2. `window.__SUTANDO_CONFIG__` (injected by the host shell).
3. `window.location.host` (default — works for desktop WKWebView, remote browser over Tailscale, and the future mobile thin-client without rebuilding).

## Build

```bash
pnpm install            # only on the first run; runs at repo root, not in client/
pnpm --filter @sutando/client build  # writes client/dist/
```

`src/web-server.ts` serves `client/dist/index.html` + hashed assets at `/` and `/v2`. When the bundle isn't built yet, both routes return a `503` with a one-line `pnpm install && pnpm build:client` hint.

## Dev workflow

```bash
pnpm --filter @sutando/client dev    # vite dev server on http://localhost:5173
```

The dev server expects `voice-agent.ts` to be running so the conversation page can hit `/sse-status` etc. Pass `?api=http://localhost:8080` if the Vite dev server is on a different origin than the API.
