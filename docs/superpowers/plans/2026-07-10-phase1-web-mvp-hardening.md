# CNINFO Web MVP Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the CNINFO web workflow safe for a private research deployment, reconnect-safe, and accurate about the text it analyzes.

**Architecture:** Keep the current FastAPI single-job worker and Cloudflare Pages proxy. Cloudflare Access protects the Pages route before the proxy can inject the backend token; the API exposes only browser-safe job information. SSE events receive immutable job-level IDs when published, so native EventSource resumes without duplicate logs. PDF parsing distinguishes a missing MD&A section from a valid extracted section.

**Tech Stack:** Python 3.12, FastAPI, sse-starlette, pytest; React 19, TypeScript, Vite, Vitest; Cloudflare Pages and Cloudflare Access.

## Global Constraints

- Phase 1 retains the existing single-job, in-memory registry; Redis, durable job state, object storage, and CSV upload are Phase 2 work.
- Browser clients must never receive `API_TOKEN`, CNINFO cookies, Python tracebacks, or server filesystem paths.
- `EventSourceResponse(..., ping=15)` remains the only heartbeat mechanism.
- SSE event IDs are assigned exactly once per job and never derived from a reconnect's replay position.
- A missing MD&A heading must analyze the full report and persist `analysis_text_source=full_text`.
- Backend changes ship with pytest coverage; frontend changes ship with a focused Vitest test plus `npm run lint` and `npm run build`.

---

## File Structure

### Backend repository: `cninfo-financial-analyzer`

- Modify `api/runner.py` — immutable SSE event envelopes, safe error publication.
- Modify `api/main.py` — `Last-Event-ID` replay and browser-safe job snapshots.
- Modify `src/pdf_parser.py` — explicit no-MD&A result.
- Modify `tests/test_api.py` — replay, ID, redaction, and snapshot tests.
- Modify `tests/test_pdf_parser.py` and `tests/test_pipeline.py` — MD&A fallback provenance tests.
- Create `.github/workflows/verify.yml` — Python verification on pushes and pull requests.
- Create `docs/production-access.md` — exact private deployment and rate-limit procedure.

### Frontend repository: `cninfo-analyzer-web`

- Create `src/lib/sse.ts` — small, testable event-ID dedupe helper.
- Create `src/lib/sse.test.ts` — dedupe unit tests.
- Modify `src/components/StreamView.tsx` — consume `MessageEvent.lastEventId` and ignore duplicate events.
- Modify `src/lib/types.ts`, `src/App.tsx`, and `src/components/ResultCard.tsx` — remove server-path display and use `result_ready`.
- Modify `package.json` and `package-lock.json` — add a `test` script and Vitest dev dependency.
- Create `.github/workflows/verify.yml` — frontend test, lint, build, and production-dependency audit.
- Modify `README.md` — document the private-access prerequisite.

---

### Task 1: Add immutable SSE event IDs in the backend

**Files:**
- Modify: `api/runner.py:45-150`
- Modify: `api/main.py:223-269`
- Test: `tests/test_api.py`

**Interfaces:**
- Produces: `EventEnvelope(id: int, payload: dict[str, Any])` for history and subscriber queues.
- Consumes: browser `Last-Event-ID` header; absent or invalid values mean `0`.
- Produces: every SSE message has an `id:` equal to its immutable `EventEnvelope.id`.

- [ ] **Step 1: Write failing backend replay tests**

Add tests that publish three envelopes, reconnect with `Last-Event-ID: 2`, and assert only event ID `3` is replayed. Add a test with a history deque whose IDs are `[1001, 1002]`; assert replay preserves those values rather than renumbering them to `1, 2`.

```python
def test_stream_replays_only_events_newer_than_last_event_id(client):
    job = _completed_job_with_event_ids(client, [1, 2, 3])
    response = client.get(
        f"/jobs/{job.id}/stream", headers={"Last-Event-ID": "2"}
    )
    assert _sse_ids(response.text) == [3]
```

- [ ] **Step 2: Run the focused test and confirm the current branch fails**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_stream_replays_only_events_newer_than_last_event_id -q`

Expected: FAIL because `stream_job` currently emits a complete replay and does not read `Last-Event-ID`.

- [ ] **Step 3: Introduce a job-level envelope and assign IDs inside `_publish`**

Use an immutable wrapper and make queues carry wrappers, not bare payloads:

```python
@dataclass(frozen=True)
class EventEnvelope:
    id: int
    payload: Dict[str, Any]

@dataclass
class JobState:
    history: Deque[EventEnvelope] = field(default_factory=lambda: deque(maxlen=HISTORY_CAP))
    subscribers: List[asyncio.Queue[EventEnvelope]] = field(default_factory=list)
    next_event_id: int = 1

async def _publish(job: JobState, payload: Dict[str, Any]) -> None:
    envelope = EventEnvelope(id=job.next_event_id, payload=payload)
    job.next_event_id += 1
    job.history.append(envelope)
    for queue in list(job.subscribers):
        queue.put_nowait(envelope)
```

Keep the existing queue-full behavior, but evict and reinsert the `EventEnvelope` rather than the payload.

- [ ] **Step 4: Replay only unseen immutable envelopes**

Add `request: Request` to `stream_job`, parse the header defensively, and use envelope IDs in both the snapshot and live path:

```python
def _last_event_id(request: Request) -> int:
    try:
        return max(0, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        return 0

for envelope in snapshot:
    if envelope.id <= last_event_id:
        continue
    yield {
        "id": str(envelope.id),
        "event": envelope.payload.get("type", "log"),
        "data": json.dumps(envelope.payload, ensure_ascii=False),
    }
```

Use the same `envelope.id` for events dequeued after subscription. Do not calculate IDs from `enumerate()` or a per-connection counter.

- [ ] **Step 5: Eliminate the shutdown coroutine warning**

Add a focused regression test that makes `asyncio.run_coroutine_threadsafe` raise `RuntimeError` after `_enqueue` has created its coroutine, forces garbage collection, and treats `RuntimeWarning` as an error:

```python
def test_enqueue_closes_coroutine_when_scheduling_is_rejected(monkeypatch):
    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", _raise_runtime_error)
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        _enqueue(_open_loop_stub(), JobState(id="job", spec=_spec()), {"type": "log"})
        gc.collect()
```

Create the coroutine before scheduling, then close it in the exception path:

```python
coro = _publish(job, payload)
try:
    asyncio.run_coroutine_threadsafe(coro, loop)
except RuntimeError:
    coro.close()
```

Retain the existing early `loop.is_closed()` branch, which appends the terminal event to history when possible.

- [ ] **Step 6: Verify the API suite**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`

Expected: PASS, including existing heartbeat, replay, result-binding, and new immutable-ID tests.

- [ ] **Step 7: Commit the backend SSE contract**

```bash
git add api/runner.py api/main.py tests/test_api.py
git commit -m "fix: make SSE replay ids immutable"
```

### Task 2: Remove browser-visible secrets, traces, and server paths

**Files:**
- Modify: `api/runner.py:252-260`
- Modify: `api/main.py:202-220`
- Modify: `tests/test_api.py`
- Modify: `src/lib/types.ts`, `src/App.tsx`, `src/components/ResultCard.tsx`

**Interfaces:**
- Produces: `GET /jobs/{id}` field `result_ready: bool`; it no longer returns `result_path`.
- Produces: `done` SSE event `{ "type": "done", "rows": int }`.
- Produces: `error` SSE event `{ "type": "error", "error": str }`; traceback is logged server-side only.

- [ ] **Step 1: Write redaction tests**

```python
def test_job_snapshot_hides_result_path_and_error_trace(client):
    job = _run_failing_job(client)
    snapshot = client.get(f"/jobs/{job}/").json()
    assert "result_path" not in snapshot
    assert "Traceback" not in snapshot["error"]
```

Also assert that the completed SSE payload contains `rows` but not `result_path`, and that an error SSE payload contains no `trace` key.

- [ ] **Step 2: Run the tests and confirm they fail on the current contract**

Run: `.venv/bin/python -m pytest tests/test_api.py -k 'redaction or result_path' -q`

Expected: FAIL because the current API returns `result_path` and publishes `trace`.

- [ ] **Step 3: Replace server paths with readiness and log exceptions privately**

```python
# api/main.py snapshot
"result_ready": job.status == "done" and bool(job.result_path),

# api/runner.py exception path
logger.exception("CNINFO analysis job failed")
_enqueue(loop, job, {"type": "error", "error": job.error})
```

Keep `job.result_path` internal because `/jobs/{id}/result` still needs it to locate the file.

- [ ] **Step 4: Update the frontend contract**

Replace `result_path` in `JobSnapshot` with `result_ready`. Remove `resultPath` from `FinalState` and `ResultCard` props. The completed card should display only the observation count and the download button.

```ts
export interface JobSnapshot {
  id: string;
  status: JobStatus;
  result_ready: boolean;
  // remaining safe fields only
}
```

- [ ] **Step 5: Run focused backend and frontend checks**

Run: `.venv/bin/python -m pytest tests/test_api.py -q`

Expected: PASS.

Run: `npm run lint && npm run build`

Expected: both commands exit 0.

- [ ] **Step 6: Commit the browser-safe API contract**

```bash
git add api/runner.py api/main.py tests/test_api.py
git commit -m "fix: redact internal job details from API"
git -C ../cninfo-analyzer-web-phase1 add src/lib/types.ts src/App.tsx src/components/ResultCard.tsx
git -C ../cninfo-analyzer-web-phase1 commit -m "fix: consume browser-safe job status"
```

### Task 3: Deduplicate EventSource messages in the frontend

**Files:**
- Create: `src/lib/sse.ts`
- Create: `src/lib/sse.test.ts`
- Modify: `src/components/StreamView.tsx`
- Modify: `package.json`, `package-lock.json`

**Interfaces:**
- Produces: `shouldAppendEvent(seen: Set<string>, id: string): boolean`.
- Consumes: `MessageEvent.lastEventId` from the native EventSource API.
- Produces: duplicate events never enter React state; events without an ID remain visible for compatibility.

- [ ] **Step 1: Add Vitest and a test command**

Add `vitest` to `devDependencies` and this script:

```json
"test": "vitest run"
```

- [ ] **Step 2: Write the helper test**

```ts
import { describe, expect, it } from "vitest";
import { shouldAppendEvent } from "./sse";

it("keeps one event for each immutable SSE id", () => {
  const seen = new Set<string>();
  expect(shouldAppendEvent(seen, "8")).toBe(true);
  expect(shouldAppendEvent(seen, "8")).toBe(false);
  expect(shouldAppendEvent(seen, "9")).toBe(true);
});
```

- [ ] **Step 3: Run the test and confirm it fails before implementation**

Run: `npm test -- src/lib/sse.test.ts`

Expected: FAIL because `src/lib/sse.ts` does not exist.

- [ ] **Step 4: Implement the minimal helper and wire it into `StreamView`**

```ts
export function shouldAppendEvent(seen: Set<string>, id: string): boolean {
  if (!id) return true;
  if (seen.has(id)) return false;
  seen.add(id);
  return true;
}
```

In the SSE handler, call `shouldAppendEvent(seenIds.current, raw.lastEventId)` before `push(data)`. Keep `seenIds` in a `useRef(new Set<string>())`, so reconnects do not reset it while the component remains mounted.

- [ ] **Step 5: Verify frontend checks**

Run: `npm test && npm run lint && npm run build`

Expected: all commands exit 0.

- [ ] **Step 6: Commit the frontend reconnect behavior**

```bash
git add src/lib/sse.ts src/lib/sse.test.ts src/components/StreamView.tsx package.json package-lock.json
git commit -m "fix: deduplicate replayed SSE events"
```

### Task 4: Preserve MD&A provenance during PDF analysis

**Files:**
- Modify: `src/pdf_parser.py:198-221,436-491`
- Modify: `src/pipeline.py:64-94`
- Test: `tests/test_pdf_parser.py`, `tests/test_pipeline.py`

**Interfaces:**
- Produces: `extract_mda_section(text: str) -> Optional[str]`; `None` means no valid MD&A was found.
- Produces: `parse_result["mda_text"] == ""` when no MD&A exists.
- Produces: `select_analysis_text(...) == (full_text, "full_text")` in that case.

- [ ] **Step 1: Add a no-MD&A regression test**

```python
def test_missing_mda_keeps_mda_text_empty_and_uses_full_text(parser, pipeline):
    full_text = "年度报告正文。这里没有管理层讨论章节。" * 100
    assert parser.extract_mda_section(full_text) is None
    text, source = pipeline.select_analysis_text({"text": full_text, "mda_text": ""})
    assert (text, source) == (full_text, "full_text")
```

- [ ] **Step 2: Run the test and confirm it fails**

Run: `.venv/bin/python -m pytest tests/test_pdf_parser.py tests/test_pipeline.py -k missing_mda -q`

Expected: FAIL because the parser currently returns `full_text` as `mda_text`.

- [ ] **Step 3: Change the parser contract and retain the existing pipeline fallback**

```python
def extract_mda_section(self, text: str) -> Optional[str]:
    # return the first validated candidate
    ...
    logger.warning("MD&A section not found")
    return None

mda_text = self.extract_mda_section(text)
result["mda_text"] = mda_text or ""
```

Do not change `select_analysis_text`'s behavior for valid short or table-of-contents MD&A text; it should continue to choose `full_text` with an explicit source when guardrails reject the candidate.

- [ ] **Step 4: Verify parser and pipeline suites**

Run: `.venv/bin/python -m pytest tests/test_pdf_parser.py tests/test_pipeline.py -q`

Expected: PASS, including the new provenance regression test.

- [ ] **Step 5: Commit the data-lineage correction**

```bash
git add src/pdf_parser.py src/pipeline.py tests/test_pdf_parser.py tests/test_pipeline.py
git commit -m "fix: preserve full-text fallback provenance"
```

### Task 5: Require private access and automate verification

**Files:**
- Create: `docs/production-access.md`
- Create: `.github/workflows/verify.yml` in both repositories
- Modify: `README.md` in both repositories

**Interfaces:**
- Produces: a deployment runbook requiring Cloudflare Access on `/api/proxy/*` before Pages deployment.
- Produces: required CI checks named `backend-verify` and `frontend-verify`.

- [ ] **Step 1: Document the exact production gate**

In `docs/production-access.md`, require these deployment steps in order:

1. Create a Cloudflare Access self-hosted application for the Pages custom domain.
2. Add an allow policy for the intended email identities or identity-provider group.
3. Protect the Pages route `https://<pages-domain>/api/proxy/*` before adding `API_TOKEN` and `API_BASE` secrets.
4. Add a Cloudflare rate-limit rule for `POST /api/proxy/jobs`: 5 requests per 10 minutes per IP; block for 10 minutes when exceeded.
5. Verify unauthenticated access receives the Access challenge and an allowed identity can create exactly one backend job.

State explicitly that a Pages secret alone authenticates the proxy to FastAPI; it does not authorize browser users.

- [ ] **Step 2: Add backend CI**

Create `.github/workflows/verify.yml`:

```yaml
name: backend-verify
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: python -m pip install --upgrade pip
      - run: python -m pip install -r requirements.txt
      - run: python -m pytest tests -q
```

- [ ] **Step 3: Add frontend CI**

Create `.github/workflows/verify.yml`:

```yaml
name: frontend-verify
on: [push, pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: 24, cache: npm }
      - run: npm ci
      - run: npm test
      - run: npm run lint
      - run: npm run build
      - run: npm audit --omit=dev --audit-level=high
```

- [ ] **Step 4: Update Vite and refresh the lockfile**

Run: `npm update vite --save-dev`

Then run: `npm audit --json`

Expected: no high-severity vulnerable production dependency; record any remaining development-only advisory in the pull request description.

- [ ] **Step 5: Verify locally and commit documentation/CI**

Run in backend: `.venv/bin/python -m pytest tests -q`

Run in frontend: `npm test && npm run lint && npm run build && npm audit --omit=dev --audit-level=high`

Expected: all commands exit 0.

```bash
git add docs/production-access.md .github/workflows/verify.yml README.md
git commit -m "ci: verify backend and private deployment prerequisites"
git -C ../cninfo-analyzer-web-phase1 add .github/workflows/verify.yml README.md package.json package-lock.json
git -C ../cninfo-analyzer-web-phase1 commit -m "ci: verify frontend build and production dependencies"
```

## Self-Review

- Spec coverage: Task 1 fixes replay identity; Task 2 removes the proxy's browser-visible sensitive details; Task 3 provides client-side defense-in-depth; Task 4 fixes analytical provenance; Task 5 establishes the access gate and repeatable verification.
- Explicitly deferred: durable jobs/results, cancellation, multi-worker execution, object storage, expensive PDF extraction optimization, and custom financial-data upload. These belong to a separate Phase 2 design because each changes the system's operational model.
- The plan specifies all implementation steps, interfaces, tests, and verification commands.
- Type consistency: `EventEnvelope.id` is an integer in the backend and a string in browser `lastEventId`; `result_ready` replaces every browser use of `result_path`.
