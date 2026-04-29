"""
FastAPI entrypoint for the cninfo-analyzer web frontend.

Routes:
  GET  /healthz                   liveness probe (no auth)
  POST /jobs                      create a streaming analysis job
  GET  /jobs/{id}                 job status snapshot
  GET  /jobs/{id}/stream          SSE: log lines + done/error events,
                                  replays history for late connections
  GET  /jobs/{id}/result          download the master_summary xlsx

Auth:
  Set API_TOKEN in the environment to require Authorization: Bearer <token>
  on every /jobs* route. Unset/empty means anonymous (local dev).

Run locally:
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator, model_validator
from sse_starlette.sse import EventSourceResponse

from .runner import (
    JobAlreadyRunning,
    JobSpec,
    registry,
    start_job,
    subscribe,
    unsubscribe,
)


ALLOWED_REPORT_TYPES = {"annual", "semi_annual", "quarterly", "prospectus"}
STOCK_CODE_RE = re.compile(r"^[0-9]{6}$")
MIN_YEAR = 1990
MAX_TASKS = 100
ALLOWED_FIN_CSV_ROOTS = ("examples", "data")


app = FastAPI(title="cninfo-analyzer web API", version="0.2.0")

allowed_origins = os.environ.get(
    "API_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in allowed_origins if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


bearer_scheme = HTTPBearer(auto_error=False)


def require_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Bearer token gate. Reads API_TOKEN at request time so test fixtures
    can flip it via monkeypatch.setenv between requests."""
    expected = os.environ.get("API_TOKEN", "").strip()
    if not expected:
        return
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if creds.credentials != expected:
        raise HTTPException(status_code=403, detail="invalid token")


def _max_year() -> int:
    return datetime.now(timezone.utc).year + 1


class CreateJobRequest(BaseModel):
    company_codes: List[str] = Field(..., min_length=1, max_length=100)
    years: List[int] = Field(..., min_length=1, max_length=20)
    report_types: List[str] = Field(default_factory=lambda: ["annual"])
    financial_data_csv: Optional[str] = None
    delete_pdf: bool = True
    save_parsed_text: bool = True

    @field_validator("company_codes")
    @classmethod
    def _validate_codes(cls, v: List[str]) -> List[str]:
        bad = [c for c in v if not STOCK_CODE_RE.match(c or "")]
        if bad:
            raise ValueError(
                f"invalid stock_code(s) {bad[:5]}; must be exactly 6 digits"
            )
        return v

    @field_validator("years")
    @classmethod
    def _validate_years(cls, v: List[int]) -> List[int]:
        upper = _max_year()
        bad = [y for y in v if y < MIN_YEAR or y > upper]
        if bad:
            raise ValueError(
                f"years out of range [{MIN_YEAR}, {upper}]: {bad[:5]}"
            )
        return v

    @field_validator("report_types")
    @classmethod
    def _validate_report_types(cls, v: List[str]) -> List[str]:
        bad = [t for t in v if t not in ALLOWED_REPORT_TYPES]
        if bad:
            raise ValueError(
                f"invalid report_types {bad}; allowed: {sorted(ALLOWED_REPORT_TYPES)}"
            )
        return v

    @field_validator("financial_data_csv")
    @classmethod
    def _validate_fin_csv(cls, v: Optional[str]) -> Optional[str]:
        if v is None or v == "":
            return None
        candidate = Path(v)
        if candidate.is_absolute():
            raise ValueError("financial_data_csv must be a relative path")
        cwd = Path.cwd().resolve()
        resolved = (cwd / candidate).resolve()
        allowed_roots = [(cwd / root).resolve() for root in ALLOWED_FIN_CSV_ROOTS]
        if not any(_is_within(resolved, root) for root in allowed_roots):
            raise ValueError(
                f"financial_data_csv must live under {list(ALLOWED_FIN_CSV_ROOTS)}"
            )
        if not resolved.exists():
            raise ValueError(f"financial_data_csv not found: {v}")
        return str(resolved)

    @model_validator(mode="after")
    def _validate_total_tasks(self):
        total = len(self.company_codes) * len(self.years) * len(self.report_types)
        if total > MAX_TASKS:
            raise ValueError(
                f"total tasks {total} exceeds limit {MAX_TASKS}"
            )
        return self


def _is_within(child: Path, root: Path) -> bool:
    try:
        child.relative_to(root)
        return True
    except ValueError:
        return False


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/jobs", response_model=CreateJobResponse)
async def create_job(
    req: CreateJobRequest,
    _: None = Depends(require_token),
) -> CreateJobResponse:
    spec = JobSpec(
        company_codes=req.company_codes,
        years=req.years,
        report_types=req.report_types,
        financial_data_csv=req.financial_data_csv,
        delete_pdf=req.delete_pdf,
        save_parsed_text=req.save_parsed_text,
    )
    try:
        job = start_job(spec)
    except JobAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "another job is already running",
                "running_job_id": exc.running_id,
            },
        )
    return CreateJobResponse(job_id=job.id, status=job.status)


@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_token)) -> dict:
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "id": job.id,
        "status": job.status,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "result_path": job.result_path,
        "history_length": len(job.history),
        "spec": {
            "company_codes": job.spec.company_codes,
            "years": job.spec.years,
            "report_types": job.spec.report_types,
        },
    }


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str, _: None = Depends(require_token)):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_source():
        # Snapshot history and register subscriber atomically — no `await`
        # between these two lines, so a concurrent _publish on the loop
        # cannot interleave (asyncio is single-threaded per loop).
        snapshot = list(job.history)
        already_finished = job.finished.is_set()
        queue = None if already_finished else subscribe(job)

        try:
            for payload in snapshot:
                event_type = payload.get("type", "log")
                yield {
                    "event": event_type,
                    "data": json.dumps(payload, ensure_ascii=False),
                }

            if already_finished:
                tail = snapshot[-1] if snapshot else None
                if not tail or tail.get("type") != "eof":
                    yield {"event": "eof", "data": json.dumps({"type": "eof"})}
                return

            assert queue is not None
            while True:
                payload = await queue.get()
                event_type = payload.get("type", "log")
                yield {
                    "event": event_type,
                    "data": json.dumps(payload, ensure_ascii=False),
                }
                if event_type == "eof":
                    return
        finally:
            if queue is not None:
                unsubscribe(job, queue)

    return EventSourceResponse(event_source())


@app.get("/jobs/{job_id}/result")
def download_result(job_id: str, _: None = Depends(require_token)):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done" or not job.result_path:
        raise HTTPException(
            status_code=409, detail=f"job not ready: status={job.status}"
        )

    path = Path(job.result_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="result file missing on disk")

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
