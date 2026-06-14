# from fastapi import Request, HTTPException
# from starlette.middleware.base import BaseHTTPMiddleware
# from sqlalchemy import select, update
# from datetime import datetime

# from config import settings

# PUBLIC_ROUTES = {
#     "/.well-known/agent.json",
#     "/health",
#     "/docs",
#     "/openapi.json",
#     "/redoc",
# }


# class RouterAuthMiddleware(BaseHTTPMiddleware):

#     async def dispatch(self, request: Request, call_next):
#         path = request.url.path

#         if path in PUBLIC_ROUTES or path.startswith("/docs") or path.startswith("/redoc"):
#             return await call_next(request)

#         # Skip auth entirely in debug mode for easier local development
#         if settings.debug:
#             return await call_next(request)

#         api_key = request.headers.get("x-trustguard-api-key")

#         if not api_key:
#             raise HTTPException(status_code=401, detail="Missing x-trustguard-api-key header")

#         # Master key always works
#         if api_key == settings.api_key:
#             return await call_next(request)

#         # Check database for issued keys
#         from db.base import AsyncSessionFactory
#         from db.models import ApiKey

#         async with AsyncSessionFactory() as db:
#             result = await db.execute(
#                 select(ApiKey).where(
#                     ApiKey.key == api_key,
#                     ApiKey.is_active == True
#                 )
#             )
#             key_record = result.scalar_one_or_none()

#             if not key_record:
#                 raise HTTPException(status_code=401, detail="Invalid or revoked API key")

#             # Update usage tracking
#             key_record.last_used_at  = datetime.utcnow()
#             key_record.request_count += 1
#             await db.commit()

#         return await call_next(request)

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

# Tier 1 — completely public, no auth needed
PUBLIC_ROUTES = {
    "/.well-known/agent.json",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# Routes that are public with GET but protected with POST
PUBLIC_GET_ROUTES = {
    "/discover",
    "/score",
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
    "master":    1000,   # requests per minute for master key
    "api_key":   100,    # requests per minute for issued keys
    "agent":     200,    # requests per minute for Self-verified agents
    "anonymous": 30,     # requests per minute for public routes
}


def _check_rate_limit(identifier: str, tier: str, window_seconds: int = 60) -> bool:
    """
    Check if identifier has exceeded rate limit for their tier.
    Returns True if allowed, False if rate limited.
    Uses a sliding window approach.
    """
    now     = time.time()
    limit   = RATE_LIMITS.get(tier, 30)
    key     = f"{tier}:{identifier}"
    window  = _rate_counters[key]

    # Remove requests outside the window
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

    Header protocol:
        x-self-agent-signature  HMAC-SHA256 of (method + url + body + timestamp)
        x-self-agent-timestamp  ISO 8601 UTC timestamp
        x-self-agent-keytype    "ed25519" (optional)
        x-self-agent-key        public key hex (optional)
    """
    signature = request.headers.get("x-self-agent-signature")
    timestamp = request.headers.get("x-self-agent-timestamp")

    if not signature or not timestamp:
        return None

    try:
        from self_id.client import self_id_client

        # Read body for signature verification
        body = await request.body()
        body_str = body.decode("utf-8") if body else ""

        # Call Self identify endpoint to verify the request
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
    Verify a database-issued API key.
    Returns key record dict if valid and active, None otherwise.
    Also updates last_used_at and request_count.
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
    Tier 2 — API key routes: database-issued key or Self Agent ID signature.
    Tier 3 — Admin routes: master key only.

    Self Agent ID signed requests are treated as Tier 2 automatically
    without needing a separate API key — this is the agent-native auth path.

    Rate limiting is applied per caller identifier.
    Disabled entirely when DEBUG=true for local development.
    """

    async def dispatch(self, request: Request, call_next):

        # Skip all auth in debug mode
        if settings.debug:
            return await call_next(request)

        path   = request.url.path
        method = request.method

        # ---- Tier 1: Public routes ----------------------------------------

        if self._is_public(path, method):
            identifier = request.client.host if request.client else "unknown"
            if not _check_rate_limit(identifier, "anonymous"):
                return JSONResponse(
                    status_code=429,
                    content={
                        "error":   "Rate limit exceeded",
                        "message": "Too many requests. Slow down."
                    }
                )
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

            # Attach agent info to request state for downstream use
            request.state.auth_type   = "self_agent"
            request.state.agent_info  = self_agent
            request.state.is_admin    = False

            logger.info(
                f"Self agent auth: {agent_address} → {path}"
            )
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
                        "Include x-trustguard-api-key header, "
                        "or sign the request with a Self Agent ID credential. "
                        "Get an API key at POST /admin/keys."
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

            request.state.auth_type  = "master"
            request.state.is_admin   = True
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

        # Admin-only routes require master key
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

        request.state.auth_type  = "api_key"
        request.state.key_info   = key_record
        request.state.is_admin   = False

        return await call_next(request)

    def _is_public(self, path: str, method: str) -> bool:
        """Check if this path/method combination is publicly accessible."""
        if path in PUBLIC_ROUTES:
            return True
        if path.startswith("/docs") or path.startswith("/redoc"):
            return True

        # GET on discovery and score is public
        if method == "GET":
            for public_prefix in PUBLIC_GET_ROUTES:
                if path.startswith(public_prefix):
                    return True

        return False

    def _is_admin_route(self, path: str) -> bool:
        """Check if this path requires admin (master key) access."""
        for admin_prefix in ADMIN_ROUTES:
            if path.startswith(admin_prefix):
                return True
        return False