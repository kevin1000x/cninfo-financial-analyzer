# Frontend handoff templates

Files in this directory are reference artifacts for the upcoming
`cninfo-analyzer-web` repo. They live in this backend repo so they
are versioned alongside the API contract they consume; copy (don't
symlink) them into the frontend repo when you scaffold it.

## Files

| File | Drop into | Purpose |
|---|---|---|
| `pages-function-proxy.ts` | `cninfo-analyzer-web/functions/api/proxy/[[path]].ts` | Cloudflare Pages Function — same-origin proxy, injects `API_TOKEN`, streams SSE pass-through |
| `vite-config.snippet.ts` | merged into `cninfo-analyzer-web/vite.config.ts` | Vite dev server proxy — forwards `/api/proxy/*` to `localhost:8000` |

## End-to-end flow

```
local dev                        Cloudflare Pages prod
─────────                        ─────────────────────
browser :5173                    browser <pages-domain>
   │                                │
   │ /api/proxy/jobs/...            │ /api/proxy/jobs/...
   ▼                                ▼
Vite server.proxy                Pages Function functions/api/proxy/[[path]].ts
   │ no Authorization               │ Authorization: Bearer $API_TOKEN
   ▼                                ▼
uvicorn :8000                    cloudflared named tunnel
(API_TOKEN unset)                   │
                                    ▼
                                 uvicorn (API_TOKEN set)
```

The browser-facing URL is identical in both environments, so the React code
stays env-agnostic — `fetch('/api/proxy/jobs')`, `new EventSource('/api/proxy/jobs/x/stream')`.

## Pages secrets to set before deploy

```bash
# from inside the frontend repo
npx wrangler pages secret put API_BASE   # e.g. https://cninfo-api.your-domain.com
npx wrangler pages secret put API_TOKEN  # same value uvicorn was started with
```

`API_BASE` is the named tunnel hostname (`cloudflared tunnel route dns
<tunnel-name> cninfo-api.your-domain.com`). It must NOT be a quick
tunnel — those don't support SSE.

## What's intentionally not here

- `package.json`, `tsconfig.json`, `tailwind.config.ts` — produced by
  `pnpm create vite@latest` + `pnpm dlx shadcn@latest init`. Generating
  them ahead of time would just diverge from whatever the latest
  scaffolder produces.
- React components — too speculative without a real Node toolchain to
  iterate against.
