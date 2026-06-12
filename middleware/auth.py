from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import select, update
from datetime import datetime

from config import settings

PUBLIC_ROUTES = {
    "/.well-known/agent.json",
    "/health",
    "/docs",
    "/openapi.json",
    "/redoc",
}


class RouterAuthMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if path in PUBLIC_ROUTES or path.startswith("/docs") or path.startswith("/redoc"):
            return await call_next(request)

        # Skip auth entirely in debug mode for easier local development
        if settings.debug:
            return await call_next(request)

        api_key = request.headers.get("x-trustguard-api-key")

        if not api_key:
            raise HTTPException(status_code=401, detail="Missing x-trustguard-api-key header")

        # Master key always works
        if api_key == settings.api_key:
            return await call_next(request)

        # Check database for issued keys
        from db.base import AsyncSessionFactory
        from db.models import ApiKey

        async with AsyncSessionFactory() as db:
            result = await db.execute(
                select(ApiKey).where(
                    ApiKey.key == api_key,
                    ApiKey.is_active == True
                )
            )
            key_record = result.scalar_one_or_none()

            if not key_record:
                raise HTTPException(status_code=401, detail="Invalid or revoked API key")

            # Update usage tracking
            key_record.last_used_at  = datetime.utcnow()
            key_record.request_count += 1
            await db.commit()

        return await call_next(request)