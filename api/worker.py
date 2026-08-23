"""
Spawned child-process entrypoint for analysis jobs.

The CPU-heavy pipeline (pdfplumber parsing in particular) used to run in a
worker thread inside the web process. Python's GIL meant a long parse would
freeze the asyncio event loop, so /healthz, GET /jobs/{id} and SSE all
stalled until parsing finished. This module runs the exact same pipeline in
an isolated process; only small JSON events cross the process boundary via
a one-way multiprocessing pipe.

The child inherits the server environment (API_TOKEN, CNINFO cookies,
Supabase service role), so secrets never appear in events or in the browser.

Test hooks (only honored when CNINFO_JOB_TEST_MODE is set explicitly by the
test suite):
  cpu   — spin the CPU for CNINFO_JOB_TEST_CPU_SECONDS, then emit a small
          xlsx; lets tests prove the event loop stays responsive under load.
  crash — exit the child abruptly (os._exit) so tests can verify the parent
          converts a dead worker into a job error and releases the slot.
"""

from __future__ import annotations

import os
import time
import traceback
from pathlib import Path
from uuid import uuid4


def _send(conn, payload: dict) -> None:
    try:
        conn.send(payload)
    except (BrokenPipeError, OSError):
        pass  # parent went away (test teardown / shutdown)


def _cpu_spin_stub(seconds: float) -> tuple[str, int]:
    """Test-only CPU load that mimics a long pdfplumber parse."""
    from loguru import logger

    import pandas as pd

    logger.info("stub: cpu spin starting")
    deadline = time.monotonic() + seconds
    x = 0
    while time.monotonic() < deadline:
        for _ in range(200_000):
            x = (x * 31 + 7) % 999_999_937
    out_dir = Path("data/results")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"master_summary_test_{uuid4().hex[:6]}.xlsx"
    pd.DataFrame([{"stock_code": "600519", "tone_raw": 0.1}]).to_excel(
        out_file, index=False, engine="openpyxl"
    )
    logger.info("stub: cpu spin finished")
    return str(out_file), 1


def run_child(spec_dict: dict, conn) -> None:
    """Entry point of the spawned worker process.

    Sends exactly one terminal event (done or error) followed by eof, then
    closes the pipe. The parent (api.runner._pump_process_events) owns all
    JobState mutation, SSE broadcast and slot release.
    """
    from loguru import logger

    _send(conn, {"type": "status", "status": "running"})

    test_mode = os.environ.get("CNINFO_JOB_TEST_MODE", "")
    if test_mode == "crash":
        os._exit(1)

    def sink(message) -> None:
        record = message.record
        _send(conn, {
            "type": "log",
            "level": record["level"].name,
            "message": record["message"],
            "module": record["module"],
            "ts": record["time"].isoformat(),
        })

    sink_id = logger.add(sink, level="INFO", format="{message}", enqueue=False)

    try:
        if test_mode == "cpu":
            seconds = float(os.environ.get("CNINFO_JOB_TEST_CPU_SECONDS", "3"))
            result_path, rows = _cpu_spin_stub(seconds)
        else:
            from api.runner import (
                JobSpec,
                _load_cninfo_cookies,
                _resolve_financial_data_csv,
                _write_companies_csv,
            )
            from src.pipeline import FinancialAnalysisPipeline

            spec = JobSpec(**spec_dict)
            cookies = _load_cninfo_cookies()
            pipeline = FinancialAnalysisPipeline(cookies=cookies)
            companies_csv = _write_companies_csv(spec.company_codes)
            financial_data_csv = _resolve_financial_data_csv(spec)

            results = pipeline.run_streaming(
                company_csv=companies_csv,
                years=spec.years,
                report_types=spec.report_types,
                financial_data_csv=financial_data_csv,
                delete_pdf=spec.delete_pdf,
                save_parsed_text=spec.save_parsed_text,
            )

            result_path = pipeline.last_output_file
            rows = int(len(results)) if hasattr(results, "__len__") else 0

        _send(conn, {"type": "done", "rows": rows, "result_path": result_path})

    except Exception as exc:
        _send(conn, {
            "type": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(),
        })

    finally:
        try:
            logger.remove(sink_id)
        except ValueError:
            pass
        _send(conn, {"type": "eof"})
        try:
            conn.close()
        except OSError:
            pass
