"""
Background runner that wraps FinancialAnalysisPipeline.run_streaming
and pumps loguru log records into a per-job asyncio.Queue.
"""

from __future__ import annotations

import asyncio
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from loguru import logger


@dataclass
class JobSpec:
    company_codes: List[str]
    years: List[int]
    report_types: List[str] = field(default_factory=lambda: ["annual"])
    financial_data_csv: Optional[str] = None
    delete_pdf: bool = True
    save_parsed_text: bool = True


@dataclass
class JobState:
    id: str
    spec: JobSpec
    status: str = "pending"  # pending | running | done | error
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    result_path: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class JobRegistry:
    """In-memory job registry. Loses state on process restart — good enough for a research demo."""

    def __init__(self) -> None:
        self._jobs: Dict[str, JobState] = {}
        self._lock = threading.Lock()

    def create(self, spec: JobSpec) -> JobState:
        job = JobState(id=uuid4().hex, spec=spec)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Optional[JobState]:
        return self._jobs.get(job_id)


registry = JobRegistry()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _enqueue(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue, payload: Dict[str, Any]) -> None:
    """Thread-safe queue push from the worker thread back to the asyncio loop."""
    asyncio.run_coroutine_threadsafe(queue.put(payload), loop)


def _make_loguru_sink(loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
    def sink(message) -> None:
        record = message.record
        _enqueue(loop, queue, {
            "type": "log",
            "level": record["level"].name,
            "message": record["message"],
            "module": record["module"],
            "ts": record["time"].isoformat(),
        })
    return sink


def _write_companies_csv(codes: List[str]) -> str:
    """Materialize a one-shot company_list CSV under data/_web_jobs/."""
    base = Path("data/_web_jobs")
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"companies_{uuid4().hex[:8]}.csv"
    with path.open("w", encoding="utf-8-sig") as fh:
        fh.write("stock_code,company_name\n")
        for code in codes:
            fh.write(f"{code},\n")
    return str(path)


def run_job(job: JobState, loop: asyncio.AbstractEventLoop) -> None:
    """Worker entry point. Runs in its own thread."""
    sink_id = None
    job.status = "running"
    job.started_at = _now_iso()
    _enqueue(loop, job.queue, {"type": "status", "status": "running"})

    try:
        from src.pipeline import FinancialAnalysisPipeline

        sink_id = logger.add(
            _make_loguru_sink(loop, job.queue),
            level="INFO",
            format="{message}",
            enqueue=False,
        )

        pipeline = FinancialAnalysisPipeline()

        companies_csv = _write_companies_csv(job.spec.company_codes)

        results = pipeline.run_streaming(
            company_csv=companies_csv,
            years=job.spec.years,
            report_types=job.spec.report_types,
            financial_data_csv=job.spec.financial_data_csv,
            delete_pdf=job.spec.delete_pdf,
            save_parsed_text=job.spec.save_parsed_text,
        )

        result_path = _latest_master_summary()
        job.result_path = result_path
        job.status = "done"
        job.finished_at = _now_iso()
        _enqueue(loop, job.queue, {
            "type": "done",
            "rows": int(len(results)) if hasattr(results, "__len__") else 0,
            "result_path": result_path,
        })

    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.finished_at = _now_iso()
        _enqueue(loop, job.queue, {
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
        _enqueue(loop, job.queue, {"type": "eof"})


def _latest_master_summary() -> Optional[str]:
    results_dir = Path("data/results")
    if not results_dir.exists():
        return None
    candidates = sorted(
        results_dir.glob("master_summary_*.xlsx"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else None


def start_job(spec: JobSpec) -> JobState:
    """Register a job and kick off its worker thread."""
    job = registry.create(spec)
    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=run_job,
        args=(job, loop),
        name=f"job-{job.id[:8]}",
        daemon=True,
    )
    thread.start()
    return job
