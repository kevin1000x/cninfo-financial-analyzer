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

### 7. Job lifecycle: one slot, cancellable, watchdog-bounded
- The runner holds exactly **one** active job. `POST /jobs/{id}/cancel` and the `JOB_TIMEOUT_SECONDS` watchdog are the only two ways to free a wedged slot short of restarting the server.
- Cancellation is a **flag, not a signal**. `JobRegistry.cancel` sets `job.cancel_requested`; the pump thread is the only thread that ever touches the child process. Sending the signal from the API thread instead would race the pump on `waitpid` (`multiprocessing.Popen.terminate` reaps without a guard).
- The pump polls the pipe (`PUMP_POLL_INTERVAL`) rather than blocking on `recv`, and re-checks cancel + deadline at the top of every iteration, so both take effect within one interval whether the worker is silent or chattering.
- A **cancel** ends as `status="cancelled"` and emits only a `status` event. It is a terminal state, not a failure — it must never emit an `error` event, or the UI paints a user-requested stop with the red failure card.
- A **timeout** ends as `status="error"` and *does* emit an `error` event; nobody asked for it, so it is a failure.
- `JOB_TIMEOUT_SECONDS=0` disables the watchdog. Read per job (not at import) so a malformed value fails `POST /jobs` loudly instead of silently disabling the timeout.
- The thread runner (`JOB_RUNNER_MODE=thread`, test-only) has no killable worker, so `cancel` returns **409** rather than accepting a request it cannot honour.

## Parsing & analysis conventions (src/ package)

### 8. Segment once, share the word list
- `analyze_text` runs `segment_text` **once** and passes the resulting list to both `calculate_tone` and `calculate_fog_index` via their optional `words` parameter. jieba over a full MD&A section dominates analysis cost; segmenting per metric roughly doubled it (~45% of `analyze_text` time on a 6k-char section).
- Both metrics keep `words=None` as the default so direct callers (`scripts/readability_vs_performance.py`) are unaffected.
- Any new word-level metric must take the same optional `words` parameter. Do not call `segment_text` again inside a metric.

### 9. Table extraction is opt-in per call, and the streaming path opts out
- `parse_pdf(..., extract_tables=None)` — `None` means "use `config.yaml`'s `parser.extract_tables`". The flag is a per-call override, not a config change.
- `_process_single_report` (the streaming/web path) passes `extract_tables=False`. `select_analysis_text` only ever reads `parse_result['text']` and `['mda_text']`, so the pdfplumber table engine was most of the per-report parse time and its `table_N.csv` / statement CSVs went unread.
- The batch path (`parse_phase`) keeps the configured flag because it exports those CSVs as deliverables.
- If a future metric needs tables on the streaming path, flip that one call site — don't widen `config.yaml`, which would re-enable extraction for every caller.

### 10. The streaming manifest is a run's only durable trace
- `run_streaming` records every task in `StreamingManifest` (default `data/results/streaming_manifest.json`, override `streaming.manifest_path`) and writes **per task**, not in batches. A cancel or the watchdog SIGTERMs the worker without unwinding it, so a batch counter would lose the tail; and results otherwise live only in memory until the final export.
- Statuses are exhaustive and mutually exclusive: `ok`, `no_announcements`, `query_error`, `no_url`, `download_failed`, `no_text`, `process_error`. **Every** early `return None` in `_process_single_report` must record one — a task that vanishes without a ledger entry is exactly the bug this exists to prevent.
- `ok` entries carry the **whole analysis dict**, not just a status. That is what lets `resume=True` export a complete summary instead of only the reports it re-processed.
- Writes go through `<file>.tmp` + `Path.replace`. SIGTERM can land mid-write, and a torn manifest would break every later resume.
- The announcement query is wrapped: one transient CNINFO failure records `query_error` and moves on. Unwrapped, it discarded every result already accumulated in memory.
- `resume` defaults to **False** and only the CLI opts in. The web path must never resume — a resubmitted job against a stale ledger would come back empty and look like a silent no-op.

### 11. A dependency is declared in exactly one file
- `setup.py` **reads** `requirements.txt` / `-dev` / `-full` / `-automation` rather than restating them. While two independent copies existed they drifted: `requirements.txt` carried thirteen packages nothing imported, while `setup.py` alone declared `pdf2image` — so the documented OCR install still left `extract_text_ocr()` broken.
- Add a dependency to the file matching how it is used, never to `setup.py`:
  - `requirements.txt` — imported at module level, or used as an explicit pandas engine (`openpyxl`, `pyarrow`). This is what the Docker image and CI install.
  - `requirements-full.txt` → extra `full`, `requirements-automation.txt` → extra `automation`. Both hold **lazy** imports that also need a system binary; record that binary in the trailing `# apt:` comment.
  - `requirements-dev.txt` → extra `dev`. Its `-r requirements.txt` line is skipped by the parser on purpose, because extras are additive to `install_requires`.
- Every spec carries a ceiling at the next major above the version this repo is verified against. A floor alone is how a routine `pip install -U` turns into a breaking change.
- Lazy import is **not** the same as optional-to-install. `akshare` and `supabase` are imported inside functions but stay in `requirements.txt`, because the web UI can send `financial_data_source: "akshare"` and a default image must be able to serve that request.
- `python_requires` is `>=3.11`, matching `mypy.ini`'s target and the `python:3.12-slim` image; `pandas>=3` and `numpy>=2.4` require 3.11 anyway. `find_packages()` is restricted to `src`/`api` because `tests/` has an `__init__.py` and used to ship as an importable top-level package.

### 12. `make check` is the gate, and CI runs it
- `make lint` = `flake8 src/ api/ tests/` + `mypy --config-file mypy.ini src/ api/`. `api/` is in both deliberately: it was outside the lint and type-check targets while being the most-changed package in the repo.
- Settings live in `.flake8` (120-char limit) and `mypy.ini`. Without `.flake8`, bare `flake8` scored 352 violations against the 79-char default, so the lint target had never passed and could not gate anything.
- `mypy.ini` sets `namespace_packages = False`. The repo-root `supabase/` directory holds only migration SQL, but with namespace resolution on mypy reads it as a module named `supabase` and reports a false `has no attribute "create_client"` whenever the wheel is absent. Do not re-enable it.
- Test imports resolve through the root `conftest.py`, not per-module `sys.path.insert`: pytest prepends a conftest's directory to `sys.path`, and insert-then-import trips flake8 E402.
- `.github/workflows/verify.yml` installs `requirements-dev.txt`, then runs `make check` plus `pip install -e . --no-deps` — nothing else in the gate imports `setup.py`, so that step is the only proof the packaging metadata still parses.

## General style

- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't comment what well-named code already says. Comments explain *why*, not *what*.
- Don't write backwards-compat shims for code that hasn't been released — this is a research demo, breaking changes are fine.
- New behaviour ships with tests. The api/ tests stub `FinancialAnalysisPipeline` to keep the suite fast.
