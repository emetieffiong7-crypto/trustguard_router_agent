import time
import logging
from collections import defaultdict
from datetime import datetime
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from config import settings

logger = logging.getLogger("trustguard.auth")

# -------------------------------------------------------------------------
# Route tiers
# -------------------------------------------------------------------------

# Exact-path routes that never need auth.
PUBLIC_ROUTES = {
    "/.well-known/agent.json",
    "/health",
    "/docs",
    "/",
    "/openapi.json",
    "/redoc",
}

# GET-only public prefixes.
PUBLIC_GET_ROUTES = {
    "/discover",
    "/score",
}

# Open regardless of method, but rate limited more tightly since they
# either trigger LLM calls or create a resource.
# - /agent/task and /agent/a2a power the public natural-language demo.
# - /admin/keys/register is self-service API key creation.
PUBLIC_OPEN_ROUTES = {
    "/agent/task",
    "/agent/a2a",
    "/admin/keys/register",
}

# Tier 3 — master key only
ADMIN_ROUTES = {
    "/admin",
}

# -------------------------------------------------------------------------
# Simple in-memory rate limiter
# For production replace with Redis
# -------------------------------------------------------------------------

_rate_counters: dict = defaultdict(list)

RATE_LIMITS = {
    "master":                  1000,  # per minute, master key
    "api_key":                 100,   # per minute, issued keys
    "agent":                   200,   # per minute, Self-verified agents
    "anonymous":               30,    # per minute, public GET routes
    "anonymous_agent_task":    10,    # per minute, public /agent/task and /agent/a2a
    "anonymous_key_register":  5,     # per minute, self-service key creation
}


def _check_rate_limit(identifier: str, tier: str, window_seconds: int = 60) -> bool:
    """
    Sliding window rate limit check. Returns True if allowed.
    """
    now    = time.time()
    limit  = RATE_LIMITS.get(tier, 30)
    key    = f"{tier}:{identifier}"
    window = _rate_counters[key]

    _rate_counters[key] = [t for t in window if now - t < window_seconds]

    if len(_rate_counters[key]) >= limit:
        return False

    _rate_counters[key].append(now)
    return True


# -------------------------------------------------------------------------
# Self Agent ID signature verification
# -------------------------------------------------------------------------

async def _verify_self_agent_signature(request: Request) -> dict | None:
    """
    Verify a Self Agent ID signed request.
    Returns agent info dict if valid, None if invalid or absent.
    """
    signature = request.headers.get("x-self-agent-signature")
    timestamp = request.headers.get("x-self-agent-timestamp")

    if not signature or not timestamp:
        return None

    try:
        from self_id.client import self_id_client

        body     = await request.body()
        body_str = body.decode("utf-8") if body else ""

        agent_info = await self_id_client.identify_agent(
            signature = signature,
            timestamp = timestamp,
            method    = request.method,
            url       = str(request.url),
            body      = body_str,
        )

        if agent_info and agent_info.get("valid"):
            return agent_info

        return None

    except Exception as e:
        logger.debug(f"Self agent signature verification failed: {e}")
        return None


# -------------------------------------------------------------------------
# API key verification
# -------------------------------------------------------------------------

async def _verify_api_key(api_key: str) -> dict | None:
    """
    Verify a database-issued API key. Returns key record if valid and active.
    """
    from db.base import AsyncSessionFactory
    from db.models import ApiKey
    from sqlalchemy import select

    async with AsyncSessionFactory() as db:
        result = await db.execute(
            select(ApiKey).where(
                ApiKey.key       == api_key,
                ApiKey.is_active == True
            )
        )
        key_record = result.scalar_one_or_none()

        if not key_record:
            return None

        key_record.last_used_at  = datetime.utcnow()
        key_record.request_count += 1
        await db.commit()

        return {
            "id":    key_record.id,
            "label": key_record.label,
            "key":   api_key,
        }


# -------------------------------------------------------------------------
# Main middleware
# -------------------------------------------------------------------------

class RouterAuthMiddleware(BaseHTTPMiddleware):
    """
    Three-tier authentication middleware.

    Tier 1 — Public routes: no auth required.
        - Includes /agent/task, /agent/a2a, /admin/keys/register, opened
          up so the landing page demo and self-service signup work without
          a key. These get tighter rate limits and request.state.auth_type
          is set to "anonymous" so downstream code can apply safe defaults
          (e.g. skip onchain writes).

    Tier 2 — API key routes: database-issued key or Self Agent ID signature.

    Tier 3 — Admin routes: master key only.

    Disabled entirely when DEBUG=true for local development — in that case
    request.state.auth_type is never set, and routes should treat a missing
    auth_type as a trusted local caller.
    """

    async def dispatch(self, request: Request, call_next):

        if settings.debug:
            return await call_next(request)

        path   = request.url.path
        method = request.method

        # ---- Tier 1: Public routes ----------------------------------------

        if self._is_public(path, method):
            identifier = request.client.host if request.client else "unknown"

            if path in {"/agent/task", "/agent/a2a"}:
                tier = "anonymous_agent_task"
            elif path == "/admin/keys/register":
                tier = "anonymous_key_register"
            else:
                tier = "anonymous"

            if not _check_rate_limit(identifier, tier):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error":   "Rate limit exceeded",
                        "message": "Too many requests. Please slow down and try again shortly."
                    }
                )

            # Mark as anonymous so routes can apply safe defaults,
            # e.g. disable onchain writes for unauthenticated callers.
            request.state.auth_type = "anonymous"
            request.state.is_admin  = False

            return await call_next(request)

        # ---- Try Self Agent ID authentication first -----------------------

        self_agent = await _verify_self_agent_signature(request)
        if self_agent:
            agent_address = self_agent.get("agentAddress", "unknown")

            if not _check_rate_limit(agent_address, "agent"):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Rate limit exceeded for this agent"}
                )

            request.state.auth_type  = "self_agent"
            request.state.agent_info = self_agent
            request.state.is_admin   = False

            logger.info(f"Self agent auth: {agent_address} → {path}")
            return await call_next(request)

        # ---- Check API key header -----------------------------------------

        api_key = (
            request.headers.get("x-trustguard-api-key") or
            request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        )

        if not api_key:
            return JSONResponse(
                status_code=401,
                content={
                    "error":   "Authentication required",
                    "message": (
                        "Include x-trustguard-api-key header, or sign the "
                        "request with a Self Agent ID credential. Get a key "
                        "at POST /admin/keys/register."
                    )
                }
            )

        # ---- Tier 3: Master key -------------------------------------------

        if api_key == settings.api_key:
            if not _check_rate_limit("master", "master"):
                return JSONResponse(
                    status_code=429,
                    content={"error": "Master key rate limit exceeded"}
                )

            request.state.auth_type = "master"
            request.state.is_admin  = True
            return await call_next(request)

        # ---- Tier 2: Database-issued API key ------------------------------

        key_record = await _verify_api_key(api_key)
        if not key_record:
            return JSONResponse(
                status_code=401,
                content={
                    "error":   "Invalid or revoked API key",
                    "message": "This key does not exist or has been revoked."
                }
            )

        if self._is_admin_route(path):
            return JSONResponse(
                status_code=403,
                content={
                    "error":   "Forbidden",
                    "message": "Admin routes require the master API key."
                }
            )

        identifier = str(key_record["id"])
        if not _check_rate_limit(identifier, "api_key"):
            return JSONResponse(
                status_code=429,
                content={
                    "error":   "Rate limit exceeded",
                    "message": "You have exceeded 100 requests per minute."
                }
            )

        request.state.auth_type = "api_key"
        request.state.key_info  = key_record
        request.state.is_admin  = False

        return await call_next(request)

    def _is_public(self, path: str, method: str) -> bool:
        if path in PUBLIC_ROUTES:
            return True
        if path.startswith("/docs") or path.startswith("/redoc"):
            return True
        if path in PUBLIC_OPEN_ROUTES:
            return True
        if method == "GET":
            for prefix in PUBLIC_GET_ROUTES:
                if path.startswith(prefix):
                    return True
        return False

    def _is_admin_route(self, path: str) -> bool:
        for prefix in ADMIN_ROUTES:
            if path.startswith(prefix):
                return True
        return False