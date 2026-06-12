import httpx
import asyncio
import logging
from typing import Optional
from config import settings

logger = logging.getLogger("trustguard.self_id")


class SelfIDClient:
    """
    Client for the Self Agent ID REST API.
    Handles registration, identity queries, and verification checks.
    No API key required — uses encrypted session tokens with 30-minute TTL.

    Base URL: https://app.ai.self.xyz/api/agent
    """

    def __init__(self):
        self.base_url = settings.self_api_base_url
        self._client  = httpx.AsyncClient(timeout=15.0)

    @property
    def chain_id(self) -> int:
        """Returns the correct chain ID based on active environment."""
        return 42220 if settings.environment == "mainnet" else 11142220

    # -------------------------------------------------------------------------
    # Query endpoints — no session token required
    # -------------------------------------------------------------------------

    async def get_agent_info(self, agent_id: int) -> Optional[dict]:
        """
        Get full agent details by agentId.
        Returns address, verification status, proof provider,
        credentials, and registration timestamp.

        GET /api/agent/info/{chainId}/{agentId}
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/api/agent/info/{self.chain_id}/{agent_id}",
                timeout=8.0
            )
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.debug(f"get_agent_info({agent_id}) failed: {e}")
            return None

    async def get_agents_by_address(self, address: str) -> list:
        """
        List all agents registered by a human address.
        Useful for finding an agent's agentId from their wallet address.

        GET /api/agent/agents/{chainId}/{address}
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/api/agent/agents/{self.chain_id}/{address}",
                timeout=8.0
            )
            if response.status_code == 200:
                data = response.json()
                return data if isinstance(data, list) else data.get("agents", [])
            return []
        except Exception as e:
            logger.debug(f"get_agents_by_address({address}) failed: {e}")
            return []

    async def verify_agent(self, agent_id: int) -> Optional[dict]:
        """
        Verify an agent's proof-of-human status.
        Returns verification status, proof provider, strength label,
        and sybil metrics.

        GET /api/agent/verify/{chainId}/{agentId}
        """
        try:
            response = await self._client.get(
                f"{self.base_url}/api/agent/verify/{self.chain_id}/{agent_id}",
                timeout=8.0
            )
            if response.status_code == 404:
                return None
            if response.status_code == 200:
                return response.json()
            return None
        except Exception as e:
            logger.debug(f"verify_agent({agent_id}) failed: {e}")
            return None

    async def get_verification_batch(
        self,
        agent_ids:        list[int],
        concurrency:      int = 10,
        delay_between:    float = 0.1,
    ) -> dict:
        """
        Verify multiple agents concurrently with rate limiting.
        Returns dict keyed by agentId with verification data.

        Used by the scoring engine to enrich Self verification status
        for agents that the subgraph has not indexed yet.
        """
        results    = {}
        semaphore  = asyncio.Semaphore(concurrency)

        async def fetch_one(agent_id: int):
            async with semaphore:
                data = await self.verify_agent(agent_id)
                if data:
                    results[agent_id] = data
                await asyncio.sleep(delay_between)

        await asyncio.gather(
            *[fetch_one(aid) for aid in agent_ids],
            return_exceptions=True
        )

        logger.info(
            f"Self verification batch: {len(results)}/{len(agent_ids)} verified"
        )
        return results

    async def get_info_batch(
        self,
        agent_ids:     list[int],
        concurrency:   int   = 10,
        delay_between: float = 0.1,
    ) -> dict:
        """
        Get full info for multiple agents concurrently.
        Returns dict keyed by agentId.
        """
        results   = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch_one(agent_id: int):
            async with semaphore:
                data = await self.get_agent_info(agent_id)
                if data:
                    results[agent_id] = data
                await asyncio.sleep(delay_between)

        await asyncio.gather(
            *[fetch_one(aid) for aid in agent_ids],
            return_exceptions=True
        )

        return results

    # -------------------------------------------------------------------------
    # Registration endpoints — require session flow
    # -------------------------------------------------------------------------

    async def request_ed25519_challenge(self, public_key_b64: str) -> dict:
        """Step 1 of Self registration. Returns a challenge to sign."""
        response = await self._client.post(
            f"{self.base_url}/api/agent/register/ed25519-challenge",
            json={
                "ed25519PublicKey": public_key_b64,
                "network":          settings.self_network
            }
        )
        response.raise_for_status()
        return response.json()

    async def register_ed25519(
        self,
        public_key_b64: str,
        signature_b64:  str,
        challenge_id:   str
    ) -> dict:
        """Step 2 of Self registration. Returns QR code for human scan."""
        response = await self._client.post(
            f"{self.base_url}/api/agent/register",
            json={
                "mode":             "ed25519",
                "ed25519PublicKey": public_key_b64,
                "ed25519Signature": signature_b64,
                "challengeId":      challenge_id,
                "network":          settings.self_network
            }
        )
        response.raise_for_status()
        return response.json()

    async def poll_registration_status(
        self,
        session_token:   str,
        max_attempts:    int = 60,
        interval_seconds: int = 5
    ) -> dict:
        """Poll registration status until confirmed or timeout."""
        for attempt in range(max_attempts):
            response = await self._client.get(
                f"{self.base_url}/api/agent/register/status",
                params={"sessionToken": session_token}
            )
            response.raise_for_status()
            data   = response.json()
            status = data.get("status")

            if status == "completed":
                return data
            if status == "failed":
                raise RuntimeError(f"Self registration failed: {data}")

            await asyncio.sleep(interval_seconds)

        raise TimeoutError("Self registration timed out")

    async def close(self):
        await self._client.aclose()


# Single importable instance
self_id_client = SelfIDClient()