"""Use the real serializer at a frozen second to catch result-file replacement."""

import asyncio
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from api.runner import JobSpec, JobState, run_job
from api.worker import run_child
from src import pipeline as pipeline_module


class PipeCapture:
    def __init__(self):
        self.events = []

    def send(self, payload):
        self.events.append(payload)

    def close(self):
        pass


@pytest.mark.parametrize("runner", ["thread", "child"])
def test_same_second_exports_preserve_both_owners_bytes(monkeypatch, tmp_path, runner):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CNINFO_JOB_TEST_MODE", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_FILE", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_JSON", raising=False)
    monkeypatch.setattr(pipeline_module, "create_timestamp", lambda: "20261009_001000")

    class ExportOnlyPipeline(pipeline_module.FinancialAnalysisPipeline):
        # Skip downloads/parsing while retaining the real save_results method.
        def __init__(self, **kwargs):
            self.config = {"output": {"results_path": "data/results", "format": "excel"}}
            self.last_output_file = None

        def run_streaming(self, *, company_csv, **kwargs):
            results = pd.read_csv(company_csv, dtype={"stock_code": str})
            self.save_results(results)
            return results

    monkeypatch.setattr(pipeline_module, "FinancialAnalysisPipeline", ExportOnlyPipeline)
    paths = []
    loop = asyncio.new_event_loop()
    loop.close()  # run_job publishes synchronously after a loop has closed.
    for code in ("600519", "000001"):
        job = JobState(id=uuid4().hex, spec=JobSpec([code], [2023]), owner_id=str(uuid4()))
        if runner == "thread":
            run_job(job, loop)
            assert job.status == "done", job.error
            result_path = job.result_path
        else:
            capture = PipeCapture()
            run_child(asdict(job.spec), capture, job.id)
            done = [event for event in capture.events if event["type"] == "done"]
            assert len(done) == 1, capture.events
            result_path = done[0]["result_path"]
        path = Path(result_path)
        assert path.parent.name == job.id
        paths.append(path)

    assert paths[0] != paths[1]
    assert paths[0].name == paths[1].name == "master_summary_20261009_001000.xlsx"
    # Read A only AFTER B exports. Without per-job directories both would be B.
    assert pd.read_excel(paths[0], dtype={"stock_code": str})["stock_code"].tolist() == ["600519"]
    assert pd.read_excel(paths[1], dtype={"stock_code": str})["stock_code"].tolist() == ["000001"]
