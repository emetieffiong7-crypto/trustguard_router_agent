# import httpx
# import time
# import json
# from datetime import datetime
# from typing import Optional
# from sqlalchemy.ext.asyncio import AsyncSession
# from sqlalchemy import select, update

# from config import settings
# from onchain.contracts import (
#     record_verification_probe,
#     get_agent_card_uri,
#     get_agent_owner,
#     check_self_verification
# )
# from db.models import Agent, Probe
# from schemas.probe import ProbeResult
# from self_id.signer import self_signer


# async def probe_agent(
#     agent_address: str,
#     agent_id: int,
#     db: AsyncSession,
#     post_onchain: bool = True
# ) -> ProbeResult:
#     """
#     Run a full verification probe against an agent.
#     Checks their agent card, A2A endpoint, x402 support, and Self verification.
#     Posts result onchain if post_onchain is True.
#     """
#     start_time = time.time()
#     evidence_parts = []

#     card_reachable = False
#     a2a_passed     = None
#     x402_passed    = None
#     a2a_endpoint   = None

#     async with httpx.AsyncClient(timeout=settings.probe_http_timeout) as client:

#         # Step 1 — fetch agent card URI from ERC-8004 registry
#         try:
#             card_uri = await get_agent_card_uri(agent_id)
#         except Exception as e:
#             card_uri = None
#             evidence_parts.append(f"card_uri_fetch_failed: {str(e)[:100]}")

#         # Step 2 — fetch and parse agent card JSON
#         if card_uri:
#             try:
#                 card_response = await client.get(card_uri)
#                 card_response.raise_for_status()
#                 card_data      = card_response.json()
#                 card_reachable = True
#                 evidence_parts.append("card_reachable: true")

#                 # Extract endpoints from card
#                 endpoints = card_data.get("endpoints", [])
#                 for ep in endpoints:
#                     if ep.get("type") == "a2a":
#                         a2a_endpoint = ep.get("url")
#                     if ep.get("type") == "x402":
#                         x402_passed = True

#             except Exception as e:
#                 evidence_parts.append(f"card_fetch_failed: {str(e)[:100]}")

#         # Step 3 — probe A2A endpoint
#         if a2a_endpoint:
#             try:
#                 signed_headers = self_signer.sign_request("POST", a2a_endpoint, "{}")
#                 a2a_response = await client.post(
#                     a2a_endpoint,
#                     json={
#                         "jsonrpc": "2.0",
#                         "id": 1,
#                         "method": "message/send",
#                         "params": {
#                             "message": {
#                                 "role": "user",
#                                 "parts": [{"kind": "text", "text": "ping"}]
#                             }
#                         }
#                     },
#                     headers={
#                         "Content-Type": "application/json",
#                         **signed_headers
#                     }
#                 )
#                 # Any response that is not a 5xx is considered a working endpoint
#                 a2a_passed = a2a_response.status_code < 500
#                 evidence_parts.append(f"a2a_status: {a2a_response.status_code}")

#             except httpx.TimeoutException:
#                 a2a_passed = False
#                 evidence_parts.append("a2a_timeout")
#             except Exception as e:
#                 a2a_passed = False
#                 evidence_parts.append(f"a2a_error: {str(e)[:100]}")

#         # Step 4 — check x402 payment endpoint
#         if a2a_endpoint and x402_passed is None:
#             try:
#                 x402_response = await client.post(
#                     a2a_endpoint,
#                     headers={"Content-Type": "application/json"}
#                 )
#                 x402_passed = x402_response.status_code == 402
#                 evidence_parts.append(f"x402_status: {x402_response.status_code}")
#             except Exception as e:
#                 x402_passed = False
#                 evidence_parts.append(f"x402_error: {str(e)[:100]}")

#     # Step 5 — check Self Agent ID verification
#     self_data   = await check_self_verification(agent_address)
#     self_verified = self_data["verified"]
#     self_fresh    = self_data["proof_fresh"]

#     evidence_parts.append(f"self_verified: {self_verified}")
#     evidence_parts.append(f"self_proof_fresh: {self_fresh}")

#     # Determine overall pass — card must be reachable AND
#     # at least one of A2A or x402 must work
#     a2a_ok  = a2a_passed is True
#     x402_ok = x402_passed is True
#     overall_passed = card_reachable and (a2a_ok or x402_ok)

#     evidence = " | ".join(evidence_parts)
#     response_time_ms = int((time.time() - start_time) * 1000)

#     # Step 6 — persist probe result to database
#     probe = Probe(
#         agent_address   = agent_address.lower(),
#         agent_id        = agent_id,
#         endpoint_probed = a2a_endpoint or "",
#         probe_type      = "full",
#         passed          = overall_passed,
#         evidence        = evidence,
#         response_time_ms = response_time_ms,
#         posted_onchain  = False,
#     )
#     db.add(probe)
#     await db.flush()

#     # Step 7 — update agent cache
#     await _update_agent_cache(
#         agent_address, agent_id, overall_passed,
#         self_verified, self_fresh, self_data, db
#     )

#     # Step 8 — post result onchain
#     tx_hash = None
#     if post_onchain:
#         try:
#             tx_hash = await record_verification_probe(
#                 agent_address, agent_id, overall_passed, evidence[:500]
#             )
#             probe.posted_onchain = True
#             probe.tx_hash        = tx_hash
#         except Exception as e:
#             evidence += f" | onchain_post_failed: {str(e)[:100]}"

#     await db.commit()

#     # Step 9 — check blacklist threshold
#     await _check_blacklist_threshold(agent_address, agent_id, db)

#     # Get current trust score for response
#     from onchain.contracts import get_router_score
#     trust_score = await get_router_score(agent_address)

#     return ProbeResult(
#         agent_address   = agent_address,
#         agent_id        = agent_id,
#         overall_passed  = overall_passed,
#         card_reachable  = card_reachable,
#         a2a_passed      = a2a_passed,
#         x402_passed     = x402_passed,
#         self_verified   = self_verified,
#         self_proof_fresh = self_fresh,
#         trust_score     = trust_score,
#         evidence        = evidence,
#         response_time_ms = response_time_ms,
#         posted_onchain  = probe.posted_onchain,
#         tx_hash         = tx_hash,
#         probed_at       = probe.probed_at
#     )


# async def _update_agent_cache(
#     agent_address: str,
#     agent_id: int,
#     passed: bool,
#     self_verified: bool,
#     self_fresh: bool,
#     self_data: dict,
#     db: AsyncSession
# ) -> None:
#     """Update or create the agent record in the local cache."""
#     result = await db.execute(
#         select(Agent).where(Agent.agent_id == agent_id)
#     )
#     agent = result.scalar_one_or_none()

#     expires_at = None
#     if self_data.get("proof_expires_at"):
#         expires_at = datetime.utcfromtimestamp(self_data["proof_expires_at"])

#     if agent is None:
#         # First time we have seen this agent — create a record
#         try:
#             owner = await get_agent_owner(agent_id)
#         except Exception:
#             owner = agent_address

#         agent = Agent(
#             agent_id             = agent_id,
#             owner_address        = owner.lower(),
#             self_verified        = self_verified,
#             self_proof_fresh     = self_fresh,
#             self_proof_expires_at = expires_at,
#             consecutive_failures = 0 if passed else 1,
#             last_probed_at       = datetime.utcnow()
#         )
#         db.add(agent)
#     else:
#         agent.self_verified         = self_verified
#         agent.self_proof_fresh      = self_fresh
#         agent.self_proof_expires_at = expires_at
#         agent.last_probed_at        = datetime.utcnow()

#         if passed:
#             agent.consecutive_failures = 0
#         else:
#             agent.consecutive_failures = (agent.consecutive_failures or 0) + 1


# async def _check_blacklist_threshold(
#     agent_address: str,
#     agent_id: int,
#     db: AsyncSession
# ) -> None:
#     """Automatically blacklist agents that exceed the failure threshold."""
#     result = await db.execute(
#         select(Agent).where(Agent.agent_id == agent_id)
#     )
#     agent = result.scalar_one_or_none()

#     if agent is None:
#         return

#     if (
#         not agent.is_blacklisted
#         and agent.consecutive_failures >= settings.blacklist_failure_threshold
#     ):
#         reason = (
#             f"Auto-blacklisted after {agent.consecutive_failures} "
#             f"consecutive failed probes"
#         )
#         try:
#             from onchain.contracts import blacklist_agent
#             await blacklist_agent(agent_address, reason)
#             agent.is_blacklisted = True
#             await db.commit()
#         except Exception:
#             pass

import httpx
import time
import json
import logging
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import settings
from onchain.contracts import (
    record_verification_probe,
    get_agent_card_uri,
    get_agent_owner,
    check_self_verification,
)
from db.models import Agent, Probe
from schemas.probe import ProbeResult
from self_id.client import self_id_client

logger = logging.getLogger("trustguard.verifier")

# -------------------------------------------------------------------------
# Staleness thresholds
# -------------------------------------------------------------------------

PROBE_STALE_HOURS    = 24   # re-probe after this many hours
CARD_STALE_DAYS      = 7    # re-fetch card after this many days
SCORE_STALE_HOURS    = 6    # re-score after this many hours


def _is_stale(last_time: Optional[datetime], stale_hours: float) -> bool:
    """Returns True if last_time is older than stale_hours or None."""
    if last_time is None:
        return True
    age_hours = (datetime.utcnow() - last_time).total_seconds() / 3600
    return age_hours > stale_hours


# -------------------------------------------------------------------------
# Smart endpoint probing
# -------------------------------------------------------------------------

async def _probe_endpoint(
    client:   httpx.AsyncClient,
    url:      str,
    method:   str = "GET",
    body:     dict | None = None,
    timeout:  float = 6.0,
) -> dict:
    """
    Probe a single URL and return structured result.
    Never raises — always returns a result dict.
    """
    start = time.time()
    try:
        if method == "GET":
            response = await client.get(url, timeout=timeout)
        else:
            response = await client.post(
                url,
                json    = body or {},
                headers = {"Content-Type": "application/json"},
                timeout = timeout,
            )

        elapsed_ms = int((time.time() - start) * 1000)
        return {
            "reachable":     True,
            "status_code":   response.status_code,
            "passed":        response.status_code < 500,
            "response_time": elapsed_ms,
            "evidence":      f"HTTP {response.status_code} in {elapsed_ms}ms",
        }

    except httpx.TimeoutException:
        return {
            "reachable":     False,
            "status_code":   None,
            "passed":        False,
            "response_time": int(timeout * 1000),
            "evidence":      "timeout",
        }
    except httpx.ConnectError:
        return {
            "reachable":     False,
            "status_code":   None,
            "passed":        False,
            "response_time": int((time.time() - start) * 1000),
            "evidence":      "connection_refused",
        }
    except Exception as e:
        return {
            "reachable":     False,
            "status_code":   None,
            "passed":        False,
            "response_time": int((time.time() - start) * 1000),
            "evidence":      f"error: {str(e)[:80]}",
        }


async def _probe_a2a_endpoint(
    client:       httpx.AsyncClient,
    a2a_endpoint: str,
) -> dict:
    """
    Smart A2A endpoint probe that tries multiple approaches.

    Strategy:
    1. GET the endpoint directly (agent card or health check)
    2. GET /.well-known/agent.json on the base domain
    3. POST with JSON-RPC ping message
    4. GET base URL only

    An endpoint is considered passing if ANY approach gets a non-5xx response.
    This prevents false failures when an agent does not support
    the specific JSON-RPC method we use but is clearly online.
    """
    evidence_parts = []

    # Parse base URL
    from urllib.parse import urlparse
    parsed   = urlparse(a2a_endpoint)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Attempt 1 — GET the A2A endpoint directly
    result1 = await _probe_endpoint(client, a2a_endpoint, "GET")
    evidence_parts.append(f"a2a_get:{result1['evidence']}")

    if result1["passed"]:
        return {
            "passed":        True,
            "status_code":   result1["status_code"],
            "response_time": result1["response_time"],
            "evidence":      " | ".join(evidence_parts),
            "method_used":   "GET",
        }

    # Attempt 2 — GET /.well-known/agent.json on the base domain
    agent_card_url = f"{base_url}/.well-known/agent.json"
    if agent_card_url != a2a_endpoint:
        result2 = await _probe_endpoint(client, agent_card_url, "GET")
        evidence_parts.append(f"agent_card:{result2['evidence']}")

        if result2["passed"]:
            return {
                "passed":        True,
                "status_code":   result2["status_code"],
                "response_time": result2["response_time"],
                "evidence":      " | ".join(evidence_parts),
                "method_used":   "agent_card",
            }

    # Attempt 3 — POST with A2A JSON-RPC ping
    rpc_body = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "message/send",
        "params":  {
            "message": {
                "role":  "user",
                "parts": [{"kind": "text", "text": "ping"}]
            }
        }
    }
    result3 = await _probe_endpoint(client, a2a_endpoint, "POST", rpc_body)
    evidence_parts.append(f"a2a_post:{result3['evidence']}")

    if result3["passed"]:
        return {
            "passed":        True,
            "status_code":   result3["status_code"],
            "response_time": result3["response_time"],
            "evidence":      " | ".join(evidence_parts),
            "method_used":   "POST_jsonrpc",
        }

    # Attempt 4 — GET base URL only (is the server up at all?)
    result4 = await _probe_endpoint(client, base_url, "GET")
    evidence_parts.append(f"base_url:{result4['evidence']}")

    # If base URL responds, the agent is online even if the specific
    # endpoint path is wrong — mark as partial pass
    if result4["passed"]:
        return {
            "passed":        True,   # server is online
            "status_code":   result4["status_code"],
            "response_time": result4["response_time"],
            "evidence":      " | ".join(evidence_parts) + " [base_url_only]",
            "method_used":   "base_url",
        }

    return {
        "passed":        False,
        "status_code":   None,
        "response_time": None,
        "evidence":      " | ".join(evidence_parts),
        "method_used":   "all_failed",
    }


async def _probe_x402_endpoint(
    client:   httpx.AsyncClient,
    endpoint: str,
) -> dict:
    """
    Check if an endpoint supports x402 payments.
    A 402 response with payment requirements is a pass.
    A 200 without X-Payment header is also a pass (endpoint works).
    A 404 or 5xx is a fail.
    """
    result = await _probe_endpoint(client, endpoint, "POST", {"ping": True})

    if result["status_code"] == 402:
        return {
            "passed":   True,
            "evidence": "x402_supported: returned 402 Payment Required",
        }

    if result["passed"]:
        return {
            "passed":   True,
            "evidence": f"x402_endpoint_reachable: {result['evidence']}",
        }

    return {
        "passed":   False,
        "evidence": f"x402_failed: {result['evidence']}",
    }


# -------------------------------------------------------------------------
# Main probe function
# -------------------------------------------------------------------------

async def probe_agent(
    agent_address: str,
    agent_id:      int,
    db:            AsyncSession,
    post_onchain:  bool = True,
    force:         bool = False,
) -> ProbeResult:
    """
    Run a full verification probe against an agent.

    Database-first: returns cached result if probe is recent enough
    unless force=True.

    Checks in order:
    1. Agent card reachability
    2. A2A endpoint (smart multi-attempt)
    3. x402 support
    4. Self Agent ID verification (onchain + API)
    """
    start_time = time.time()

    # ---- Check database cache first ----------------------------------------

    if not force:
        result = await db.execute(
            select(Agent).where(
                Agent.owner_address == agent_address.lower()
            )
        )
        cached = result.scalar_one_or_none()

        if cached and not _is_stale(cached.last_probed_at, PROBE_STALE_HOURS):
            logger.info(
                f"Returning cached probe for {agent_address} "
                f"(last probed: {cached.last_probed_at})"
            )
            from onchain.contracts import get_router_score
            trust_score = await get_router_score(agent_address)

            return ProbeResult(
                agent_address    = agent_address,
                agent_id         = agent_id,
                overall_passed   = cached.consecutive_failures == 0 and cached.a2a_endpoint is not None,
                card_reachable   = cached.card_uri is not None,
                a2a_passed       = cached.a2a_endpoint is not None,
                x402_passed      = cached.supports_x402,
                self_verified    = cached.self_verified,
                self_proof_fresh = cached.self_proof_fresh,
                trust_score      = trust_score,
                evidence         = f"cached_result | last_probed: {cached.last_probed_at}",
                response_time_ms = None,
                posted_onchain   = False,
                tx_hash          = None,
                probed_at        = cached.last_probed_at or datetime.utcnow(),
            )

    # ---- Live probe ----------------------------------------------------------

    evidence_parts = []
    card_reachable = False
    a2a_passed     = None
    x402_passed    = None
    a2a_endpoint   = None
    card_data      = {}

    async with httpx.AsyncClient(
        timeout  = settings.probe_http_timeout,
        follow_redirects = True,
        headers  = {"User-Agent": "TrustGuard-Verifier/1.0"}
    ) as client:

        # Step 1 — fetch agent card URI from registry
        try:
            card_uri = await get_agent_card_uri(agent_id)
        except Exception as e:
            card_uri = None
            evidence_parts.append(f"card_uri_error: {str(e)[:60]}")

        # Step 2 — fetch and parse agent card JSON
        if card_uri:
            card_probe = await _probe_endpoint(client, card_uri, "GET")
            evidence_parts.append(f"card:{card_probe['evidence']}")

            if card_probe["passed"]:
                card_reachable = True
                try:
                    response = await client.get(card_uri, timeout=6.0)
                    card_data = response.json()

                    # Extract endpoints
                    for ep in card_data.get("endpoints", []):
                        ep_type = ep.get("type", "").lower()
                        if ep_type == "a2a":
                            a2a_endpoint = ep.get("url")
                        if ep_type == "x402":
                            x402_passed = True

                    # Check capabilities list
                    caps = card_data.get("capabilities", [])
                    if isinstance(caps, list):
                        if any("x402" in str(c).lower() for c in caps):
                            x402_passed = True

                except Exception as e:
                    evidence_parts.append(f"card_parse_error: {str(e)[:60]}")

        # Step 3 — probe A2A endpoint with smart multi-attempt
        if a2a_endpoint:
            a2a_result = await _probe_a2a_endpoint(client, a2a_endpoint)
            a2a_passed = a2a_result["passed"]
            evidence_parts.append(f"a2a:{a2a_result['evidence']}")
        else:
            evidence_parts.append("a2a: no_endpoint_in_card")

        # Step 4 — probe x402 if not already determined from card
        if a2a_endpoint and x402_passed is None:
            x402_result = await _probe_x402_endpoint(client, a2a_endpoint)
            x402_passed = x402_result["passed"]
            evidence_parts.append(f"x402:{x402_result['evidence']}")

    # Step 5 — Self verification (onchain + API)
    try:
        self_data     = await check_self_verification(agent_address)
        self_verified = self_data.get("verified", False)
        self_fresh    = self_data.get("proof_fresh", False)

        if not self_verified and agent_id:
            api_result = await self_id_client.verify_agent(agent_id)
            if api_result and (
                api_result.get("isVerified") or
                api_result.get("verified", False)
            ):
                self_verified = True
                self_fresh    = api_result.get("isProofFresh", False)
                evidence_parts.append("self: verified_via_api")
            else:
                evidence_parts.append("self: not_verified")
        else:
            evidence_parts.append(
                f"self: {'verified' if self_verified else 'not_verified'}"
            )

    except Exception as e:
        self_verified = False
        self_fresh    = False
        evidence_parts.append(f"self_error: {str(e)[:60]}")

    # ---- Determine overall result ------------------------------------------

    # Pass if card is reachable AND (a2a works OR x402 works)
    # Also pass if card is reachable and a2a_passed is None
    # (agent may not have listed A2A endpoint but card is valid)
    if a2a_passed is None and card_reachable:
        overall_passed = True
        evidence_parts.append("pass_reason: card_reachable_no_a2a_endpoint")
    else:
        overall_passed = card_reachable and (
            a2a_passed is True or x402_passed is True
        )

    evidence         = " | ".join(evidence_parts)
    response_time_ms = int((time.time() - start_time) * 1000)

    # ---- Persist probe to database -----------------------------------------

    probe = Probe(
        agent_address    = agent_address.lower(),
        agent_id         = agent_id,
        endpoint_probed  = a2a_endpoint or "",
        probe_type       = "full",
        passed           = overall_passed,
        evidence         = evidence,
        response_time_ms = response_time_ms,
        posted_onchain   = False,
    )
    db.add(probe)
    await db.flush()

    # ---- Update agent cache ------------------------------------------------

    await _update_agent_cache(
        agent_address  = agent_address,
        agent_id       = agent_id,
        overall_passed = overall_passed,
        a2a_endpoint   = a2a_endpoint,
        x402_passed    = x402_passed or False,
        card_data      = card_data,
        self_verified  = self_verified,
        self_fresh     = self_fresh,
        db             = db,
    )

    # ---- Post onchain if requested -----------------------------------------

    tx_hash = None
    if post_onchain:
        try:
            tx_hash = await record_verification_probe(
                agent_address = agent_address,
                agent_id      = agent_id,
                passed        = overall_passed,
                evidence      = evidence[:500]
            )
            probe.posted_onchain = True
            probe.tx_hash        = tx_hash
        except Exception as e:
            evidence += f" | onchain_failed: {str(e)[:60]}"

    await db.commit()

    # ---- Auto-blacklist if threshold exceeded ------------------------------

    await _check_blacklist_threshold(agent_address, agent_id, db)

    # ---- Get current trust score -------------------------------------------

    from onchain.contracts import get_router_score
    trust_score = await get_router_score(agent_address)

    return ProbeResult(
        agent_address    = agent_address,
        agent_id         = agent_id,
        overall_passed   = overall_passed,
        card_reachable   = card_reachable,
        a2a_passed       = a2a_passed,
        x402_passed      = x402_passed,
        self_verified    = self_verified,
        self_proof_fresh = self_fresh,
        trust_score      = trust_score,
        evidence         = evidence,
        response_time_ms = response_time_ms,
        posted_onchain   = probe.posted_onchain,
        tx_hash          = tx_hash,
        probed_at        = probe.probed_at,
    )


async def _update_agent_cache(
    agent_address:  str,
    agent_id:       int,
    overall_passed: bool,
    a2a_endpoint:   Optional[str],
    x402_passed:    bool,
    card_data:      dict,
    self_verified:  bool,
    self_fresh:     bool,
    db:             AsyncSession,
) -> None:
    """Update the agent record in local database after a probe."""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    name        = card_data.get("name")
    description = card_data.get("description")
    mcp_ep      = None
    for ep in card_data.get("endpoints", []):
        if ep.get("type", "").lower() == "mcp":
            mcp_ep = ep.get("url")

    if agent is None:
        try:
            owner = await get_agent_owner(agent_id)
        except Exception:
            owner = agent_address

        agent = Agent(
            agent_id             = agent_id,
            owner_address        = owner.lower(),
            a2a_endpoint         = a2a_endpoint,
            mcp_endpoint         = mcp_ep,
            supports_x402        = x402_passed,
            name                 = name,
            description          = description,
            self_verified        = self_verified,
            self_proof_fresh     = self_fresh,
            consecutive_failures = 0 if overall_passed else 1,
            last_probed_at       = datetime.utcnow(),
        )
        db.add(agent)
    else:
        if a2a_endpoint:
            agent.a2a_endpoint = a2a_endpoint
        if mcp_ep:
            agent.mcp_endpoint = mcp_ep
        if x402_passed:
            agent.supports_x402 = True
        if name and not agent.name:
            agent.name = name
        if description and not agent.description:
            agent.description = description

        agent.self_verified    = self_verified
        agent.self_proof_fresh = self_fresh
        agent.last_probed_at   = datetime.utcnow()

        if overall_passed:
            agent.consecutive_failures = 0
        else:
            agent.consecutive_failures = (agent.consecutive_failures or 0) + 1


async def _check_blacklist_threshold(
    agent_address: str,
    agent_id:      int,
    db:            AsyncSession,
) -> None:
    """Auto-blacklist agents exceeding consecutive failure threshold."""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if agent is None:
        return

    if (
        not agent.is_blacklisted and
        (agent.consecutive_failures or 0) >= settings.blacklist_failure_threshold
    ):
        reason = (
            f"Auto-blacklisted: {agent.consecutive_failures} "
            f"consecutive failed probes"
        )
        try:
            from onchain.contracts import blacklist_agent
            await blacklist_agent(agent_address, reason)
            agent.is_blacklisted = True
            await db.commit()
        except Exception:
            pass