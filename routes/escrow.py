from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from db.base import get_db
from schemas.escrow import (
    EscrowCreateRequest,
    EscrowCreateResponse,
    EscrowReleaseRequest,
    EscrowRefundRequest,
    EscrowStatusResponse
)
from services.router import (
    create_escrow,
    release_escrow,
    refund_escrow,
    get_escrow_status
)
from onchain.client import web3_client

router = APIRouter(prefix="/escrow", tags=["Escrow"])


@router.post("/create", response_model=EscrowCreateResponse)
async def escrow_create(
    request: EscrowCreateRequest,
    db: AsyncSession = Depends(get_db)
) -> EscrowCreateResponse:
    """
    Lock tokens in escrow for a payment to a registered ERC-8004 agent.
    The calling wallet must have approved TrustGuardRouter to spend the token amount.
    """
    try:
        return await create_escrow(
            request       = request,
            payer_address = web3_client.router_address,
            db            = db
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Escrow creation failed: {str(e)}")


@router.post("/release")
async def escrow_release(
    request: EscrowReleaseRequest,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Release escrowed funds to the payee after confirming service delivery.
    Only the authorized router backend can call this.
    """
    try:
        return await release_escrow(request=request, db=db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Escrow release failed: {str(e)}")


@router.post("/refund")
async def escrow_refund(
    request: EscrowRefundRequest,
    db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Refund an escrow to the payer.
    Callable by the payer at any time or by anyone after the timeout.
    """
    try:
        return await refund_escrow(request=request, db=db)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Escrow refund failed: {str(e)}")


@router.get("/status/{escrow_id}", response_model=EscrowStatusResponse)
async def escrow_status(
    escrow_id: str,
    db: AsyncSession = Depends(get_db)
) -> EscrowStatusResponse:
    """Return the current state of an escrow by its ID."""
    return await get_escrow_status(escrow_id=escrow_id, db=db)