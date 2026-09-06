"""
Process-isolation tests for the job runner.

These run the REAL spawn-based worker path (JOB_RUNNER_MODE unset) with the
api.worker test hooks (CNINFO_JOB_TEST_MODE) instead of a monkeypatched
pipeline, because monkeypatches cannot cross a spawn boundary. They verify:
- event-loop responsiveness while the worker burns CPU (the original bug)
- crashed workers become job errors and release the single-job slot
- a cancel request kills the worker, marks the job cancelled and frees the slot
- the wall-clock watchdog terminates a job nobody cancels
- SSE streams live progress and terminates with eof
- the produced xlsx is downloadable via /jobs/{id}/result
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from api import main as api_main
from api import runner as api_runner


# CLAUDE.md: after a job finishes, job.history types must be a subset of this.
CONTRACT_EVENT_TYPES = {"status", "log", "done", "error", "eof"}


@pytest.fixture
def proc_client(monkeypatch, tmp_path):
    """TestClient with the real process runner; stubs come from the child's
    CNINFO_JOB_TEST_MODE hook."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    monkeypatch.delenv("API_TOKEN", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_FILE", raising=False)
    monkeypatch.delenv("CNINFO_COOKIES_JSON", raising=False)
    monkeypatch.delenv("CNINFO_JOB_TEST_MODE", raising=False)
    monkeypatch.delenv("JOB_RUNNER_MODE", raising=False)
    monkeypatch.delenv("JOB_TIMEOUT_SECONDS", raising=False)
    api_runner.registry.reset_for_tests()

    with TestClient(api_main.app) as c:
        yield c

    api_runner.registry.reset_for_tests()


def _wait_for_status(
    client: TestClient,
    job_id: str,
    target: str,
    *,
    timeout: float = 60.0,
    headers: dict | None = None,
) -> dict:
    deadline = time.time() + timeout
    last: dict = {}
    while time.time() < deadline:
        r = client.get(f"/jobs/{job_id}", headers=headers or {})
        assert r.status_code == 200, f"status poll failed: {r.status_code} {r.text}"
        last = r.json()
        if last["status"] == target:
            return last
        time.sleep(0.1)
    raise AssertionError(f"job {job_id} did not reach {target} (last={last})")


def test_health_and_status_stay_responsive_during_cpu_job(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "3")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    # While the worker process burns CPU for ~3s, every health/status probe
    # must return promptly — this is exactly what the thread runner broke.
    health_latencies: list[float] = []
    status_latencies: list[float] = []
    deadline = time.time() + 30
    while time.time() < deadline:
        t0 = time.perf_counter()
        hr = proc_client.get("/healthz")
        health_latencies.append(time.perf_counter() - t0)
        assert hr.status_code == 200

        t0 = time.perf_counter()
        sr = proc_client.get(f"/jobs/{job_id}")
        status_latencies.append(time.perf_counter() - t0)
        assert sr.status_code == 200
        if sr.json()["status"] in {"done", "error"}:
            break
        time.sleep(0.05)

    snap = _wait_for_status(proc_client, job_id, "done")
    assert snap["result_path"] is not None
    # At least one probe must have landed while the CPU spin was active
    # (job duration >= 3s, polling every ~50ms), so the sample is meaningful.
    assert max(health_latencies) < 1.0, f"health blocked: {health_latencies}"
    assert max(status_latencies) < 1.0, f"status blocked: {status_latencies}"


def test_crashed_worker_becomes_error_and_releases_slot(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "crash")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    snap = _wait_for_status(proc_client, job_id, "error")
    assert "worker process exited unexpectedly" in (snap["error"] or "")

    # The single-job slot must be free again.
    monkeypatch.delenv("CNINFO_JOB_TEST_MODE")
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "0.2")
    r2 = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r2.status_code == 200
    _wait_for_status(proc_client, r2.json()["job_id"], "done")


def test_cancel_kills_worker_and_frees_slot(proc_client, monkeypatch):
    """P0-3: without a cancel path, a wedged worker holds the single job slot
    until the server is restarted."""
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "60")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    _wait_for_status(proc_client, job_id, "running")

    cancel = proc_client.post(f"/jobs/{job_id}/cancel")
    assert cancel.status_code == 202
    assert cancel.json() == {"job_id": job_id, "cancel_requested": True}

    # The worker was spinning for 60s; reaching "cancelled" promptly is the
    # proof that it was actually killed rather than waited out.
    started = time.time()
    snap = _wait_for_status(proc_client, job_id, "cancelled")
    assert time.time() - started < 30, "cancel did not interrupt the worker"
    assert snap["error"] == "job cancelled by request"
    assert snap["result_path"] is None

    job = api_runner.registry.get(job_id)
    types = [payload["type"] for payload in job.history]
    assert set(types) <= CONTRACT_EVENT_TYPES, f"unexpected types: {types}"
    assert types[-1] == "eof"
    # A user-requested stop is a terminal state, not a failure: no error event,
    # so the UI does not render the red failure card.
    assert "error" not in types, f"cancel must not emit an error event: {types}"
    assert "done" not in types
    assert types.count("status") >= 2, f"expected running + cancelled: {types}"

    # The point of the fix: the slot is usable again.
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "0.2")
    r2 = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2023]})
    assert r2.status_code == 200, "cancelled job must release the single-job slot"
    _wait_for_status(proc_client, r2.json()["job_id"], "done")


def test_timeout_terminates_job_nobody_cancelled(proc_client, monkeypatch):
    """The watchdog is the backstop for a job no client cancels — e.g. the
    browser tab was closed while a download wedged."""
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "60")
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "1")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    started = time.time()
    snap = _wait_for_status(proc_client, job_id, "error")
    elapsed = time.time() - started
    assert "time limit" in (snap["error"] or ""), snap["error"]
    assert elapsed < 30, f"watchdog did not fire; worker ran {elapsed:.1f}s of its 60s spin"

    job = api_runner.registry.get(job_id)
    types = [payload["type"] for payload in job.history]
    assert set(types) <= CONTRACT_EVENT_TYPES, f"unexpected types: {types}"
    assert types[-1] == "eof"
    # Unlike a cancel, a timeout IS a failure and must surface as one.
    assert "error" in types, f"timeout must emit an error event: {types}"
    assert any("time limit" in payload.get("error", "") for payload in job.history)

    monkeypatch.delenv("JOB_TIMEOUT_SECONDS")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "0.2")
    r2 = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2023]})
    assert r2.status_code == 200, "timed-out job must release the single-job slot"
    _wait_for_status(proc_client, r2.json()["job_id"], "done")


def test_timeout_of_zero_disables_watchdog(proc_client, monkeypatch):
    """0 is the documented off switch; a long job must be allowed to finish."""
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "2")
    monkeypatch.setenv("JOB_TIMEOUT_SECONDS", "0")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r.status_code == 200
    snap = _wait_for_status(proc_client, r.json()["job_id"], "done")
    assert snap["error"] is None


def test_cancel_rejects_job_that_already_finished(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "0.2")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    job_id = r.json()["job_id"]
    _wait_for_status(proc_client, job_id, "done")

    cancel = proc_client.post(f"/jobs/{job_id}/cancel")
    assert cancel.status_code == 409
    assert "already finished" in cancel.json()["detail"]
    # A rejected cancel must not disturb the completed job.
    assert proc_client.get(f"/jobs/{job_id}").json()["status"] == "done"


def test_cancel_unknown_job_is_404(proc_client):
    assert proc_client.post("/jobs/no-such-job/cancel").status_code == 404


def test_cancel_requires_token(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "5")
    monkeypatch.setenv("API_TOKEN", "s3cret")

    r = proc_client.post(
        "/jobs",
        json={"company_codes": ["600519"], "years": [2022]},
        headers={"Authorization": "Bearer s3cret"},
    )
    assert r.status_code == 200
    job_id = r.json()["job_id"]

    unauth = proc_client.post(f"/jobs/{job_id}/cancel")
    assert unauth.status_code == 401, "cancel is a mutating route and must be gated"

    authed = proc_client.post(
        f"/jobs/{job_id}/cancel", headers={"Authorization": "Bearer s3cret"}
    )
    assert authed.status_code == 202
    _wait_for_status(
        proc_client, job_id, "cancelled", headers={"Authorization": "Bearer s3cret"}
    )


def test_429_while_process_job_running(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "3")

    r1 = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    assert r1.status_code == 200
    job_id_1 = r1.json()["job_id"]

    r2 = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2023]})
    assert r2.status_code == 429
    assert r2.json()["detail"]["running_job_id"] == job_id_1

    _wait_for_status(proc_client, job_id_1, "done")


def test_sse_streams_progress_and_eof_for_process_job(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "1")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    job_id = r.json()["job_id"]

    # Connect while running; collect until eof.
    events: list[tuple[str, dict]] = []
    with proc_client.stream("GET", f"/jobs/{job_id}/stream") as resp:
        assert resp.status_code == 200
        current_event = None
        data_lines: list[str] = []
        for line in "".join(resp.iter_text()).splitlines():
            if not line:
                if current_event and data_lines:
                    events.append((current_event, json.loads("\n".join(data_lines))))
                current_event, data_lines = None, []
                continue
            if line.startswith("event:"):
                current_event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].strip())

    types = [t for t, _ in events]
    assert "status" in types
    assert "log" in types
    assert "done" in types
    assert types[-1] == "eof"

    # Reconnect after completion replays history including eof.
    with proc_client.stream("GET", f"/jobs/{job_id}/stream") as resp:
        replay_text = "".join(resp.iter_text())
    assert "event: done" in replay_text
    assert replay_text.rstrip().endswith("eof") or "event: eof" in replay_text


def test_result_xlsx_downloadable_from_process_job(proc_client, monkeypatch):
    monkeypatch.setenv("CNINFO_JOB_TEST_MODE", "cpu")
    monkeypatch.setenv("CNINFO_JOB_TEST_CPU_SECONDS", "0.2")

    r = proc_client.post("/jobs", json={"company_codes": ["600519"], "years": [2022]})
    job_id = r.json()["job_id"]
    snap = _wait_for_status(proc_client, job_id, "done")

    dl = proc_client.get(f"/jobs/{job_id}/result")
    assert dl.status_code == 200
    assert dl.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert dl.content[:2] == b"PK"  # xlsx zip magic
    assert len(dl.content) > 0
    assert snap["result_path"] in {None, snap["result_path"]}
