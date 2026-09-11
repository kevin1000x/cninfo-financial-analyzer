"""Mount the finaudit answering service onto this app under /audit/*.

Why this file exists
--------------------
finaudit is a separate project with its own repository, tests and release
gates. It is *deployed* here, not developed here: in mid-2026 Hugging Face
stopped issuing new Docker Spaces to free accounts, and this Space predates
that change, so it is the only container this account can run. Sharing it
costs less than standing up a second host, and the sharing is shallow --
one container, two independent request paths.

What is shared is the container, not the codebase
-------------------------------------------------
finaudit's source is never committed to this repository. `finaudit/` holds a
placeholder; the deploy snapshot pushed to the Space fills it. Nothing here
imports finaudit at module scope, so a plain clone of this repo builds and
runs exactly as before -- the routes are simply not registered.

That asymmetry is deliberate. This repo stays the authority for cninfo, the
finaudit repo stays the authority for finaudit, and the Space is a snapshot
of both. Vendoring the source here would create a second copy that drifts.

What it costs this image
------------------------
Nothing measurable. finaudit's only third-party import is PyYAML, already a
dependency here, and its four top-level packages (agent, semantic_layer,
service, extractor) collide with nothing this app imports. The payload is
~770 KB of Python and YAML; it parses no PDFs and opens no sockets except
the optional model calls below.

One correction to the sentence above, 2026-09-11: "the one optional model
call" was accurate while /answer was the only route. /verify fans out -- one
request walks up to 24 claims, each of which may make its own model call.
Upstream caps the count; this file keeps every such call off the event loop.
See the note on the /verify handler.

Auth is deliberately *not* shared
---------------------------------
/jobs* is gated by API_TOKEN via `Authorization: Bearer` and is fail-OPEN
when that variable is unset -- anonymous local dev is intended there (see
main.py). /audit/* is gated by FINAUDIT_API_TOKEN via `X-Finaudit-Token` and
is fail-CLOSED: with no token configured the routes are not registered at
all.

The asymmetry is not an oversight. An unauthenticated /jobs call spends CPU;
an unauthenticated /audit call hands out a figure with an evidence chain
attached to it, and the upstream project treats an answer as a record rather
than a response. Refusing to serve is the cheaper failure of the two.

Two headers, not one
--------------------
The service reads its own token from `X-Finaudit-Token` and leaves
`Authorization` untouched, so the same client works with or without a
platform gate in front of the Space. The Cloudflare Pages function that
proxies these routes already sends both headers.
"""

from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse
from loguru import logger
from starlette.concurrency import run_in_threadpool

PREFIX = "/audit"
TOKEN_ENV = "FINAUDIT_API_TOKEN"


def mount_audit_routes(app: FastAPI) -> bool:
    """Register /audit/* on `app`. Returns whether it did.

    Every reason for not registering is logged with the reason, because the
    symptom is identical in all of them -- a 404 -- and "payload missing"
    and "token unset" need different fixes.
    """
    try:
        from service.api import (
            answer_endpoint,
            authorized,
            coverage_endpoint,
            presented_token,
            verify_endpoint,
        )
    except ImportError as exc:
        logger.info(f"finaudit payload not present; {PREFIX}/* disabled ({exc})")
        return False

    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        logger.warning(
            f"{TOKEN_ENV} is unset; {PREFIX}/* NOT registered. "
            "This is fail-closed on purpose: an open answering endpoint is worse "
            "than a missing one."
        )
        return False

    router = APIRouter(prefix=PREFIX, tags=["audit"])

    def _denied(request: Request):
        """None when the caller may proceed, else the 401 to return."""
        # `authorized` compares with hmac.compare_digest upstream; do not
        # reimplement the comparison here.
        if authorized(presented_token(request.headers.get), token):
            return None
        return JSONResponse(
            {"detail": "missing or wrong X-Finaudit-Token"}, status_code=401
        )

    @router.get("/coverage")
    async def coverage(request: Request):
        denied = _denied(request)
        if denied is not None:
            return denied
        code, body = coverage_endpoint()
        return JSONResponse(body, status_code=code)

    @router.post("/answer")
    async def answer(request: Request):
        denied = _denied(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:
            # Upstream turns any non-dict into a 400 with a readable reason;
            # do not pre-judge it here.
            payload = None

        # answer_endpoint is synchronous and may make one blocking HTTPS call
        # (metric-name normalisation, ~1s). Calling it directly from this
        # coroutine would park the whole event loop for that second, which
        # would also stall every /jobs* SSE stream sharing this process.
        code, body = await run_in_threadpool(answer_endpoint, payload)
        return JSONResponse(body, status_code=code)

    @router.post("/verify")
    async def verify(request: Request):
        """Check a pasted conclusion, claim by claim.

        This is the *entry point* upstream now leads with; /answer stays because
        their frozen evaluation suites drive it and swapping it would move the
        ground under their scores.

        Why the threadpool hop matters more here than on /answer
        --------------------------------------------------------
        One /answer call does at most one blocking model call. One /verify call
        splits a paragraph into claims and walks each one -- upstream caps that
        at 24 per request (`agent.verify.MAX_CHECKABLE_CLAIMS`), so the worst
        case is 24 sequential HTTPS calls, not one. Running that on the event
        loop would stall every /jobs* SSE stream in this process for the whole
        duration. The cap lives upstream; the hop lives here; neither alone is
        enough.
        """
        denied = _denied(request)
        if denied is not None:
            return denied
        try:
            payload = await request.json()
        except Exception:
            # Same as /answer: upstream turns any non-dict into a 400 with a
            # readable reason. Do not pre-judge it here.
            payload = None

        code, body = await run_in_threadpool(verify_endpoint, payload)
        return JSONResponse(body, status_code=code)

    app.include_router(router)
    logger.info(
        f"finaudit mounted: GET {PREFIX}/coverage, "
        f"POST {PREFIX}/verify, POST {PREFIX}/answer"
    )
    return True
