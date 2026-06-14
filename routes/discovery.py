# from fastapi import APIRouter, Depends, Query
# from typing import Optional
# from sqlalchemy.ext.asyncio import AsyncSession

# from db.base import get_db
# from schemas.discovery import DiscoveryResponse
# from services.discovery import discover_agents

# router = APIRouter(prefix="/discover", tags=["Discovery"])


# @router.get("", response_model=DiscoveryResponse)
# async def discover(
#     capability:          Optional[str] = Query(None, description="Filter by capability: a2a, x402, payments"),
#     min_score:           int           = Query(0,    description="Minimum TrustGuard score (0-100)"),
#     self_verified_only:  bool          = Query(False, description="Only return Self-verified agents"),
#     limit:               int           = Query(10,   description="Maximum number of results", le=50),
#     db: AsyncSession = Depends(get_db)
# ) -> DiscoveryResponse:
#     """
#     Discover registered ERC-8004 agents ranked by TrustGuard score.
#     Optionally filter by capability, minimum score, or Self verification status.
#     """
#     return await discover_agents(
#         capability         = capability,
#         min_score          = min_score,
#         self_verified_only = self_verified_only,
#         limit              = limit,
#         db                 = db
#     )

from fastapi import APIRouter, Depends, Query, HTTPException
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_db
from schemas.discovery import DiscoveryResponse
from services.discovery import discover_agents, get_agent_profile

router = APIRouter(prefix="/discover", tags=["Discovery"])


@router.get("", response_model=DiscoveryResponse)
async def discover(
    capability:         Optional[str] = Query(None),
    min_score:          int           = Query(0),
    self_verified_only: bool          = Query(False),
    limit:              int           = Query(10, le=50),
    db: AsyncSession = Depends(get_db)
) -> DiscoveryResponse:
    """
    Discover registered ERC-8004 agents ranked by TrustGuard score.
    """
    return await discover_agents(
        capability         = capability,
        min_score          = min_score,
        self_verified_only = self_verified_only,
        limit              = limit,
        db                 = db
    )


@router.get("/agent")
async def discover_single_agent(
    address:  Optional[str] = Query(None, description="Agent wallet or owner address"),
    agent_id: Optional[int] = Query(None, description="ERC-8004 agentId"),
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Get a full intelligence profile for a specific agent.
    Pass either an address or an agentId — the other is resolved automatically.

    Returns everything TrustGuard knows about this agent:
    onchain identity, Self verification, reputation, endpoints,
    trust score breakdown, and probe history.
    """
    if not address and agent_id is None:
        raise HTTPException(
            status_code=400,
            detail="Provide either address or agent_id as a query parameter."
        )

    profile = await get_agent_profile(
        address  = address,
        agent_id = agent_id,
        db       = db
    )

    if profile is None:
        raise HTTPException(
            status_code=404,
            detail="Agent not found in ERC-8004 registry or local database."
        )

    return profile

@router.get("/search")
async def search_agents(
    q:     str = Query(..., description="Agent name or description to search for"),
    limit: int = Query(10, le=50),
    db: AsyncSession = Depends(get_db)
) -> DiscoveryResponse:
    """
    Search agents by name or description.
    Searches local database — instant response, no external calls.
    """
    from services.discovery import search_agents_by_name
    return await search_agents_by_name(query=q, limit=limit, db=db)