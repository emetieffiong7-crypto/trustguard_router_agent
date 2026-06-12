from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_db
from schemas.agent import AgentScoreResponse
from services.scorer import get_agent_score, get_probe_history

router = APIRouter(prefix="/score", tags=["Scoring"])


@router.get("/{agent_address}", response_model=AgentScoreResponse)
async def agent_score(
    agent_address: str,
    db: AsyncSession = Depends(get_db)
) -> AgentScoreResponse:
    """
    Return the full TrustGuard score and trust profile for an agent address.
    Combines onchain data with local probe history and Self verification status.
    """
    return await get_agent_score(agent_address=agent_address, db=db)


@router.get("/{agent_address}/probes")
async def agent_probe_history(
    agent_address: str,
    limit: int = Query(10, le=50),
    db: AsyncSession = Depends(get_db)
) -> list:
    """Return recent verification probe history for an agent."""
    return await get_probe_history(
        agent_address = agent_address,
        limit         = limit,
        db            = db
    )