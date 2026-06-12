from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from db.models import Probe, Agent
from onchain.contracts import get_router_score, get_trust_score
from schemas.agent import AgentScoreResponse


async def get_agent_score(
    agent_address: str,
    db: AsyncSession
) -> AgentScoreResponse:
    """
    Return a complete score profile for an agent.
    Combines onchain trust score with local probe history
    and Self verification status from the cache.
    """

    # Get onchain trust score
    onchain_score = await get_trust_score(agent_address)

    # Get cached agent metadata for Self verification status
    result = await db.execute(
        select(Agent).where(
            Agent.owner_address == agent_address.lower()
        )
    )
    cached = result.scalar_one_or_none()

    return AgentScoreResponse(
        agent_address          = agent_address,
        agent_id               = cached.agent_id if cached else None,
        trust_score            = await get_router_score(agent_address),
        total_interactions     = onchain_score["total_interactions"],
        successful_settlements = onchain_score["successful_settlements"],
        failed_verifications   = onchain_score["failed_verifications"],
        disputes_raised        = onchain_score["disputes_raised"],
        is_blacklisted         = onchain_score["blacklisted"],
        self_verified          = cached.self_verified if cached else False,
        self_proof_fresh       = cached.self_proof_fresh if cached else False,
    )


async def get_probe_history(
    agent_address: str,
    limit: int,
    db: AsyncSession
) -> list:
    """Return recent probe history for an agent from local database."""
    result = await db.execute(
        select(Probe)
        .where(Probe.agent_address == agent_address.lower())
        .order_by(Probe.probed_at.desc())
        .limit(limit)
    )
    probes = result.scalars().all()

    return [
        {
            "probe_type":      p.probe_type,
            "passed":          p.passed,
            "evidence":        p.evidence,
            "response_time_ms": p.response_time_ms,
            "posted_onchain":  p.posted_onchain,
            "tx_hash":         p.tx_hash,
            "probed_at":       p.probed_at.isoformat()
        }
        for p in probes
    ]