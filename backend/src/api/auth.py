"""
X-API-Key authentication for the CBC API.

CBC runs behind an INTERNAL load balancer today precisely because it has no
authentication. Moving it to the public ALB needs this first: ``/api/analyze``
and ``/api/analyze-file`` each spend OCR.space credits and Gemini tokens, so an
open public endpoint is a live financial exposure, not a theoretical one.

Two decisions here are load-bearing:

1. ``/api/health`` MUST stay public. The ALB health check cannot send custom
   headers, so requiring a key there would mark every target permanently
   unhealthy and take the service down.
2. This FAILS CLOSED. With no keys configured the protected paths return 503
   rather than falling through unauthenticated, so a missing secret degrades to
   "unavailable" instead of "wide open".
"""

from __future__ import annotations

import hmac
import logging
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

logger = logging.getLogger(__name__)

API_KEY_HEADER = "X-API-Key"

# Paths reachable without a key. `/api/health` is here for the ALB (see above);
# the rest are the service banner and the auto-generated docs, which expose
# schema shapes but no credentials and no billable work.
PUBLIC_PATHS = frozenset({"/", "/api/health", "/docs", "/redoc", "/openapi.json"})

# Declared so Swagger UI renders an Authorize button. The middleware below does
# the actual enforcement; auto_error=False keeps this from rejecting a second
# time with a different shape.
api_key_scheme = APIKeyHeader(name=API_KEY_HEADER, auto_error=False)


def _valid_keys() -> frozenset[str]:
    """
    Parse CBC_API_KEYS at call time, not import time.

    Reading per request means a key can be rotated, or a per-caller key added,
    by updating the task definition secret and restarting, with no rebuild. It
    also keeps tests honest: monkeypatching the env var takes effect
    immediately.
    """
    raw = os.getenv("CBC_API_KEYS", "")
    return frozenset(k.strip() for k in raw.split(",") if k.strip())


async def api_key_middleware(request: Request, call_next):
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)

    keys = _valid_keys()
    if not keys:
        logger.error(
            "CBC_API_KEYS is unset; refusing request to %s rather than serving "
            "it unauthenticated.",
            request.url.path,
        )
        return JSONResponse(
            status_code=503,
            content={"detail": "Service not configured for authentication."},
        )

    # compare_digest, not ==, so a wrong key cannot be recovered by timing how
    # long the rejection took. Every candidate is compared, no early exit.
    presented = request.headers.get(API_KEY_HEADER, "")
    if not any(hmac.compare_digest(presented, k) for k in keys):
        # Never log the presented value: rejected keys are still secrets, and
        # they end up in CloudWatch.
        logger.warning("Rejected %s: invalid or missing %s.", request.url.path, API_KEY_HEADER)
        return JSONResponse(
            status_code=401,
            content={"detail": f"Invalid or missing {API_KEY_HEADER}."},
        )

    return await call_next(request)
