import httpx
import time
import json
from datetime import datetime
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from config import settings
from onchain.contracts import (
    record_verification_probe,
    get_agent_card_uri,
    get_agent_owner,
    check_self_verification
)
from db.models import Agent, Probe
from schemas.probe import ProbeResult
from self_id.signer import self_signer


async def probe_agent(
    agent_address: str,
    agent_id: int,
    db: AsyncSession,
    post_onchain: bool = True
) -> ProbeResult:
    """
    Run a full verification probe against an agent.
    Checks their agent card, A2A endpoint, x402 support, and Self verification.
    Posts result onchain if post_onchain is True.
    """
    start_time = time.time()
    evidence_parts = []

    card_reachable = False
    a2a_passed     = None
    x402_passed    = None
    a2a_endpoint   = None

    async with httpx.AsyncClient(timeout=settings.probe_http_timeout) as client:

        # Step 1 — fetch agent card URI from ERC-8004 registry
        try:
            card_uri = await get_agent_card_uri(agent_id)
        except Exception as e:
            card_uri = None
            evidence_parts.append(f"card_uri_fetch_failed: {str(e)[:100]}")

        # Step 2 — fetch and parse agent card JSON
        if card_uri:
            try:
                card_response = await client.get(card_uri)
                card_response.raise_for_status()
                card_data      = card_response.json()
                card_reachable = True
                evidence_parts.append("card_reachable: true")

                # Extract endpoints from card
                endpoints = card_data.get("endpoints", [])
                for ep in endpoints:
                    if ep.get("type") == "a2a":
                        a2a_endpoint = ep.get("url")
                    if ep.get("type") == "x402":
                        x402_passed = True

            except Exception as e:
                evidence_parts.append(f"card_fetch_failed: {str(e)[:100]}")

        # Step 3 — probe A2A endpoint
        if a2a_endpoint:
            try:
                signed_headers = self_signer.sign_request("POST", a2a_endpoint, "{}")
                a2a_response = await client.post(
                    a2a_endpoint,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "message/send",
                        "params": {
                            "message": {
                                "role": "user",
                                "parts": [{"kind": "text", "text": "ping"}]
                            }
                        }
                    },
                    headers={
                        "Content-Type": "application/json",
                        **signed_headers
                    }
                )
                # Any response that is not a 5xx is considered a working endpoint
                a2a_passed = a2a_response.status_code < 500
                evidence_parts.append(f"a2a_status: {a2a_response.status_code}")

            except httpx.TimeoutException:
                a2a_passed = False
                evidence_parts.append("a2a_timeout")
            except Exception as e:
                a2a_passed = False
                evidence_parts.append(f"a2a_error: {str(e)[:100]}")

        # Step 4 — check x402 payment endpoint
        if a2a_endpoint and x402_passed is None:
            try:
                x402_response = await client.post(
                    a2a_endpoint,
                    headers={"Content-Type": "application/json"}
                )
                x402_passed = x402_response.status_code == 402
                evidence_parts.append(f"x402_status: {x402_response.status_code}")
            except Exception as e:
                x402_passed = False
                evidence_parts.append(f"x402_error: {str(e)[:100]}")

    # Step 5 — check Self Agent ID verification
    self_data   = await check_self_verification(agent_address)
    self_verified = self_data["verified"]
    self_fresh    = self_data["proof_fresh"]

    evidence_parts.append(f"self_verified: {self_verified}")
    evidence_parts.append(f"self_proof_fresh: {self_fresh}")

    # Determine overall pass — card must be reachable AND
    # at least one of A2A or x402 must work
    a2a_ok  = a2a_passed is True
    x402_ok = x402_passed is True
    overall_passed = card_reachable and (a2a_ok or x402_ok)

    evidence = " | ".join(evidence_parts)
    response_time_ms = int((time.time() - start_time) * 1000)

    # Step 6 — persist probe result to database
    probe = Probe(
        agent_address   = agent_address.lower(),
        agent_id        = agent_id,
        endpoint_probed = a2a_endpoint or "",
        probe_type      = "full",
        passed          = overall_passed,
        evidence        = evidence,
        response_time_ms = response_time_ms,
        posted_onchain  = False,
    )
    db.add(probe)
    await db.flush()

    # Step 7 — update agent cache
    await _update_agent_cache(
        agent_address, agent_id, overall_passed,
        self_verified, self_fresh, self_data, db
    )

    # Step 8 — post result onchain
    tx_hash = None
    if post_onchain:
        try:
            tx_hash = await record_verification_probe(
                agent_address, agent_id, overall_passed, evidence[:500]
            )
            probe.posted_onchain = True
            probe.tx_hash        = tx_hash
        except Exception as e:
            evidence += f" | onchain_post_failed: {str(e)[:100]}"

    await db.commit()

    # Step 9 — check blacklist threshold
    await _check_blacklist_threshold(agent_address, agent_id, db)

    # Get current trust score for response
    from onchain.contracts import get_router_score
    trust_score = await get_router_score(agent_address)

    return ProbeResult(
        agent_address   = agent_address,
        agent_id        = agent_id,
        overall_passed  = overall_passed,
        card_reachable  = card_reachable,
        a2a_passed      = a2a_passed,
        x402_passed     = x402_passed,
        self_verified   = self_verified,
        self_proof_fresh = self_fresh,
        trust_score     = trust_score,
        evidence        = evidence,
        response_time_ms = response_time_ms,
        posted_onchain  = probe.posted_onchain,
        tx_hash         = tx_hash,
        probed_at       = probe.probed_at
    )


async def _update_agent_cache(
    agent_address: str,
    agent_id: int,
    passed: bool,
    self_verified: bool,
    self_fresh: bool,
    self_data: dict,
    db: AsyncSession
) -> None:
    """Update or create the agent record in the local cache."""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    expires_at = None
    if self_data.get("proof_expires_at"):
        expires_at = datetime.utcfromtimestamp(self_data["proof_expires_at"])

    if agent is None:
        # First time we have seen this agent — create a record
        try:
            owner = await get_agent_owner(agent_id)
        except Exception:
            owner = agent_address

        agent = Agent(
            agent_id             = agent_id,
            owner_address        = owner.lower(),
            self_verified        = self_verified,
            self_proof_fresh     = self_fresh,
            self_proof_expires_at = expires_at,
            consecutive_failures = 0 if passed else 1,
            last_probed_at       = datetime.utcnow()
        )
        db.add(agent)
    else:
        agent.self_verified         = self_verified
        agent.self_proof_fresh      = self_fresh
        agent.self_proof_expires_at = expires_at
        agent.last_probed_at        = datetime.utcnow()

        if passed:
            agent.consecutive_failures = 0
        else:
            agent.consecutive_failures = (agent.consecutive_failures or 0) + 1


async def _check_blacklist_threshold(
    agent_address: str,
    agent_id: int,
    db: AsyncSession
) -> None:
    """Automatically blacklist agents that exceed the failure threshold."""
    result = await db.execute(
        select(Agent).where(Agent.agent_id == agent_id)
    )
    agent = result.scalar_one_or_none()

    if agent is None:
        return

    if (
        not agent.is_blacklisted
        and agent.consecutive_failures >= settings.blacklist_failure_threshold
    ):
        reason = (
            f"Auto-blacklisted after {agent.consecutive_failures} "
            f"consecutive failed probes"
        )
        try:
            from onchain.contracts import blacklist_agent
            await blacklist_agent(agent_address, reason)
            agent.is_blacklisted = True
            await db.commit()
        except Exception:
            pass