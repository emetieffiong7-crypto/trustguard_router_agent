import httpx
from typing import Any, Optional


class SubgraphClient:
    """
    Thin async GraphQL client for The Graph subgraph queries.
    All query strings live in subgraph/queries.py.
    """

    def __init__(self):
        # Import here to avoid circular import at module load time
        from config import settings
        self.url     = settings.active_subgraph_url
        self._client = httpx.AsyncClient(timeout=15.0)

    def update_url(self) -> None:
        """Call this after environment changes to refresh the URL."""
        from config import settings
        self.url = settings.active_subgraph_url

    async def query(
        self,
        query_string: str,
        variables:    Optional[dict] = None
    ) -> dict:
        """
        Execute a GraphQL query and return the data dict.
        Returns empty dict if subgraph URL is not configured.
        """
        if not self.url:
            return {}

        payload = {"query": query_string}
        if variables:
            payload["variables"] = {
                k: v for k, v in variables.items() if v is not None
            }

        try:
            response = await self._client.post(self.url, json=payload)
            response.raise_for_status()

            body = response.json()

            if "errors" in body:
                # Log but do not crash — return empty so discovery
                # degrades gracefully rather than breaking the API
                import logging
                logging.getLogger("trustguard.subgraph").warning(
                    f"Subgraph query error: {body['errors']}"
                )
                return {}

            return body.get("data", {})

        except Exception as e:
            import logging
            logging.getLogger("trustguard.subgraph").warning(
                f"Subgraph query failed: {e}"
            )
            return {}

    async def close(self) -> None:
        await self._client.aclose()


# Single importable instance — this is what main.py imports
subgraph_client = SubgraphClient()