"""User-boundary tests: authenticate with Auth, then check every job route."""

import sys
import types
from uuid import uuid4

import pytest
import requests
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api import auth
from api import main
from api.audit_mount import mount_audit_routes
from api.runner import JobSpec, registry


ALICE = str(uuid4())
BOB = str(uuid4())
TOKENS = {"alice-session": ALICE, "bob-session": BOB}


class AuthResponse:
    def __init__(self, status, body):
        self.status_code = status
        self.body = body

    def json(self):
        return self.body


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setenv("SUPABASE_PUBLISHABLE_KEY", "publishable-test-key")

    def get_user(url, *, headers, timeout, allow_redirects):
        assert url == "https://project.supabase.co/auth/v1/user"
        assert headers["apikey"] == "publishable-test-key"
        assert timeout == 5 and allow_redirects is False
        user_id = TOKENS.get(headers["Authorization"].removeprefix("Bearer "))
        if user_id:
            return AuthResponse(200, {"id": user_id, "user_metadata": {"user_id": BOB}})
        return AuthResponse(401, {})

    monkeypatch.setattr(auth.requests, "get", get_user)
    monkeypatch.setattr(main, "start_job", lambda spec, owner_id=None: registry.reserve(spec, owner_id))
    registry.reset_for_tests()
    with TestClient(main.app) as test_client:
        yield test_client
    registry.reset_for_tests()


def headers(token="alice-session"):
    return {"Authorization": f"Bearer {token}"}


def test_creation_uses_verified_user_not_client_input(client):
    response = client.post("/jobs", headers={**headers(), "X-User-Id": BOB}, json={
        "company_codes": ["600519"], "years": [2023], "owner_id": BOB,
    })
    assert response.status_code == 200
    assert registry.get(response.json()["job_id"]).owner_id == ALICE


@pytest.mark.parametrize("token", [None, "expired-token", "tampered.jwt.signature", "service-api-key"])
def test_invalid_or_expired_session_cannot_create(client, token):
    response = client.post("/jobs", headers=headers(token) if token else {}, json={
        "company_codes": ["600519"], "years": [2023],
    })
    assert response.status_code == 401
    assert registry.running_id() is None


def test_supabase_mode_never_falls_back_to_legacy(client, monkeypatch):
    monkeypatch.setenv("API_TOKEN", "service-api-key")
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert client.get("/jobs/anything", headers=headers("service-api-key")).status_code == 503


def test_readiness_checks_configuration_and_required_audit(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUDIT", "1")
    monkeypatch.setattr(main.app.state, "audit_enabled", False)
    assert client.get("/readyz").status_code == 503
    monkeypatch.setattr(main.app.state, "audit_enabled", True)
    response = client.get("/readyz")
    assert response.status_code == 200 and response.json()["ready"] is True
    assert "publishable-test-key" not in response.text
    monkeypatch.delenv("SUPABASE_PUBLISHABLE_KEY")
    monkeypatch.delenv("SUPABASE_ANON_KEY", raising=False)
    assert client.get("/readyz").status_code == 503
    monkeypatch.setenv("AUTH_MODE", "unknown-mode")
    assert client.get("/readyz").status_code == 503
    assert client.get("/jobs/anything", headers=headers()).status_code == 503


def test_auth_transport_failure_is_unavailable_not_permission(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise requests.Timeout("private upstream detail")
    monkeypatch.setattr(auth.requests, "get", unavailable)
    response = client.get("/jobs/anything", headers=headers())
    assert response.status_code == 503
    assert "private" not in response.text


@pytest.mark.parametrize("body", [{}, {"id": "not-a-user"}, {"user_metadata": {"id": ALICE}}, None])
def test_malformed_auth_response_does_not_authenticate(client, monkeypatch, body):
    monkeypatch.setattr(auth.requests, "get", lambda *a, **kw: AuthResponse(200, body))
    assert client.get("/jobs/anything", headers=headers()).status_code == 503


@pytest.mark.parametrize("path,method", [("", "get"), ("/stream", "get"), ("/result", "get"), ("/cancel", "post")])
def test_other_user_cannot_access_any_job_route(client, path, method):
    job = registry.reserve(JobSpec(["600519"], [2023]), owner_id=ALICE)
    job.cancellable = True
    response = getattr(client, method)(f"/jobs/{job.id}{path}", headers=headers("bob-session"))
    assert response.status_code == 404
    assert job.cancel_requested is False
    # Unknown jobs and other people's jobs are indistinguishable.
    missing = getattr(client, method)(f"/jobs/{uuid4().hex}{path}", headers=headers("bob-session"))
    assert missing.json() == response.json()


def test_owner_can_read_stream_and_download(client, tmp_path):
    job = registry.reserve(JobSpec(["600519"], [2023]), owner_id=ALICE)
    output = tmp_path / "result.xlsx"
    output.write_bytes(b"owner-result")
    job.status = "done"
    job.result_path = str(output)
    job.finished.set()
    assert client.get(f"/jobs/{job.id}", headers=headers()).status_code == 200
    stream = client.get(f"/jobs/{job.id}/stream", headers=headers())
    assert stream.status_code == 200 and "event: eof" in stream.text
    result = client.get(f"/jobs/{job.id}/result", headers=headers())
    assert result.status_code == 200 and result.content == b"owner-result"


def test_owner_can_cancel(client):
    job = registry.reserve(JobSpec(["600519"], [2023]), owner_id=ALICE)
    job.cancellable = True
    assert client.post(f"/jobs/{job.id}/cancel", headers=headers()).status_code == 202
    assert job.cancel_requested is True


def test_busy_id_only_visible_to_same_owner(client):
    job = registry.reserve(JobSpec(["600519"], [2023]), owner_id=ALICE)
    payload = {"company_codes": ["000001"], "years": [2023]}
    other = client.post("/jobs", headers=headers("bob-session"), json=payload)
    assert other.status_code == 429
    assert job.id not in other.text and "running_job_id" not in other.json()["detail"]
    own = client.post("/jobs", headers=headers(), json=payload)
    assert own.status_code == 429 and own.json()["detail"]["running_job_id"] == job.id


def test_legacy_jobs_not_adopted_by_new_users(client):
    job = registry.reserve(JobSpec(["600519"], [2023]))
    assert client.get(f"/jobs/{job.id}", headers=headers()).status_code == 404


def test_audit_requires_user_and_service_token(client, monkeypatch):
    fake = types.ModuleType("service.api")
    fake.answer_endpoint = fake.verify_endpoint = lambda payload: (200, {"ok": True})
    fake.coverage_endpoint = lambda: (200, {"rows": []})
    fake.presented_token = lambda getter: getter("X-Finaudit-Token")
    fake.authorized = lambda supplied, expected: supplied == expected
    monkeypatch.setitem(sys.modules, "service.api", fake)
    monkeypatch.setenv("FINAUDIT_API_TOKEN", "audit-service-key")
    audit_app = FastAPI()
    assert mount_audit_routes(audit_app)
    service_header = {"X-Finaudit-Token": "audit-service-key"}
    with TestClient(audit_app) as audit_client:
        assert audit_client.post("/audit/verify", json={}, headers=service_header).status_code == 401
        assert audit_client.post("/audit/verify", json={}, headers=headers()).status_code == 401
        assert audit_client.post("/audit/verify", json={}, headers={**headers(), **service_header}).status_code == 200
        monkeypatch.setenv("AUTH_MODE", "legacy")
        assert audit_client.post("/audit/verify", json={}, headers=service_header).status_code == 200
