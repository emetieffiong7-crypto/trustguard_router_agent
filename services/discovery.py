from datetime import datetime
from typing import Optional, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import httpx
import logging

from config import settings
from db.models import Agent
from schemas.discovery import DiscoveredAgent, DiscoveryResponse
from subgraph.client import subgraph_client
from subgraph.queries import (
    GET_AGENT_TRUST_SCORES,
    GET_REGISTERED_AGENTS,
    GET_ALL_REPUTATION_SUMMARIES,
    GET_SELF_VERIFIED_AGENTS,
)
from onchain.contracts import (
    get_agent_owner,
    get_agent_wallet,
    get_agent_card_uri,
    check_self_verification,
)

logger = logging.getLogger("trustguard.discovery")


async def discover_agents(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Discover agents ranked by trust score.

    On mainnet — queries registered agents from the ERC-8004 subgraph
    and enriches with reputation summaries.

    On sepolia — queries TrustGuard trust score events from the subgraph
    and falls back to direct RPC if subgraph is not available.
    """
    if settings.environment == "mainnet":
        return await _discover_mainnet(
            capability, min_score, self_verified_only, limit, db
        )
    return await _discover_sepolia(
        capability, min_score, self_verified_only, limit, db
    )

async def _discover_mainnet(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Mainnet discovery — primary source is local database (populated by
    backfill script), enriched with subgraph reputation data.
    Falls back to subgraph-only if database is empty.
    """

    # Check how many agents we have locally
    from sqlalchemy import func
    from db.models import Agent

    count_result = await db.execute(select(func.count(Agent.id)))
    local_count  = count_result.scalar() or 0

    logger.info(f"Local agent count: {local_count}")

    if local_count > 10:
        # Use local database as primary source — much faster and complete
        return await _discover_from_local_with_reputation(
            capability, min_score, self_verified_only, limit, db
        )

    # Fall back to subgraph if database is empty
    return await _discover_from_subgraph_mainnet(
        capability, min_score, self_verified_only, limit, db
    )

async def get_agent_profile(
    address:  Optional[str],
    agent_id: Optional[int],
    db:       AsyncSession,
) -> Optional[dict]:
    """
    Build a complete intelligence profile for a single agent.
    Resolves agentId from address or vice versa automatically.
    Aggregates from all available data sources.
    """
    from onchain.contracts import (
        get_agent_owner,
        get_agent_wallet,
        get_agent_card_uri,
        get_reputation_summary,
        check_self_verification,
        get_self_verification_with_api_fallback,
    )
    from self_id.client import self_id_client
    from services.scorer import get_probe_history

    # -------------------------------------------------------------------------
    # Step 1 — Resolve agentId and address from whichever was provided
    # -------------------------------------------------------------------------

    resolved_agent_id      = agent_id
    resolved_owner_address = address.lower() if address else None

    # If only address provided, try to find agentId from local database first
    if address and resolved_agent_id is None:
        result = await db.execute(
            select(Agent).where(
                Agent.owner_address == address.lower()
            )
        )
        cached = result.scalar_one_or_none()
        if cached:
            resolved_agent_id = cached.agent_id

    # If only agentId provided, resolve owner address from contract
    if resolved_agent_id is not None and resolved_owner_address is None:
        try:
            resolved_owner_address = (await get_agent_owner(resolved_agent_id)).lower()
        except Exception:
            pass

    # If we still have no agentId, try Self API by address
    if resolved_agent_id is None and resolved_owner_address:
        try:
            agents = await self_id_client.get_agents_by_address(resolved_owner_address)
            if agents:
                resolved_agent_id = int(agents[0].get("agentId", 0)) or None
        except Exception:
            pass

    if resolved_agent_id is None and resolved_owner_address is None:
        return None

    # -------------------------------------------------------------------------
    # Step 2 — Load from local database cache
    # -------------------------------------------------------------------------

    cached_agent = None
    if resolved_agent_id is not None:
        result = await db.execute(
            select(Agent).where(Agent.agent_id == resolved_agent_id)
        )
        cached_agent = result.scalar_one_or_none()
    elif resolved_owner_address:
        result = await db.execute(
            select(Agent).where(Agent.owner_address == resolved_owner_address)
        )
        cached_agent = result.scalar_one_or_none()
        if cached_agent:
            resolved_agent_id = cached_agent.agent_id

    # -------------------------------------------------------------------------
    # Step 3 — Fetch onchain identity data
    # -------------------------------------------------------------------------

    card_uri       = None
    wallet_address = None

    if resolved_agent_id is not None:
        try:
            card_uri = await get_agent_card_uri(resolved_agent_id)
        except Exception:
            card_uri = cached_agent.card_uri if cached_agent else None

        try:
            wallet_address = await get_agent_wallet(resolved_agent_id)
            zero = "0x" + "0" * 40
            if wallet_address and wallet_address.lower() == zero:
                wallet_address = None
        except Exception:
            wallet_address = cached_agent.wallet_address if cached_agent else None

    # -------------------------------------------------------------------------
    # Step 4 — Self verification (onchain + API fallback)
    # -------------------------------------------------------------------------

    self_data = {}
    if resolved_agent_id is not None and resolved_owner_address:
        try:
            self_data = await get_self_verification_with_api_fallback(
                agent_id      = resolved_agent_id,
                owner_address = resolved_owner_address,
            )
        except Exception as e:
            logger.warning(f"Self verification failed for {resolved_agent_id}: {e}")

    # -------------------------------------------------------------------------
    # Step 5 — ERC-8004 Reputation Registry
    # -------------------------------------------------------------------------

    reputation = {}
    if resolved_agent_id is not None:
        try:
            reputation = await get_reputation_summary(resolved_agent_id)
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Step 6 — Probe history from local database
    # -------------------------------------------------------------------------

    probe_history = []
    if resolved_owner_address:
        try:
            probe_history = await get_probe_history(
                agent_address = resolved_owner_address,
                limit         = 5,
                db            = db,
            )
        except Exception:
            pass

    # -------------------------------------------------------------------------
    # Step 7 — Subgraph registration data
    # -------------------------------------------------------------------------

    subgraph_data = {}
    if resolved_agent_id is not None:
        try:
            sg_result = await subgraph_client.query(
                """
                query GetAgent($agentId: String!) {
                  registeredAgents(where: { agentId: $agentId }) {
                    agentId
                    owner
                    cardURI
                    registeredAt
                    transactionHash
                  }
                  agentReputationSummaries(where: { agentId: $agentId }) {
                    totalFeedback
                    positiveCount
                    negativeCount
                    cumulativeScore
                    lastUpdated
                  }
                }
                """,
                variables={"agentId": str(resolved_agent_id)}
            )
            agents_sg = sg_result.get("registeredAgents", [])
            rep_sg    = sg_result.get("agentReputationSummaries", [])

            if agents_sg:
                subgraph_data["registration"] = agents_sg[0]
            if rep_sg:
                subgraph_data["reputation"] = rep_sg[0]

        except Exception as e:
            logger.warning(f"Subgraph query failed for agent {resolved_agent_id}: {e}")

    # -------------------------------------------------------------------------
    # Step 8 — Compute or retrieve composite score
    # -------------------------------------------------------------------------

    score_breakdown = {}
    trust_score     = cached_agent.trust_score if cached_agent else 0

    if cached_agent:
        score_breakdown = {
            "total":       cached_agent.trust_score,
            "age":         cached_agent.score_age_component   or 0,
            "card":        cached_agent.score_card_component  or 0,
            "reputation":  cached_agent.score_rep_component   or 0,
            "self":        cached_agent.score_self_component  or 0,
            "probe":       cached_agent.score_probe_component or 0,
        }

    # -------------------------------------------------------------------------
    # Step 9 — Parse card for endpoint data if not in cache
    # -------------------------------------------------------------------------

    card_data = {}
    if card_uri and (not cached_agent or not cached_agent.a2a_endpoint):
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                from scripts.backfill_agents import fetch_and_parse_card
                card_data = await fetch_and_parse_card(card_uri, client)
        except Exception:
            pass

    a2a_endpoint  = (
        card_data.get("a2a_endpoint") or
        (cached_agent.a2a_endpoint if cached_agent else None)
    )
    supports_x402 = (
        card_data.get("supports_x402") or
        (cached_agent.supports_x402 if cached_agent else False)
    )
    name          = (
        card_data.get("name") or
        (cached_agent.name if cached_agent else None)
    )
    description   = (
        card_data.get("description") or
        (cached_agent.description if cached_agent else None)
    )

    # -------------------------------------------------------------------------
    # Step 10 — Determine risk assessment
    # -------------------------------------------------------------------------

    is_blacklisted   = cached_agent.is_blacklisted if cached_agent else False
    is_self_verified = self_data.get("verified", False) or (
        cached_agent.self_verified if cached_agent else False
    )

    if is_blacklisted:
        risk_level = "BLOCKED"
    elif trust_score >= 70:
        risk_level = "LOW"
    elif trust_score >= 40:
        risk_level = "MEDIUM"
    elif trust_score >= 20:
        risk_level = "MODERATE"
    else:
        risk_level = "HIGH"

    # -------------------------------------------------------------------------
    # Assemble final profile
    # -------------------------------------------------------------------------

    registered_at = None
    if subgraph_data.get("registration"):
        ts = subgraph_data["registration"].get("registeredAt")
        if ts:
            registered_at = datetime.utcfromtimestamp(int(ts)).isoformat()
    elif cached_agent and cached_agent.registered_at:
        registered_at = cached_agent.registered_at.isoformat()

    return {
        # Core identity
        "agent_id":       resolved_agent_id,
        "owner_address":  resolved_owner_address,
        "wallet_address": wallet_address,
        "card_uri":       card_uri,
        "registered_at":  registered_at,

        # Parsed capabilities
        "name":           name,
        "description":    description,
        "a2a_endpoint":   a2a_endpoint,
        "supports_x402":  supports_x402,

        # Trust signals
        "trust_score":    trust_score,
        "score_breakdown": score_breakdown,
        "risk_level":     risk_level,
        "is_blacklisted": is_blacklisted,

        # Self verification
        "self_verification": {
            "verified":              is_self_verified,
            "proof_fresh":           self_data.get("proof_fresh", False),
            "proof_expires_at":      self_data.get("proof_expires_at"),
            "verification_strength": self_data.get("verification_strength"),
            "proof_provider":        self_data.get("proof_provider"),
            "sybil_count":           self_data.get("sybil_count"),
            "source":                self_data.get("source", "unknown"),
        },

        # ERC-8004 reputation
        "reputation": {
            "total_feedback":   reputation.get("count",  0),
            "cumulative_score": reputation.get("sum",    0),
            "avg_score":        reputation.get("score",  0),
            "subgraph_summary": subgraph_data.get("reputation"),
        },

        # Probe history
        "probe_history":  probe_history,

        # Subgraph data
        "onchain_registration": subgraph_data.get("registration"),

        # TrustGuard metadata
        "trustguard_metadata": {
            "last_probed_at":  cached_agent.last_probed_at.isoformat()
                               if cached_agent and cached_agent.last_probed_at else None,
            "last_scored_at":  cached_agent.last_scored_at.isoformat()
                               if cached_agent and cached_agent.last_scored_at else None,
            "consecutive_failures": cached_agent.consecutive_failures
                                    if cached_agent else 0,
            "in_local_db":     cached_agent is not None,
        }
    }

async def _discover_from_local_with_reputation(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Query local database for agents, enrich with subgraph reputation,
    and return ranked results. This is the primary mainnet discovery path
    since the backfill has populated the full agent set.
    """
    from db.models import Agent

    # Build query from local database
    query = select(Agent).where(
        Agent.is_blacklisted == False
    )

    if self_verified_only:
        query = query.where(Agent.self_verified == True)

    if min_score > 0:
        query = query.where(Agent.trust_score >= min_score)

    if capability:
        cap_lower = capability.lower()
        if cap_lower == "a2a":
            query = query.where(Agent.a2a_endpoint != None)
        if cap_lower == "x402":
            query = query.where(Agent.supports_x402 == True)

    # Get more than needed so we can sort and slice
    query   = query.order_by(
        Agent.self_verified.desc(),
        Agent.trust_score.desc(),
        Agent.agent_id.asc()
    ).limit(limit * 2)

    result = await db.execute(query)
    agents = result.scalars().all()

    if not agents:
        return DiscoveryResponse(
            results=[],
            total=0,
            filtered_by_capability=capability,
            min_score_applied=min_score
        )

    # Fetch reputation summaries from subgraph for these specific agents
    agent_ids = [str(a.agent_id) for a in agents]
    rep_map: dict = {}

    try:
        rep_data = await subgraph_client.query(
            GET_ALL_REPUTATION_SUMMARIES,
            variables={"limit": len(agent_ids) + 10}
        )
        rep_map = {
            str(r["agentId"]): r
            for r in rep_data.get("agentReputationSummaries", [])
        }
    except Exception as e:
        logger.warning(f"Could not fetch reputation from subgraph: {e}")

    # Fetch Self verified agents from subgraph
    self_verified_owners: set = set()
    try:
        self_data = await subgraph_client.query(
            GET_SELF_VERIFIED_AGENTS,
            variables={"limit": 500}
        )
        self_verified_owners = {
            s["owner"].lower()
            for s in self_data.get("selfVerifiedAgents", [])
        }
    except Exception as e:
        logger.warning(f"Could not fetch Self verified agents: {e}")

    discovered: List[DiscoveredAgent] = []

    for agent in agents:
        rep          = rep_map.get(str(agent.agent_id), {})
        total_fb     = int(rep.get("totalFeedback",   0))
        pos_count    = int(rep.get("positiveCount",   0))
        cum_score    = int(rep.get("cumulativeScore", 0))

        # Use subgraph reputation score if available,
        # otherwise use TrustGuard score from database
        if total_fb > 0:
            rep_score   = min(100, int((pos_count / total_fb) * 100))
            trust_score = max(agent.trust_score, rep_score)
        else:
            trust_score = agent.trust_score

        # Update Self verification from subgraph
        is_self_verified = (
            agent.owner_address.lower() in self_verified_owners
            or agent.self_verified
        )

        success_rate = round(pos_count / total_fb, 2) if total_fb > 0 else 0.0

        discovered.append(DiscoveredAgent(
            agent_id         = agent.agent_id,
            owner_address    = agent.owner_address,
            wallet_address   = agent.wallet_address,
            name             = agent.name,
            description      = agent.description,
            a2a_endpoint     = agent.a2a_endpoint,
            supports_x402    = agent.supports_x402,
            trust_score      = trust_score,
            self_verified    = is_self_verified,
            self_proof_fresh = is_self_verified,
            total_interactions = total_fb,
            success_rate     = success_rate,
            card_uri         = agent.card_uri,
        ))

        if len(discovered) >= limit:
            break

    # Sort by Self-verified first then trust score
    discovered.sort(
        key=lambda a: (a.self_verified, a.trust_score, a.total_interactions),
        reverse=True
    )

    return DiscoveryResponse(
        results                = discovered[:limit],
        total                  = len(discovered),
        filtered_by_capability = capability,
        min_score_applied      = min_score
    )

async def search_agents_by_name(
    query: str,
    limit: int,
    db:    AsyncSession,
) -> DiscoveryResponse:
    """
    Search agents by name or description using local database.
    Case-insensitive text match. Returns immediately from cache.
    No external calls needed.
    """
    from sqlalchemy import or_, func

    query_lower = f"%{query.lower()}%"

    result = await db.execute(
        select(Agent).where(
            Agent.is_blacklisted == False,
            or_(
                func.lower(Agent.name).like(query_lower),
                func.lower(Agent.description).like(query_lower),
            )
        ).order_by(
            Agent.trust_score.desc()
        ).limit(limit)
    )
    agents = result.scalars().all()

    discovered = [
        DiscoveredAgent(
            agent_id         = a.agent_id,
            owner_address    = a.owner_address,
            wallet_address   = a.wallet_address,
            name             = a.name,
            description      = a.description,
            a2a_endpoint     = a.a2a_endpoint,
            supports_x402    = a.supports_x402 or False,
            trust_score      = a.trust_score or 0,
            self_verified    = a.self_verified or False,
            self_proof_fresh = a.self_proof_fresh or False,
            total_interactions = 0,
            success_rate     = 0.0,
            card_uri         = a.card_uri,
        )
        for a in agents
    ]

    return DiscoveryResponse(
        results                = discovered,
        total                  = len(discovered),
        filtered_by_capability = None,
        min_score_applied      = 0
    )


async def _discover_from_subgraph_mainnet(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Fallback — query subgraph directly when local database is empty.
    Only used before the first backfill run.
    """
    agents_data = await subgraph_client.query(
        GET_REGISTERED_AGENTS,
        variables={"limit": limit * 3, "skip": 0}
    )
    registered = agents_data.get("registeredAgents", [])

    if not registered:
        return DiscoveryResponse(
            results=[],
            total=0,
            filtered_by_capability=capability,
            min_score_applied=min_score
        )

    rep_data    = await subgraph_client.query(
        GET_ALL_REPUTATION_SUMMARIES,
        variables={"limit": 100}
    )
    rep_summaries = {
        str(r["agentId"]): r
        for r in rep_data.get("agentReputationSummaries", [])
    }

    self_data = await subgraph_client.query(
        GET_SELF_VERIFIED_AGENTS,
        variables={"limit": 200}
    )
    self_verified_owners = {
        s["owner"].lower()
        for s in self_data.get("selfVerifiedAgents", [])
    }

    discovered: List[DiscoveredAgent] = []

    for agent_record in registered:
        owner_address = agent_record["owner"].lower()
        agent_id_str  = str(agent_record["agentId"])

        is_self_verified = owner_address in self_verified_owners
        if self_verified_only and not is_self_verified:
            continue

        rep        = rep_summaries.get(agent_id_str, {})
        total_fb   = int(rep.get("totalFeedback", 0))
        pos_count  = int(rep.get("positiveCount", 0))
        trust_score = min(100, int((pos_count / total_fb) * 100)) if total_fb > 0 else 0

        if trust_score < min_score:
            continue

        discovered.append(DiscoveredAgent(
            agent_id         = int(agent_record["agentId"]),
            owner_address    = owner_address,
            wallet_address   = None,
            name             = None,
            description      = None,
            a2a_endpoint     = None,
            supports_x402    = False,
            trust_score      = trust_score,
            self_verified    = is_self_verified,
            self_proof_fresh = is_self_verified,
            total_interactions = total_fb,
            success_rate     = round(pos_count / total_fb, 2) if total_fb > 0 else 0.0,
            card_uri         = agent_record.get("cardURI"),
        ))

        if len(discovered) >= limit:
            break

    discovered.sort(
        key=lambda a: (a.self_verified, a.trust_score),
        reverse=True
    )

    return DiscoveryResponse(
        results                = discovered,
        total                  = len(discovered),
        filtered_by_capability = capability,
        min_score_applied      = min_score
    )

async def _discover_sepolia(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Sepolia discovery — uses TrustGuard trust score events from subgraph.
    Falls back to local database cache if subgraph is unavailable.
    """

    # Try subgraph first
    try:
        data = await subgraph_client.query(
            GET_AGENT_TRUST_SCORES,
            variables={"minScore": min_score, "limit": limit * 3}
        )
        score_events = data.get("trustScoreUpdateds", [])
    except Exception as e:
        logger.warning(f"Subgraph unavailable, falling back to cache: {e}")
        score_events = []

    if not score_events:
        # Fall back to local database cache
        return await _discover_from_cache(
            capability, min_score, self_verified_only, limit, db
        )

    # Deduplicate — keep latest score per agent
    seen: dict = {}
    for event in score_events:
        addr = event["agent"].lower()
        if addr not in seen:
            seen[addr] = event

    agent_addresses = list(seen.keys())

    result = await db.execute(
        select(Agent).where(Agent.owner_address.in_(agent_addresses))
    )
    cached_agents = {
        a.owner_address.lower(): a
        for a in result.scalars().all()
    }

    discovered: List[DiscoveredAgent] = []

    for address, score_event in seen.items():
        cached       = cached_agents.get(address)
        trust_score  = int(score_event.get("newScore", 0))
        agent_id     = cached.agent_id if cached else None

        if agent_id is None:
            continue

        self_verified = cached.self_verified if cached else False
        self_fresh    = cached.self_proof_fresh if cached else False

        if self_verified_only and not (self_verified and self_fresh):
            continue

        if capability and cached:
            cap_lower = capability.lower()
            if cap_lower == "a2a" and not cached.a2a_endpoint:
                continue
            if cap_lower == "x402" and not cached.supports_x402:
                continue

        discovered.append(DiscoveredAgent(
            agent_id         = agent_id,
            owner_address    = address,
            wallet_address   = cached.wallet_address if cached else None,
            name             = cached.name if cached else None,
            description      = cached.description if cached else None,
            a2a_endpoint     = cached.a2a_endpoint if cached else None,
            supports_x402    = cached.supports_x402 if cached else False,
            trust_score      = trust_score,
            self_verified    = self_verified,
            self_proof_fresh = self_fresh,
            total_interactions = 0,
            success_rate     = 0.0,
            card_uri         = cached.card_uri if cached else None,
        ))

        if len(discovered) >= limit:
            break

    discovered.sort(
        key=lambda a: (a.self_verified and a.self_proof_fresh, a.trust_score),
        reverse=True
    )

    return DiscoveryResponse(
        results                = discovered,
        total                  = len(discovered),
        filtered_by_capability = capability,
        min_score_applied      = min_score
    )


async def _discover_from_cache(
    capability:         Optional[str],
    min_score:          int,
    self_verified_only: bool,
    limit:              int,
    db:                 AsyncSession
) -> DiscoveryResponse:
    """
    Last resort — return agents from local database cache only.
    Used when both subgraph and RPC are unavailable.
    """
    query = select(Agent).where(
        Agent.is_blacklisted == False,
        Agent.trust_score >= min_score
    ).order_by(Agent.trust_score.desc()).limit(limit)

    if self_verified_only:
        query = query.where(Agent.self_verified == True)

    result = await db.execute(query)
    agents = result.scalars().all()

    discovered = [
        DiscoveredAgent(
            agent_id         = a.agent_id,
            owner_address    = a.owner_address,
            wallet_address   = a.wallet_address,
            name             = a.name,
            description      = a.description,
            a2a_endpoint     = a.a2a_endpoint,
            supports_x402    = a.supports_x402,
            trust_score      = a.trust_score,
            self_verified    = a.self_verified,
            self_proof_fresh = a.self_proof_fresh,
            total_interactions = 0,
            success_rate     = 0.0,
            card_uri         = a.card_uri,
        )
        for a in agents
    ]

    return DiscoveryResponse(
        results                = discovered,
        total                  = len(discovered),
        filtered_by_capability = capability,
        min_score_applied      = min_score
    )