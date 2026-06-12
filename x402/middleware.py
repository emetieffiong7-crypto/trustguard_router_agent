import json
import base64
import httpx
from typing import Optional
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings


# Routes that require x402 payment from callers
# Key is the path, value is the price in USDC base units
X402_PROTECTED_ROUTES = {
    "/agent/task": settings.x402_price_per_task,
    "/agent/a2a":  settings.x402_price_per_task,
}


class X402ServerMiddleware(BaseHTTPMiddleware):
    """
    x402 server middleware. When an agent calls a protected route
    without a payment header, return a 402 with payment requirements.
    When payment is present, verify it via Thirdweb facilitator before
    allowing the request through.

    Sits in the middleware stack after auth but before route handlers.
    Disabled entirely when X402_ENABLED=false or DEBUG=true.
    """

    FACILITATOR_URL = "https://x402.org/facilitator"

    async def dispatch(self, request: Request, call_next):

        # Skip x402 entirely in debug mode or if disabled
        if settings.debug or not settings.x402_enabled:
            return await call_next(request)

        path = request.url.path

        # Only applies to x402-protected routes
        if path not in X402_PROTECTED_ROUTES:
            return await call_next(request)

        price      = X402_PROTECTED_ROUTES[path]
        payment    = request.headers.get("X-Payment") or \
                     request.headers.get("x-payment")

        # No payment header — return 402 with requirements
        if not payment:
            return self._payment_required_response(price, path)

        # Payment header present — verify with facilitator
        verified = await self._verify_payment(payment, price, path)

        if not verified:
            return JSONResponse(
                status_code=402,
                content={
                    "error":   "Payment verification failed",
                    "message": "The payment provided could not be verified. Please retry with a valid payment."
                }
            )

        # Payment verified — allow request through
        response = await call_next(request)
        return response

    def _payment_required_response(self, price: int, path: str) -> JSONResponse:
        """Return a 402 response with payment requirements."""
        return JSONResponse(
            status_code=402,
            content={
                "error":     "Payment Required",
                "scheme":    "fixed",
                "price":     str(price),
                "currency":  settings.usdc_address,
                "chainId":   settings.celo_chain_id,
                "recipient": settings.router_private_key and
                             __import__("eth_account").Account.from_key(
                                 settings.router_private_key
                             ).address,
                "description": f"Payment required to use TrustGuard {path}",
                "x402Version": 1,
            }
        )

    async def _verify_payment(
        self,
        payment_header: str,
        expected_price: int,
        path: str
    ) -> bool:
        """
        Verify a payment header with the x402 facilitator.
        Falls back to local verification if Thirdweb is not configured.
        """
        if not settings.thirdweb_secret_key:
            # No facilitator configured — do basic local verification
            return self._verify_locally(payment_header, expected_price)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.FACILITATOR_URL}/verify",
                    json={
                        "payment":       payment_header,
                        "expectedPrice": str(expected_price),
                        "currency":      settings.usdc_address,
                        "chainId":       settings.celo_chain_id,
                    },
                    headers={
                        "Authorization": f"Bearer {settings.thirdweb_secret_key}",
                        "Content-Type":  "application/json",
                    }
                )

                if response.status_code == 200:
                    data = response.json()
                    return data.get("isValid", False)

                return False

        except Exception:
            # Facilitator unreachable — fall back to local verification
            return self._verify_locally(payment_header, expected_price)

    def _verify_locally(self, payment_header: str, expected_price: int) -> bool:
        """
        Basic local payment verification when Thirdweb is not available.
        Checks structure and price only — does not verify onchain settlement.
        Use only for development and testing.
        """
        try:
            decoded  = base64.b64decode(payment_header).decode()
            payload  = json.loads(decoded)
            price    = int(payload.get("payload", {}).get("value", 0))
            chain_id = payload.get("payload", {}).get("chainId")

            return (
                price >= expected_price and
                chain_id == settings.celo_chain_id and
                "signature" in payload
            )
        except Exception:
            return False