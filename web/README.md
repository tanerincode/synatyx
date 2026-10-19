# Synatyx dashboard (web/)

Vite + React + TypeScript (strict). Built to `web/dist` and served by the Python
server at `/dashboard`; all data comes from `/dashboard/api/*` with the admin key
(`Authorization: Bearer`, kept in `sessionStorage`).

```bash
npm ci
npm run dev             # http://localhost:5173/dashboard/, proxies /dashboard/api to :8000
VITE_MOCK_ALL=1 npm run dev   # no server needed: every endpoint served from src/api/mockData.ts
npm run check           # typecheck + lint + vitest + build (same as `make web-check`)
npx playwright test     # e2e against `vite preview` with mocks (same as `make web-e2e`)
E2E_PORT=4184 npx playwright test  # pick your own port: several worktrees share this machine
```

## e2e screenshots

Specs save screenshots under `e2e/screenshots/` (git-ignored, ~280 KB each). They
belong in the PR, not the repo: upload them to the PR body or comments (or commit
them temporarily on the PR branch and drop them before merge). Nothing under
`e2e/screenshots/` is ever merged.

## Layout

| Path | What |
| --- | --- |
| `src/api/client.ts` | The only module that talks to the server: one typed function per endpoint (`api.*`). |
| `src/api/types.ts` | Response and request shapes, mirroring `src/transports/mcp/dashboard/`. |
| `src/api/mockData.ts` | Deterministic mock store used when `VITE_MOCK_ALL=1` (tree-shaken from production builds). |
| `src/components/Shell.tsx` | Sidebar, top bar (project/user switchers, refresh, theme), ⌘K palette. |
| `src/components/ui.tsx` | Shared pieces: icons, layer pill, status chip, agent, empty/error/skeleton states, dialogs, toasts. |
| `src/lib/` | `useApi` (fetch + refresh), `useScope` (project/user in the URL), `useShell`, markdown, diff, formatting. |
| `src/pages/<Page>.tsx` | One file per page (plus `src/pages/<page>/` for its parts). |

## Conventions

- Pages never call `fetch`; they call `api.*` through `useApi(key, fn)`. The key must encode every input.
- Project and user live in the URL query (`?project=&user=`); use `useScope()` and `scoped()` for links.
- Every list has a skeleton, an empty state that says what fills it, and an `ErrorState` with the server's message and a retry.
- Layer colours are CSS variables (`--l1`…`--l4`); never hard-code them.
- Content from the server is rendered with `<Markdown>` (React nodes, no `innerHTML`).
