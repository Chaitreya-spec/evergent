# Evergent — frontend

Next.js 16 (App Router, Turbopack), strict TypeScript, Tailwind CSS 4, Radix
primitives, Lucide icons, React Flow (the canvas), Recharts (usage), TanStack
Query (data), Vitest + Testing Library, Playwright.

## Run

```bash
npm install
cp .env.example .env.local     # EVERGENT_API_URL — server-side only
npm run dev                    # http://localhost:3000 (backend on :8000)
```

## How it talks to the backend

- `src/app/api/session/route.ts` signs in, registers and signs out. Tokens are
  set as httpOnly cookies and never reach page JavaScript.
- `src/app/api/v1/[...path]/route.ts` forwards every API call with the
  session's bearer token, refreshes it when it expires (one refresh shared by
  concurrent requests), checks the request's origin on writes, and streams
  responses through — which is what makes the live SSE stream work.
- `src/proxy.ts` redirects signed-out visitors to `/signin`.
- `src/lib/api/schema.ts` is generated from the backend's OpenAPI document
  (`npm run api:types`); CI fails if it is stale.

## Layout

- `src/components/workspace/` — the workflow page: `EngineeringCanvas` (React
  Flow), `nodes`, `Inspector`, `Console`, `WorkflowHeader` (controls and plan
  approval).
- `src/lib/workflow/` — pure logic, unit-tested: `graph.ts` (layout, statuses,
  lineage, filters), `events.ts` (log lines, dedupe), `useWorkflowStream.ts`
  (replaying, reconnecting SSE), `preview.ts` (locked-down HTML preview),
  `problems.ts`.
- `src/components/pages/` — history, projects, agents, models & providers,
  files, usage, settings.
- `src/components/ui/` — themed, accessible primitives.

## Scripts

| Script | |
| --- | --- |
| `npm run dev` / `build` / `start` | Develop / production build / serve it |
| `npm run lint`, `npm run typecheck` | ESLint, `tsc --noEmit` |
| `npm test` | Vitest unit and component tests |
| `npm run test:e2e` | Playwright against real servers (see `playwright.config.ts`) |
| `npm run api:types` | Regenerate API types from `../backend/openapi.json` |

The production image (`Dockerfile`) runs the standalone server as a non-root
user; it contains no secrets — only `EVERGENT_API_URL` at runtime.
