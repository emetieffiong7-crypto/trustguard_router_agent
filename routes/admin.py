import secrets
from fastapi import APIRouter, Depends, HTTPException, Header, Request, logger
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime

from db.base import get_db
from db.models import ApiKey
from config import settings

router = APIRouter(prefix="/admin", tags=["Admin"])


def _require_master_key(x_trustguard_api_key: str = Header(...)):
    """Only the master key from .env can call admin endpoints."""
    if x_trustguard_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="Invalid master key")


@router.post("/keys")
async def generate_api_key(
    label: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Generate a new API key for a developer or agent.
    Requires the master API key in the x-trustguard-api-key header.
    """
    new_key = secrets.token_urlsafe(32)

    api_key = ApiKey(key=new_key, label=label)
    db.add(api_key)
    await db.commit()

    return {
        "key":        new_key,
        "label":      label,
        "created_at": api_key.created_at.isoformat(),
        "message":    "Store this key securely. It will not be shown again."
    }

@router.post("/keys/register")
async def register_for_api_key(
    label:   str,
    email:   str = "",
    purpose: str = "",
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Self-service API key registration for developers and agents.
    No master key required — anyone can request a key.
    Keys are active immediately.

    For agents: pass your agent address as the label.
    For developers: pass your name/project as the label.
    """
    import secrets
    from db.models import ApiKey

    new_key = secrets.token_urlsafe(32)

    api_key = ApiKey(
        key        = new_key,
        label      = f"{label} | {purpose}" if purpose else label,
        is_active  = True,
    )
    db.add(api_key)
    await db.commit()

    logger.info(f"New API key issued: label='{label}' purpose='{purpose}'")

    return {
        "key":        new_key,
        "label":      label,
        "created_at": api_key.created_at.isoformat(),
        "rate_limit": "100 requests per minute",
        "usage":      "Include as x-trustguard-api-key header in requests",
        "message":    "Store this key securely. It will not be shown again.",
        "docs":       "https://your-railway-app.up.railway.app/docs"
    }


@router.get("/keys/me")
async def my_key_info(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Returns information about the currently authenticated key.
    Works for both API keys and Self Agent ID authentication.
    """
    auth_type = getattr(request.state, "auth_type", "unknown")

    if auth_type == "self_agent":
        agent_info = getattr(request.state, "agent_info", {})
        return {
            "auth_type":     "self_agent",
            "agent_address": agent_info.get("agentAddress"),
            "agent_id":      agent_info.get("agentId"),
            "verified":      agent_info.get("valid", False),
            "rate_limit":    "200 requests per minute",
        }

    if auth_type == "master":
        return {
            "auth_type":  "master",
            "rate_limit": "1000 requests per minute",
            "access":     "full admin access",
        }

    if auth_type == "api_key":
        key_info = getattr(request.state, "key_info", {})
        return {
            "auth_type":  "api_key",
            "label":      key_info.get("label"),
            "key_id":     key_info.get("id"),
            "rate_limit": "100 requests per minute",
        }

    return {"auth_type": "unknown"}

@router.get("/keys")
async def list_api_keys(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> list:
    """List all issued API keys (keys are masked, only metadata shown)."""
    result = await db.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
    keys   = result.scalars().all()

    return [
        {
            "id":            k.id,
            "label":         k.label,
            "key_preview":   k.key[:8] + "...",
            "is_active":     k.is_active,
            "created_at":    k.created_at.isoformat(),
            "last_used_at":  k.last_used_at.isoformat() if k.last_used_at else None,
            "request_count": k.request_count,
        }
        for k in keys
    ]


@router.delete("/keys/{key_id}")
async def revoke_api_key(
    key_id: int,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """Deactivate an API key without deleting it."""
    result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
    key    = result.scalar_one_or_none()

    if not key:
        raise HTTPException(status_code=404, detail="Key not found")

    key.is_active = False
    await db.commit()

    return {"message": f"Key {key_id} ({key.label}) revoked"}

@router.post("/enrich/contracts")
async def enrich_from_contracts(
    limit:        int  = 200,
    only_missing: bool = True,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Enrich agent database with reputation and Self data
    fetched directly from ERC-8004 contracts and Self REST API.
    Zero gas cost. Run this to improve scores without waiting for subgraph.
    """
    import asyncio
    from scripts.backfill_agents import enrich_agents_with_contract_data

    asyncio.create_task(
        enrich_agents_with_contract_data(
            db_session   = db,
            limit        = limit,
            only_missing = only_missing,
        )
    )

    return {
        "message":     "Contract enrichment started in background",
        "limit":       limit,
        "only_missing": only_missing,
        "gas_cost":    "zero — all read operations",
    }


@router.post("/backfill")
async def trigger_backfill(
    batch_size: int = 25,
    start_id:   int = 0,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Trigger agent backfill from ERC-8004 registry.
    Runs in the background so the request returns immediately.
    Check /admin/backfill/status for progress.
    """
    import asyncio
    from scripts.backfill_agents import main as run_backfill

    # Run as background task so HTTP response returns immediately
    asyncio.create_task(run_backfill(batch_size=batch_size, start_id=start_id))

    return {
        "message":    "Backfill started in background",
        "batch_size": batch_size,
        "start_id":   start_id,
        "note":       "Monitor server logs for progress. "
                      "Check GET /admin/agents/count for current count."
    }


@router.post("/verify/batch")
async def batch_verify(
    limit: int = 10,
    min_score: int = 0,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Probe the top N agents that have card URIs but have not been
    verified recently. Posts results onchain.
    """
    from db.models import Agent
    from sqlalchemy import select, or_
    from services.verifier import probe_agent
    from datetime import datetime, timedelta

    # Find agents with card URIs that have not been probed in 24 hours
    cutoff = datetime.utcnow() - timedelta(hours=24)

    result = await db.execute(
        select(Agent).where(
            Agent.card_uri != None,
            Agent.is_blacklisted == False,
            Agent.agent_id != None,
            or_(
                Agent.last_probed_at == None,
                Agent.last_probed_at < cutoff
            )
        ).order_by(
            Agent.trust_score.desc()
        ).limit(limit)
    )
    agents = result.scalars().all()

    results = []
    for agent in agents:
        try:
            probe_result = await probe_agent(
                agent_address = agent.owner_address,
                agent_id      = agent.agent_id,
                db            = db,
                post_onchain  = True,
            )
            results.append({
                "agent_id":      agent.agent_id,
                "address":       agent.owner_address,
                "passed":        probe_result.overall_passed,
                "a2a_passed":    probe_result.a2a_passed,
                "x402_passed":   probe_result.x402_passed,
                "self_verified": probe_result.self_verified,
                "tx_hash":       probe_result.tx_hash,
            })
        except Exception as e:
            results.append({
                "agent_id": agent.agent_id,
                "address":  agent.owner_address,
                "error":    str(e)
            })

    return {
        "verified":   len([r for r in results if r.get("passed")]),
        "total":      len(results),
        "results":    results
    }


@router.get("/agents/count")
async def agent_count(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """Returns total number of agents in the local database."""
    from sqlalchemy import func
    from db.models import Agent

    result = await db.execute(select(func.count(Agent.id)))
    count  = result.scalar()

    return {
        "total_agents":     count,
        "environment":      settings.environment,
        "registry_address": settings.erc8004_identity_registry
    }

@router.get("/agents/stats")
async def agent_stats(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """Breakdown of agent data quality in the local database."""
    from sqlalchemy import func, case
    from db.models import Agent

    result = await db.execute(
        select(
            func.count(Agent.id).label("total"),
            func.count(Agent.card_uri).label("has_card_uri"),
            func.count(Agent.a2a_endpoint).label("has_a2a_endpoint"),
            func.sum(
                case((Agent.supports_x402 == True, 1), else_=0)
            ).label("supports_x402"),
            func.sum(
                case((Agent.self_verified == True, 1), else_=0)
            ).label("self_verified"),
            func.sum(
                case((Agent.is_blacklisted == True, 1), else_=0)
            ).label("blacklisted"),
            func.sum(
                case((Agent.trust_score > 0, 1), else_=0)
            ).label("has_trust_score"),
            func.sum(
                case((Agent.last_probed_at != None, 1), else_=0)
            ).label("probed"),
        )
    )
    row = result.one()

    return {
        "total":            row.total,
        "has_card_uri":     row.has_card_uri,
        "has_a2a_endpoint": row.has_a2a_endpoint,
        "supports_x402":    int(row.supports_x402 or 0),
        "self_verified":    int(row.self_verified or 0),
        "blacklisted":      int(row.blacklisted or 0),
        "has_trust_score":  int(row.has_trust_score or 0),
        "probed":           int(row.probed or 0),
        "environment":      settings.environment,
    }

@router.post("/scoring/run")
async def run_scoring(
    post_onchain: bool = False,
    batch_size:   int  = 100,
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Run a full composite scoring pass over all agents.
    Set post_onchain=true to post qualifying scores to Celo mainnet.
    Runs in the background — check logs for progress.
    """
    import asyncio
    from services.scoring_engine import run_full_scoring_pass

    asyncio.create_task(
        run_full_scoring_pass(
            batch_size   = batch_size,
            post_onchain = post_onchain,
        )
    )

    return {
        "message":      "Scoring pass started in background",
        "post_onchain": post_onchain,
        "batch_size":   batch_size,
        "note":         "Monitor server logs for progress. "
                        "Check GET /admin/agents/stats for score distribution."
    }


@router.get("/scoring/distribution")
async def score_distribution(
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Show distribution of trust scores across all agents.
    Useful for validating the scoring model is not biased.
    """
    from sqlalchemy import case, func, and_
    from db.models import Agent

    result = await db.execute(
        select(
            func.count(Agent.id).label("total"),
            func.avg(Agent.trust_score).label("avg_score"),
            func.max(Agent.trust_score).label("max_score"),
            func.min(Agent.trust_score).label("min_score"),
            func.sum(
                case((Agent.trust_score == 0, 1), else_=0)
            ).label("score_0"),
            func.sum(
                case((
                    and_(Agent.trust_score > 0, Agent.trust_score <= 20),
                    1
                ), else_=0)
            ).label("score_1_20"),
            func.sum(
                case((
                    and_(Agent.trust_score > 20, Agent.trust_score <= 50),
                    1
                ), else_=0)
            ).label("score_21_50"),
            func.sum(
                case((
                    and_(Agent.trust_score > 50, Agent.trust_score <= 75),
                    1
                ), else_=0)
            ).label("score_51_75"),
            func.sum(
                case((Agent.trust_score > 75, 1), else_=0)
            ).label("score_76_100"),
        )
    )
    row = result.one()

    return {
        "total":       row.total,
        "avg_score":   round(float(row.avg_score or 0), 1),
        "max_score":   row.max_score or 0,
        "min_score":   row.min_score or 0,
        "distribution": {
            "score_0":      int(row.score_0      or 0),
            "score_1_20":   int(row.score_1_20   or 0),
            "score_21_50":  int(row.score_21_50  or 0),
            "score_51_75":  int(row.score_51_75  or 0),
            "score_76_100": int(row.score_76_100 or 0),
        }
    }

@router.post("/enrich/self-verification")
async def enrich_self_verification(
    limit:       int = 100,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_master_key)
) -> dict:
    """
    Query the Self REST API to check verification status for agents
    that are not yet marked as Self verified in the database.
    Updates the database and returns a summary.

    Run this periodically to catch Self verified agents that the
    subgraph has not indexed yet.
    """
    from db.models import Agent
    from sqlalchemy import select
    from self_id.client import self_id_client

    result = await db.execute(
        select(Agent)
        .where(
            Agent.self_verified == False,
            Agent.agent_id != None,
        )
        .order_by(Agent.trust_score.desc())
        .limit(limit)
    )
    candidates = result.scalars().all()

    if not candidates:
        return {"message": "No unverified agents to check", "checked": 0}

    agent_ids  = [a.agent_id for a in candidates]
    id_to_agent = {a.agent_id: a for a in candidates}

    api_results = await self_id_client.get_verification_batch(
        agent_ids    = agent_ids,
        concurrency  = 5,
        delay_between = 0.2,
    )

    newly_verified = 0
    for agent_id, data in api_results.items():
        is_verified = (
            data.get("isVerified") or
            data.get("verified", False)
        )
        agent = id_to_agent.get(agent_id)
        if agent and is_verified:
            agent.self_verified    = True
            agent.self_proof_fresh = data.get("isProofFresh", False)
            newly_verified        += 1

    await db.commit()

    return {
        "checked":         len(candidates),
        "newly_verified":  newly_verified,
        "total_api_hits":  len(api_results),
    }