from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_db
from schemas.probe import ProbeRequest, ProbeResult
from services.verifier import probe_agent

router = APIRouter(prefix="/verify", tags=["Verification"])


@router.post("", response_model=ProbeResult)
async def verify_agent(
    request: ProbeRequest,
    db: AsyncSession = Depends(get_db)
) -> ProbeResult:
    """
    Probe an agent's advertised endpoints and verify their ERC-8004
    and Self Agent ID status. Posts result onchain.
    """
    agent_id = request.agent_id or 0

    return await probe_agent(
        agent_address = request.agent_address,
        agent_id      = agent_id,
        db            = db,
        post_onchain  = True
    )