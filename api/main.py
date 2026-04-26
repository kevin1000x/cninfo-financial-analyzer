"""
FastAPI entrypoint for the cninfo-analyzer web frontend.

Routes:
  GET  /healthz                   liveness probe
  POST /jobs                      create a streaming analysis job
  GET  /jobs/{id}                 job status snapshot
  GET  /jobs/{id}/stream          SSE: log lines + done/error events
  GET  /jobs/{id}/result          download the master_summary xlsx

Run locally:
  uvicorn api.main:app --reload --port 8000
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from .runner import JobSpec, registry, start_job


app = FastAPI(title="cninfo-analyzer web API", version="0.1.0")

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


class CreateJobRequest(BaseModel):
    company_codes: List[str] = Field(..., min_length=1, max_length=100)
    years: List[int] = Field(..., min_length=1, max_length=20)
    report_types: List[str] = Field(default_factory=lambda: ["annual"])
    financial_data_csv: Optional[str] = None
    delete_pdf: bool = True
    save_parsed_text: bool = True


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.post("/jobs", response_model=CreateJobResponse)
async def create_job(req: CreateJobRequest) -> CreateJobResponse:
    spec = JobSpec(
        company_codes=req.company_codes,
        years=req.years,
        report_types=req.report_types,
        financial_data_csv=req.financial_data_csv,
        delete_pdf=req.delete_pdf,
        save_parsed_text=req.save_parsed_text,
    )
    job = start_job(spec)
    return CreateJobResponse(job_id=job.id, status=job.status)


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
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
        "spec": {
            "company_codes": job.spec.company_codes,
            "years": job.spec.years,
            "report_types": job.spec.report_types,
        },
    }


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")

    async def event_source():
        while True:
            payload = await job.queue.get()
            event_type = payload.get("type", "log")
            yield {"event": event_type, "data": json.dumps(payload, ensure_ascii=False)}
            if event_type == "eof":
                return

    return EventSourceResponse(event_source())


@app.get("/jobs/{job_id}/result")
def download_result(job_id: str):
    job = registry.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    if job.status != "done" or not job.result_path:
        raise HTTPException(status_code=409, detail=f"job not ready: status={job.status}")

    path = Path(job.result_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="result file missing on disk")

    return FileResponse(
        path=str(path),
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
