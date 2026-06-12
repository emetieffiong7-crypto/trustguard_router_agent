import hmac
import hashlib
import base64
from datetime import datetime, timezone
from config import settings


class SelfAgentSigner:
    """
    Signs outbound HTTP requests with Self Agent ID headers.
    Used when TrustGuard makes authenticated requests to other Self-verified agents.

    Header protocol:
        x-self-agent-signature  HMAC-SHA256 of (method + url + body + timestamp)
        x-self-agent-timestamp  ISO 8601 UTC timestamp
        x-self-agent-keytype    "ed25519"
        x-self-agent-key        agent public key hex
    """

    def __init__(self):
        self.private_key = settings.self_agent_private_key
        self.public_key  = settings.self_agent_public_key

    def _is_configured(self) -> bool:
        return bool(self.private_key and self.public_key)

    def sign_request(
        self,
        method: str,
        url: str,
        body: str = ""
    ) -> dict:
        """
        Returns a dict of signed headers to attach to an outbound request.
        Returns empty dict if Self credentials are not yet configured.
        """
        if not self._is_configured():
            return {}

        timestamp = datetime.now(timezone.utc).isoformat()
        message   = f"{method.upper()}{url}{body}{timestamp}"

        private_key_bytes = bytes.fromhex(self.private_key)
        signature         = hmac.new(
            private_key_bytes,
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        return {
            "x-self-agent-signature": signature,
            "x-self-agent-timestamp": timestamp,
            "x-self-agent-keytype":   "ed25519",
            "x-self-agent-key":       self.public_key,
        }


# Single importable instance
self_signer = SelfAgentSigner()