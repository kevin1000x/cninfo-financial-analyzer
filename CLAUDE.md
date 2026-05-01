# CLAUDE.md — project conventions for `cninfo-financial-analyzer`

This file is loaded into context for any Claude Code session run inside this repository. Keep it short and prescriptive.

## Web API conventions (api/ package)

The `api/` package wraps the existing `FinancialAnalysisPipeline` over HTTP for an upcoming web frontend. The following constraints **must** be respected by any new code or refactor in this layer.

### 1. SSE heartbeat is a protocol-level comment, not an event
- `EventSourceResponse(..., ping=15)` is the only heartbeat mechanism. It emits SSE comments (`:ping`) at the wire level; the browser and our `EventSource` client do not see them as messages.
- **Do not** emit `{"type": "ping", ...}` payloads from the runner. Do not push pings into `job.history`. Replay would surface them as noise on every refresh.
- Tests must guard this: after a completed job, `job.history` types must be a subset of `{status, log, done, error, eof}`.

### 2. Local Vite dev does not inject Authorization
- The Vite dev server's `server.proxy` forwards `/api/proxy/*` to `http://localhost:8000` **without** adding any `Authorization` header.
- Local development pattern: leave `API_TOKEN` unset on the FastAPI side; the backend then runs anonymously and the proxy needs no token.
- Production pattern: `API_TOKEN` is set on the FastAPI side; the Cloudflare Pages Function (`functions/api/proxy/[[path]].ts`) injects the token from a Pages secret.
- If you ever need to test the token path locally, add explicit header injection to the Vite proxy config — but don't make that the default.

### 3. CNINFO cookies are loaded server-side, never via the client
- A single helper `_load_cninfo_cookies()` in `api/runner.py` reads cookies from environment:
  - `CNINFO_COOKIES_FILE` — path to a JSON file `{"JSESSIONID": "...", ...}`. Takes precedence.
  - `CNINFO_COOKIES_JSON` — JSON string. Fallback.
  - Neither set → returns `None`; pipeline runs without authenticated cookies.
- Failures (file missing, malformed JSON) raise a clear `ValueError`. The worker thread's `try/except` converts that into a job `error` event for the SSE stream.
- **Cookie values must never appear** in: log lines, SSE events, `job.history`, API responses, or anywhere reachable by the browser. Only count or path may be logged (e.g. `loaded N cookies from /path`).

### 4. Same-origin proxy: pass-through, minimal headers
The Cloudflare Pages Function at `functions/api/proxy/[[path]].ts` must:
- Preserve original `method`, `body`, and query string when forwarding to the tunnel.
- For `/jobs/{id}/stream`: return `new Response(upstream.body, ...)` directly. Do **not** `await upstream.text()` or buffer — that breaks SSE.
- Set only the headers that matter: `Content-Type` (e.g. `text/event-stream`), `Cache-Control: no-cache`. Do **not** blanket-copy upstream headers; hop-by-hop headers (`Connection`, `Transfer-Encoding`, etc.) must not leak through.
- Inject `Authorization: Bearer <API_TOKEN>` from the Pages secret. The token never reaches the browser.

### 5. `report_types` whitelist
`api.main.ALLOWED_REPORT_TYPES` is the **single source of truth** for what the API will accept. It must only contain values that the downstream pipeline can actually handle end-to-end. Currently:
```python
ALLOWED_REPORT_TYPES = {"annual", "semi_annual", "quarterly"}
```
- `prospectus` is **not** supported: `config.yaml` has no `categories.prospectus`, `downloader._build_se_date()` does not handle it, and `_filter_announcements()` has no keyword list for it. Passing it would silently return zero results, so the API rejects it with 422.
- `config.yaml`'s `report_types.prospectus` mapping may stay; it's a lookup table, not a contract. Don't rely on it from new code paths.
- If a new type is added end-to-end (config + downloader + tests), update the whitelist alongside it. Don't widen the whitelist preemptively.

### 6. Demo / smoke-test stock codes
- **TNI demo**: `600000` (浦发银行), years `2020-2022`. Covered by `examples/financial_data.csv`, full TNI pipeline runs.
- **Real-stock demo without TNI**: `600519` (贵州茅台). Not in the example financial CSV; UI must explicitly mark this as "skip TNI". Useful for testing the download/parse path on a stock the example data doesn't cover.
- The example CSV only has `000001 / 000002 / 600000 / 600036`. Any TNI demo using other codes needs a user-supplied financial_data CSV.

## General style

- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't comment what well-named code already says. Comments explain *why*, not *what*.
- Don't write backwards-compat shims for code that hasn't been released — this is a research demo, breaking changes are fine.
- New behaviour ships with tests. The api/ tests stub `FinancialAnalysisPipeline` to keep the suite fast.
