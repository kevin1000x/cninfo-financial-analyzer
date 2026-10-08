"""Validate user sessions at Supabase Auth; keep legacy deployments explicit."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit
from uuid import UUID

import requests
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    # None is the legacy shared principal, never a signed-in user's identity.
    user_id: Optional[str] = None


def auth_mode() -> str:
    mode = os.environ.get("AUTH_MODE", "legacy").strip().lower()
    if mode not in {"legacy", "supabase"}:
        raise HTTPException(status_code=503, detail="invalid AUTH_MODE configuration")
    return mode


def supabase_config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = (os.environ.get("SUPABASE_PUBLISHABLE_KEY") or os.environ.get("SUPABASE_ANON_KEY") or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured") from None
    local_http = parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if not key or not parsed.netloc or (parsed.scheme != "https" and not local_http):
        raise HTTPException(status_code=503, detail="Supabase authentication is not configured")
    return url, key


def auth_configured() -> bool:
    try:
        if auth_mode() == "supabase":
            supabase_config()
    except (HTTPException, ValueError):
        return False
    return True


def require_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Principal:
    if auth_mode() == "legacy":
        expected = os.environ.get("API_TOKEN", "").strip()
        if not expected:
            return Principal()
        if creds is None or not creds.credentials:
            raise HTTPException(status_code=401, detail="missing bearer token")
        if not hmac.compare_digest(creds.credentials, expected):
            raise HTTPException(status_code=403, detail="invalid token")
        return Principal()

    url, key = supabase_config()
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=401, detail="sign in required")

    # Do not trust decoded claims or client user_id. Auth validates JWT signature
    # and expiration on every request; no server session/token cache can outlive it.
    try:
        response = requests.get(
            f"{url}/auth/v1/user",
            headers={"apikey": key, "Authorization": f"Bearer {creds.credentials}"},
            timeout=5,
            allow_redirects=False,
        )
    except requests.RequestException:
        raise HTTPException(status_code=503, detail="authentication service unavailable") from None
    if response.status_code in {401, 403}:
        raise HTTPException(status_code=401, detail="session expired or invalid")
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail="authentication service unavailable")
    try:
        user = response.json()
        user_id = str(UUID(user["id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        raise HTTPException(status_code=503, detail="invalid authentication response") from None
    return Principal(user_id=user_id)


def require_audit_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    # Legacy audit calls use their own X-Finaudit-Token, not the jobs API_TOKEN.
    # In user mode both the user's validated JWT and that service gate are needed.
    if auth_mode() == "supabase":
        require_user(creds)
