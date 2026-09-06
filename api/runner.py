"""
Background runner that wraps FinancialAnalysisPipeline.run_streaming.

Design notes:
- One job at a time. POST /jobs while a job is active raises
  JobAlreadyRunning, which the API translates to HTTP 429. Loguru sinks
  attach to the global root logger, so concurrent jobs would interleave
  each other's logs and corrupt every consumer.
- Per-job event history (capped) + per-connection subscriber queues so
  SSE consumers can disconnect/reconnect (browser refresh, mobile
  switch) and still see what happened.
- result_path comes from pipeline.last_output_file (set inside
  FinancialAnalysisPipeline.save_results), not by scanning data/results
  for the newest xlsx — that scan would mis-bind A's xlsx to job B if
  jobs ran back-to-back.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional
from uuid import uuid4

from loguru import logger


HISTORY_CAP = 1000

# Wall-clock budget for one job. The default covers a MAX_TASKS-sized batch
# (100 reports) with room to spare; without any budget a wedged download or a
# runaway parse holds the single job slot until the server is restarted.
DEFAULT_JOB_TIMEOUT_SECONDS = 10800.0

# How long the pump blocks waiting for worker output before it re-checks the
# deadline. Bounds the overshoot of the timeout to well under a second.
PUMP_POLL_INTERVAL = 0.5


@dataclass
class JobSpec:
    company_codes: List[str]
    years: List[int]
    report_types: List[str] = field(default_factory=lambda: ["annual"])
    financial_data_csv: Optional[str] = None
    financial_data_source: str = "none"
    delete_pdf: bool = True
    save_parsed_text: bool = True


@dataclass
class JobState:
    id: str
    spec: JobSpec
    status: str = "pending"  # pending | running | done | error | cancelled
    history: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=HISTORY_CAP))
    # Total events ever published, used as the SSE `id:` source. History
    # evicts past HISTORY_CAP, so an id derived from a history index would
    # renumber the same event on each reconnect once a job exceeds the cap.
    event_seq: int = 0
    subscribers: List[asyncio.Queue] = field(default_factory=list)
    finished: asyncio.Event = field(default_factory=asyncio.Event)
    result_path: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    # False whenever there is no killable worker — i.e. the thread runner,
    # where a Python thread cannot be stopped mid-parse.
    cancellable: bool = False
    # Set by a cancel request and observed by the pump, which is the only
    # thread that touches the child. Signalling the process from the API
    # thread instead would race the pump on waitpid.
    cancel_requested: bool = False
    # 0 or None disables the watchdog.
    timeout_seconds: Optional[float] = None


class JobAlreadyRunning(RuntimeError):
    """Raised by JobRegistry.reserve when another job is still active."""

    def __init__(self, running_id: str) -> None:
        super().__init__(f"another job is already running: {running_id}")
        self.running_id = running_id


class JobNotCancellable(RuntimeError):
    """Raised by JobRegistry.cancel when the job cannot be interrupted."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class JobRegistry:
    """In-memory single-job registry. Process-local; restart loses state."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobState] = {}
        self._running_id: Optional[str] = None
        self._lock = threading.Lock()

    def reserve(self, spec: JobSpec) -> JobState:
        with self._lock:
            if self._running_id is not None:
                running = self._jobs.get(self._running_id)
                if running and running.status in {"pending", "running"}:
                    raise JobAlreadyRunning(self._running_id)
            job = JobState(id=uuid4().hex, spec=spec)
            self._jobs[job.id] = job
            self._running_id = job.id
            return job

    def release(self, job_id: str) -> None:
        with self._lock:
            if self._running_id == job_id:
                self._running_id = None

    def cancel(self, job: JobState) -> None:
        """Flag a running job for termination.

        Returns as soon as the flag is set. The pump thread notices within one
        poll interval, kills the worker, publishes the terminal events and
        releases the slot — so the caller learns the outcome by reading
        job.status afterwards, not from this call.
        """
        with self._lock:
            if job.status not in {"pending", "running"}:
                raise JobNotCancellable(f"job already finished: status={job.status}")
            if not job.cancellable:
                raise JobNotCancellable(
                    "this job has no interruptible worker "
                    "(JOB_RUNNER_MODE=thread cannot cancel a running job)"
                )
            job.cancel_requested = True

    def get(self, job_id: str) -> Optional[JobState]:
        return self._jobs.get(job_id)

    def running_id(self) -> Optional[str]:
        with self._lock:
            return self._running_id

    def reset_for_tests(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._running_id = None


registry = JobRegistry()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _job_timeout_seconds() -> float:
    """Wall-clock budget for one job; 0 disables the watchdog.

    Read per job rather than at import so an operator can change it without a
    redeploy, and so a malformed value fails the POST /jobs request loudly
    instead of silently disabling the timeout."""
    raw = os.environ.get("JOB_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return DEFAULT_JOB_TIMEOUT_SECONDS
    return max(float(raw), 0.0)


def _terminate(proc) -> None:
    """Stop a worker process, escalating to SIGKILL if SIGTERM is ignored.

    Safe to call twice: multiprocessing swallows ProcessLookupError, so the
    pump's own teardown can race a cancel from the API thread."""
    proc.terminate()
    proc.join(timeout=5)
    if proc.is_alive():
        proc.kill()
        proc.join(timeout=5)


def _stamp(job: JobState, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the job-global monotonic id that becomes the SSE `id:` field.

    The id lives on the payload rather than being counted per connection so
    that a reconnect replaying history reproduces the exact ids the client
    already saw — the invariant frontend dedupe depends on."""
    job.event_seq += 1
    payload["seq"] = job.event_seq
    return payload


async def _publish(job: JobState, payload: Dict[str, Any]) -> None:
    """Append to history and fan out to subscribers. Runs on the loop thread,
    so history append + subscribers iteration is atomic w.r.t. subscribe()."""
    _stamp(job, payload)
    job.history.append(payload)
    for q in list(job.subscribers):
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            try:
                _ = q.get_nowait()
                q.put_nowait(payload)
            except (asyncio.QueueEmpty, asyncio.QueueFull):
                pass
    if payload.get("type") == "eof":
        job.finished.set()


def _enqueue(loop: asyncio.AbstractEventLoop, job: JobState, payload: Dict[str, Any]) -> None:
    """Thread-safe trigger of _publish from the worker thread.
    Silently drops events if the target loop is gone — happens during
    test teardown and on graceful shutdown."""
    if loop.is_closed():
        _stamp(job, payload)
        job.history.append(payload)
        if payload.get("type") == "eof":
            try:
                job.finished.set()
            except RuntimeError:
                pass
        return
    try:
        asyncio.run_coroutine_threadsafe(_publish(job, payload), loop)
    except RuntimeError:
        pass


def _make_loguru_sink(loop: asyncio.AbstractEventLoop, job: JobState):
    def sink(message) -> None:
        record = message.record
        _enqueue(loop, job, {
            "type": "log",
            "level": record["level"].name,
            "message": record["message"],
            "module": record["module"],
            "ts": record["time"].isoformat(),
        })
    return sink


def _load_cninfo_cookies() -> Optional[Dict[str, str]]:
    """Read CNINFO cookies from environment.

    Precedence: CNINFO_COOKIES_FILE (path to JSON) > CNINFO_COOKIES_JSON
    (JSON string). Both unset → returns None and the pipeline runs with
    no auth cookies. Failures raise ValueError with a clear message; the
    worker thread converts that into a job-error event.

    Cookie *values* must never be logged, returned, or echoed back to
    the client. Only the source path and key count may be mentioned.
    """
    file_path = os.environ.get("CNINFO_COOKIES_FILE", "").strip()
    if file_path:
        path = Path(file_path)
        if not path.exists():
            raise ValueError(f"CNINFO_COOKIES_FILE not found: {file_path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"CNINFO_COOKIES_FILE is not valid JSON: {exc.msg}") from None
        if not isinstance(data, dict):
            raise ValueError("CNINFO_COOKIES_FILE must contain a JSON object of name→value")
        cookies = {str(k): str(v) for k, v in data.items()}
        logger.info(f"Loaded {len(cookies)} CNINFO cookies from file")
        return cookies

    json_blob = os.environ.get("CNINFO_COOKIES_JSON", "").strip()
    if json_blob:
        try:
            data = json.loads(json_blob)
        except json.JSONDecodeError as exc:
            raise ValueError(f"CNINFO_COOKIES_JSON is not valid JSON: {exc.msg}") from None
        if not isinstance(data, dict):
            raise ValueError("CNINFO_COOKIES_JSON must decode to a JSON object")
        cookies = {str(k): str(v) for k, v in data.items()}
        logger.info(f"Loaded {len(cookies)} CNINFO cookies from env")
        return cookies

    return None


def _write_companies_csv(codes: List[str]) -> str:
    base = Path("data/_web_jobs")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"companies_{uuid4().hex[:8]}.csv"
    with path.open("w", encoding="utf-8-sig") as fh:
        fh.write("stock_code,company_name\n")
        for code in codes:
            fh.write(f"{code},\n")
    return str(path)


def _write_financial_data_csv(financial_data) -> str:
    base = Path("data/_web_jobs")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"financial_metrics_{uuid4().hex[:8]}.csv"
    financial_data.to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def _resolve_financial_data_csv(spec: JobSpec) -> Optional[str]:
    """Resolve an explicit CSV or generate one from the AKShare cache path."""
    if spec.financial_data_csv:
        return spec.financial_data_csv
    if spec.financial_data_source == "none":
        return None
    if spec.financial_data_source != "akshare":
        raise ValueError(f"unsupported financial_data_source: {spec.financial_data_source}")

    from src.financial_data_sources import (
        AKShareFinancialProvider,
        CachedFinancialDataProvider,
        NullFinancialMetricsStore,
        SupabaseFinancialMetricsStore,
    )

    store = SupabaseFinancialMetricsStore.from_env() or NullFinancialMetricsStore()
    metrics = CachedFinancialDataProvider(
        store=store,
        source=AKShareFinancialProvider(),
    ).load(spec.company_codes, spec.years)
    if metrics.empty:
        raise ValueError("AKShare returned no annual financial metrics for this job")
    return _write_financial_data_csv(metrics)


def run_job(job: JobState, loop: asyncio.AbstractEventLoop) -> None:
    sink_id = None
    job.status = "running"
    job.started_at = _now_iso()
    _enqueue(loop, job, {"type": "status", "status": "running"})

    try:
        from src.pipeline import FinancialAnalysisPipeline

        sink_id = logger.add(
            _make_loguru_sink(loop, job),
            level="INFO",
            format="{message}",
            enqueue=False,
        )

        cookies = _load_cninfo_cookies()
        pipeline = FinancialAnalysisPipeline(cookies=cookies)
        companies_csv = _write_companies_csv(job.spec.company_codes)
        financial_data_csv = _resolve_financial_data_csv(job.spec)

        results = pipeline.run_streaming(
            company_csv=companies_csv,
            years=job.spec.years,
            report_types=job.spec.report_types,
            financial_data_csv=financial_data_csv,
            delete_pdf=job.spec.delete_pdf,
            save_parsed_text=job.spec.save_parsed_text,
        )

        job.result_path = pipeline.last_output_file
        job.status = "done"
        job.finished_at = _now_iso()
        _enqueue(loop, job, {
            "type": "done",
            "rows": int(len(results)) if hasattr(results, "__len__") else 0,
            "result_path": job.result_path,
        })

    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = _now_iso()
        _enqueue(loop, job, {
            "type": "error",
            "error": job.error,
            "trace": traceback.format_exc(),
        })

    finally:
        if sink_id is not None:
            try:
                logger.remove(sink_id)
            except ValueError:
                pass
        _enqueue(loop, job, {"type": "eof"})
        registry.release(job.id)


def start_job(spec: JobSpec) -> JobState:
    """Reserve the single job slot, then execute the job.

    Default execution is an isolated worker PROCESS (spawn) so CPU-heavy
    pdfplumber parsing cannot stall the web event loop via the GIL. The
    parent keeps the registry, SSE history/subscribers and result binding
    in-process; the child only streams small JSON events over a pipe.

    Set JOB_RUNNER_MODE=thread to use the legacy in-process thread (used by
    test fixtures that monkeypatch run_job / src.pipeline, which cannot
    cross a spawn boundary).

    Raises JobAlreadyRunning if another job is currently active."""
    job = registry.reserve(spec)
    loop = asyncio.get_running_loop()
    # Resolve execution helpers via module globals so tests can monkeypatch
    # them (closure-captured references would defeat that).
    import api.runner as _self
    mode = os.environ.get("JOB_RUNNER_MODE", "process")
    if mode == "thread":
        thread = threading.Thread(
            target=_self.run_job,
            args=(job, loop),
            name=f"job-{job.id[:8]}",
            daemon=True,
        )
        thread.start()
        return job
    return _self.start_job_process(job, loop)


def start_job_process(job: JobState, loop: asyncio.AbstractEventLoop) -> JobState:
    """Spawn the worker process and a pump thread that relays its events.

    The pump owns all JobState mutation for the process path, enforces the
    job's wall-clock deadline, converts a dead/crashed worker into a job
    error, and always releases the job slot."""
    import multiprocessing

    from api.worker import run_child

    ctx = multiprocessing.get_context("spawn")
    recv_conn, send_conn = ctx.Pipe(duplex=False)
    spec_dict = {
        name: getattr(job.spec, name)
        for name in job.spec.__dataclass_fields__
    }
    proc = ctx.Process(
        target=run_child,
        args=(spec_dict, send_conn),
        name=f"job-{job.id[:8]}",
        daemon=True,
    )
    job.timeout_seconds = _job_timeout_seconds()
    job.started_at = _now_iso()
    proc.start()
    send_conn.close()  # parent never sends; closing frees the write end
    # Only once the child exists: before this there is nothing to kill.
    job.cancellable = True
    pump = threading.Thread(
        target=_pump_process_events,
        args=(job, loop, recv_conn, proc),
        name=f"pump-{job.id[:8]}",
        daemon=True,
    )
    pump.start()
    return job


def _apply_process_payload(job: JobState, payload: Dict[str, Any]) -> None:
    """Mirror terminal child events into JobState for status polling."""
    event_type = payload.get("type")
    if event_type == "status" and payload.get("status") == "running":
        job.status = "running"
        job.started_at = job.started_at or _now_iso()
    elif event_type == "done":
        job.status = "done"
        job.result_path = payload.get("result_path")
        job.finished_at = _now_iso()
    elif event_type == "error":
        job.status = "error"
        job.error = payload.get("error")
        job.finished_at = _now_iso()


def _pump_process_events(
    job: JobState,
    loop: asyncio.AbstractEventLoop,
    conn,
    proc,
) -> None:
    """Relay worker events to the asyncio loop; guarantee eof + slot release."""
    timeout_seconds = job.timeout_seconds or 0.0
    deadline = time.monotonic() + timeout_seconds if timeout_seconds > 0 else None
    saw_terminal = False
    timed_out = False
    cancelled = False
    try:
        while True:
            # Checked before reading so a cancel lands within one poll interval
            # even while the worker is chattering, not only when it goes quiet.
            if job.cancel_requested:
                cancelled = True
                break
            if deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                break
            # poll rather than recv: a worker wedged in a C-level call emits
            # nothing, and a blocking recv would never re-check either condition.
            if not conn.poll(PUMP_POLL_INTERVAL):
                continue
            payload = conn.recv()  # raises EOFError when the child closes
            _apply_process_payload(job, payload)
            _enqueue(loop, job, payload)
            event_type = payload.get("type")
            if event_type in {"done", "error"}:
                saw_terminal = True
            if event_type == "eof":
                break
    except (EOFError, OSError):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass
        if timed_out or cancelled:
            # The worker is not going to finish on its own, so skip the join
            # grace period a normal exit gets.
            _terminate(proc)
        else:
            proc.join(timeout=30)
            if proc.is_alive():
                _terminate(proc)
        if not saw_terminal and job.status in {"pending", "running"}:
            job.finished_at = _now_iso()
            if timed_out:
                job.status = "error"
                job.error = (
                    f"job exceeded its {timeout_seconds:g}s time limit and was terminated"
                )
                _enqueue(loop, job, {"type": "error", "error": job.error})
            elif cancelled:
                # Not a failure: no error event, so the UI does not paint a
                # user-requested stop with the red failure card.
                job.status = "cancelled"
                job.error = "job cancelled by request"
                _enqueue(loop, job, {"type": "status", "status": "cancelled"})
            else:
                job.status = "error"
                job.error = (
                    f"worker process exited unexpectedly (exitcode={proc.exitcode})"
                )
                _enqueue(loop, job, {"type": "error", "error": job.error})
        _enqueue(loop, job, {"type": "eof"})
        registry.release(job.id)


def subscribe(job: JobState) -> asyncio.Queue:
    """Register a subscriber queue. Must be called on the asyncio loop
    thread (FastAPI handlers always are). Synchronous on purpose so the
    caller can snapshot history + register without an intervening await."""
    q: asyncio.Queue = asyncio.Queue(maxsize=HISTORY_CAP)
    job.subscribers.append(q)
    return q


def unsubscribe(job: JobState, q: asyncio.Queue) -> None:
    try:
        job.subscribers.remove(q)
    except ValueError:
        pass
