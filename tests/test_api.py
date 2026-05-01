"""
Focused tests for the api/ FastAPI layer.

These tests exercise the runner end-to-end (loguru sink, history,
subscribe/unsubscribe, eof) but stub out the heavy pipeline so they
finish in milliseconds without hitting CNINFO.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api import runner as api_runner


# --------------------------------------------------------------------- helpers

class FakePipeline:
    """Stand-in for FinancialAnalysisPipeline that emits a couple of log
    lines and writes a small xlsx to data/results, then sets
    last_output_file on itself.

    Class-level `last_init_kwargs` lets tests assert what was passed to
    the constructor (e.g. cookies)."""

    last_init_kwargs: dict = {}

    def __init__(self, *args, **kwargs) -> None:
        FakePipeline.last_init_kwargs = kwargs
        self.last_output_file: str | None = None

    def run_streaming(self, **_kwargs) -> pd.DataFrame:
        from loguru import logger
        logger.info("fake: phase 1 starting")
        logger.info("fake: phase 2 finished")
        out_dir = Path("data/results")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"master_summary_test_{uuid4().hex[:6]}.xlsx"
        df = pd.DataFrame([{"stock_code": "600519", "tone_raw": 0.1}])
        df.to_excel(out_file, index=False, engine="openpyxl")
        self.last_output_file = str(out_file)
        return df


def _wait_for_status(client: TestClient, job_id: str, target: str, *, timeout: float = 20.0, headers=None) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}", headers=headers or {})
        assert r.status_code == 200
        last = r.json()
        if last["status"] == target:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not reach {target} (last={last})")


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    current_event: str | None = None
    current_data: list[str] = []
    for line in text.splitlines():
        if not line:
            if current_event is not None and current_data:
                payload = "\n".join(current_data)
                try:
                    events.append((current_event, json.loads(payload)))
                except json.JSONDecodeError:
                    events.append((current_event, {"raw": payload}))
            current_event = None
            current_data = []
            continue
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            current_data.append(line[len("data:"):].strip())
    return events


# --------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session", autouse=True)
def _warm_imports():
    """Pre-import slow modules once per session so the per-test worker
    thread doesn't pay 5-10s of cold-import cost (pandas/jieba/openpyxl)."""
    import pandas  # noqa: F401
    import openpyxl  # noqa: F401
    pandas.DataFrame([{"x": 1}]).to_excel("/tmp/_warm_xlsx_probe.xlsx", index=False, engine="openpyxl")


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Fresh TestClient with a clean job registry, a stubbed pipeline,
    and a working dir that has examples/ + data/ subtrees so the
    financial_data_csv validator has something to point at."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "examples").mkdir()
    (tmp_path / "data").mkdir()
    (tmp_path / "examples" / "financial_data.csv").write_text(
        "stock_code,year,ROA\n600519,2022,0.5\n", encoding="utf-8"
    )

    monkeypatch.setattr("src.pipeline.FinancialAnalysisPipeline", FakePipeline)
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_FILE", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_JSON", raising=False)
    FakePipeline.last_init_kwargs = {}
    api_runner.registry.reset_for_tests()

    with TestClient(api_main.app) as c:
        yield c

    api_runner.registry.reset_for_tests()


# --------------------------------------------------------------------- tests

def test_healthz_no_auth_required(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_create_job_dev_mode_no_token(client):
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    body = r.json()
    assert "job_id" in body
    assert body["status"] in {"pending", "running"}


def test_token_required_when_set(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cret")

    # No header → 401
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 401

    # Wrong token → 403
    r = client.post(
        "/jobs",
        json={"company_codes": ["600519"], "years": [2022]},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 403

    # Correct token → 200
    r = client.post(
        "/jobs",
        json={"company_codes": ["600519"], "years": [2022]},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200


def test_healthz_never_requires_token(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "s3cret")
    r = client.get("/healthz")
    assert r.status_code == 200


@pytest.mark.parametrize(
    "payload,reason",
    [
        ({"company_codes": ["12345"], "years": [2022]}, "5-digit code"),
        ({"company_codes": ["abcdef"], "years": [2022]}, "letters in code"),
        ({"company_codes": ["6005199"], "years": [2022]}, "7-digit code"),
        ({"company_codes": ["600519"], "years": [1980]}, "year too low"),
        ({"company_codes": ["600519"], "years": [9999]}, "year too high"),
        (
            {"company_codes": ["600519"], "years": [2022], "report_types": ["weekly"]},
            "bad report_type",
        ),
        (
            {"company_codes": ["600519"], "years": [2022], "report_types": ["prospectus"]},
            "prospectus removed from whitelist (config.yaml has no category, downloader has no keywords)",
        ),
        (
            {"company_codes": [f"{i:06d}" for i in range(20)], "years": list(range(2010, 2020)), "report_types": ["annual"]},
            "200 tasks > 100 limit",
        ),
    ],
)
def test_validation_rejects_bad_input(client, payload, reason):
    r = client.post("/jobs", json=payload)
    assert r.status_code == 422, f"{reason}: expected 422, got {r.status_code} {r.text}"


def test_financial_csv_must_be_under_allowed_roots(client):
    # Absolute path → rejected
    r = client.post(
        "/jobs",
        json={
            "company_codes": ["600519"],
            "years": [2022],
            "financial_data_csv": "/etc/passwd",
        },
    )
    assert r.status_code == 422

    # Path outside examples/ + data/ → rejected (../ escape)
    r = client.post(
        "/jobs",
        json={
            "company_codes": ["600519"],
            "years": [2022],
            "financial_data_csv": "../escape.csv",
        },
    )
    assert r.status_code == 422

    # Valid relative under examples/ → accepted
    r = client.post(
        "/jobs",
        json={
            "company_codes": ["600519"],
            "years": [2022],
            "financial_data_csv": "examples/financial_data.csv",
        },
    )
    assert r.status_code == 200


def test_429_when_job_already_running(client, monkeypatch):
    started = []

    def slow_run_job(job, loop):
        started.append(job.id)
        time.sleep(2.0)  # block the slot
        api_runner.registry.release(job.id)

    monkeypatch.setattr(api_runner, "run_job", slow_run_job)

    r1 = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r1.status_code == 200
    job_id_1 = r1.json()["job_id"]

    # While the first one is "running", a second submission must be rejected.
    deadline = time.time() + 1.0
    while not started and time.time() < deadline:
        time.sleep(0.02)

    r2 = client.post("/jobs", json={"company_codes": ["600519"], "years": [2023]})
    assert r2.status_code == 429
    body = r2.json()
    assert body["detail"]["running_job_id"] == job_id_1


def test_completed_job_stream_replays_and_terminates(client):
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    snap = _wait_for_status(client, job_id, "done")
    assert snap["result_path"] is not None
    assert "master_summary_test_" in Path(snap["result_path"]).name
    # Sanity: that file is the one we made, not "the latest in data/results"
    # — we'll also assert /result returns it byte-for-byte below.

    with client.stream("GET", f"/jobs/{job_id}/stream") as resp:
        assert resp.status_code == 200
        chunks = "".join(resp.iter_text())

    events = _parse_sse(chunks)
    types = [t for t, _ in events]
    assert "log" in types, f"expected log replay, got {types}"
    assert types[-1] == "eof", f"stream must end with eof, got {types}"


def test_cookies_file_takes_precedence(client, monkeypatch, tmp_path):
    cookie_file = tmp_path / "cookies.json"
    cookie_file.write_text('{"FROM_FILE": "abc"}', encoding="utf-8")
    monkeypatch.setenv("CNINFO_COOKIES_FILE", str(cookie_file))
    monkeypatch.setenv("CNINFO_COOKIES_JSON", '{"FROM_ENV": "xyz"}')

    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _wait_for_status(client, job_id, "done")

    assert FakePipeline.last_init_kwargs.get("cookies") == {"FROM_FILE": "abc"}


def test_cookies_json_fallback(client, monkeypatch):
    monkeypatch.setenv("CNINFO_COOKIES_JSON", '{"JSESSIONID": "from-env-only"}')

    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _wait_for_status(client, job_id, "done")

    assert FakePipeline.last_init_kwargs.get("cookies") == {"JSESSIONID": "from-env-only"}


def test_cookies_unset_passes_none(client):
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _wait_for_status(client, job_id, "done")

    # Pipeline should be constructed with cookies=None when neither env is set.
    assert FakePipeline.last_init_kwargs.get("cookies") is None


def test_cookies_invalid_json_surfaces_as_job_error(client, monkeypatch):
    monkeypatch.setenv("CNINFO_COOKIES_JSON", "not-json{")

    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    snap = _wait_for_status(client, job_id, "error")
    assert snap["error"] is not None
    assert "CNINFO_COOKIES_JSON" in snap["error"]


def test_heartbeat_does_not_pollute_history(client):
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _wait_for_status(client, job_id, "done")

    job = api_runner.registry.get(job_id)
    types = [event.get("type") for event in job.history]
    assert "ping" not in types, f"history must not contain ping events: {types}"
    assert set(types) <= {"status", "log", "done", "error", "eof"}, (
        f"unexpected event types in history: {types}"
    )


def test_result_path_bound_to_job_not_latest_scan(client, monkeypatch):
    # First job runs to completion via the FakePipeline.
    r = client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_a = r.json()["job_id"]
    snap_a = _wait_for_status(client, job_a, "done")
    path_a = snap_a["result_path"]
    assert path_a is not None

    # A second xlsx appears in data/results AFTER job A finished. If the
    # runner used "latest mtime in data/results" to bind result_path, this
    # would now mis-attribute that file to job A.
    later = Path("data/results") / "master_summary_unrelated.xlsx"
    pd.DataFrame([{"x": 1}]).to_excel(later, index=False, engine="openpyxl")
    os.utime(later, None)

    # Re-fetch job A: result_path must still point at the file produced
    # by the FakePipeline run, not the unrelated newer one.
    r = client.get(f"/jobs/{job_a}")
    assert r.json()["result_path"] == path_a

    # Downloading job A's result returns the original file's bytes.
    r = client.get(f"/jobs/{job_a}/result")
    assert r.status_code == 200
    expected_bytes = Path(path_a).read_bytes()
    assert r.content == expected_bytes
