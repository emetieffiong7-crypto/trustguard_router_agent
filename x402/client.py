import httpx
import json
import time
from typing import Optional
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3

from config import settings


class X402PaymentError(Exception):
    """Raised when an x402 payment cannot be completed."""
    pass


class X402Client:
    """
    Handles outbound x402 payments when TrustGuard hits a 402 response
    from another agent's endpoint.

    Flow:
    1. Receive a 402 response with payment requirements
    2. Validate the requirements are acceptable
    3. Sign a payment authorisation from the backend wallet
    4. Return the X-Payment header value for the retry request
    """

    # Tokens we are willing to pay with on Celo mainnet
    ACCEPTED_TOKENS = {
        "0xceba9300f2b948710d2653dd7b07f33a8b32118c": "USDC",
        "0x765de816845861e75a25fca122bb6898b8b1282a": "USDm",
    }

    # Maximum we will pay per request without human approval (in token base units)
    # Default: 1 USDC (6 decimals) = 1_000_000
    MAX_AUTO_PAYMENT = 1_000_000

    def __init__(self):
        self.account = Account.from_key(settings.router_private_key)
        self.w3      = Web3()

    def _validate_payment_requirements(self, requirements: dict) -> None:
        """
        Validate that payment requirements from a 402 response are
        acceptable before signing anything.
        """
        chain_id = requirements.get("chainId")
        if chain_id != settings.celo_chain_id:
            raise X402PaymentError(
                f"Unsupported chainId {chain_id}. "
                f"TrustGuard only pays on Celo mainnet ({settings.celo_chain_id})."
            )

        currency = requirements.get("currency", "").lower()
        if currency not in self.ACCEPTED_TOKENS:
            raise X402PaymentError(
                f"Unsupported payment token {currency}. "
                f"Accepted: {list(self.ACCEPTED_TOKENS.values())}"
            )

        price = int(requirements.get("price", 0))
        if price > self.MAX_AUTO_PAYMENT:
            raise X402PaymentError(
                f"Payment requirement {price} exceeds auto-payment "
                f"limit {self.MAX_AUTO_PAYMENT}. Human approval required."
            )

        if price <= 0:
            raise X402PaymentError("Invalid payment price: must be greater than zero.")

    def _build_payment_payload(self, requirements: dict, endpoint_url: str) -> dict:
        """
        Build the signed payment payload that goes in the X-Payment header.
        This follows the x402 protocol specification from Coinbase/Thirdweb.
        """
        price    = int(requirements.get("price", 0))
        currency = requirements.get("currency")
        chain_id = requirements.get("chainId")

        # Build the payment authorisation message
        # Format follows x402 spec: recipient + amount + token + nonce + deadline
        nonce    = int(time.time())
        deadline = nonce + 300  # 5 minute window

        message = {
            "from":     self.account.address,
            "to":       requirements.get("recipient", ""),
            "value":    str(price),
            "token":    currency,
            "chainId":  chain_id,
            "nonce":    nonce,
            "deadline": deadline,
            "endpoint": endpoint_url,
        }

        # Sign the payment message
        message_str    = json.dumps(message, sort_keys=True)
        message_hash   = encode_defunct(text=message_str)
        signed_message = self.account.sign_message(message_hash)

        return {
            "payload":   message,
            "signature": signed_message.signature.hex(),
            "scheme":    requirements.get("scheme", "fixed"),
        }

    async def handle_402_response(
        self,
        response_body: dict,
        endpoint_url: str
    ) -> dict:
        """
        Process a 402 response and return the X-Payment header value.
        Raises X402PaymentError if payment cannot or should not be made.
        """
        self._validate_payment_requirements(response_body)

        payment_payload = self._build_payment_payload(response_body, endpoint_url)

        import base64
        header_value = base64.b64encode(
            json.dumps(payment_payload).encode()
        ).decode()

        return {
            "header_value": header_value,
            "amount":       int(response_body.get("price", 0)),
            "token":        response_body.get("currency"),
            "token_symbol": self.ACCEPTED_TOKENS.get(
                response_body.get("currency", "").lower(), "UNKNOWN"
            )
        }

    async def fetch_with_payment(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        max_retries: int = 2,
        **kwargs
    ) -> httpx.Response:
        """
        Make an HTTP request and automatically handle x402 payment if required.
        Returns the final response after payment if needed.
        Raises X402PaymentError if payment fails or is not acceptable.
        """
        for attempt in range(max_retries):
            response = await client.request(method, url, **kwargs)

            if response.status_code != 402:
                return response

            if attempt == max_retries - 1:
                raise X402PaymentError(
                    f"Still receiving 402 after {max_retries} attempts on {url}"
                )

            try:
                requirements = response.json()
            except Exception:
                raise X402PaymentError(f"Could not parse 402 response from {url}")

            payment = await self.handle_402_response(requirements, url)

            # Add payment header to the next attempt
            headers = kwargs.get("headers", {})
            headers["X-Payment"] = payment["header_value"]
            kwargs["headers"]    = headers

        return response


# Single importable instance
x402_client = X402Client()