from datetime import datetime, timedelta
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from web3 import AsyncWeb3
from eth_abi import encode

from config import settings
from db.models import Escrow
from schemas.escrow import (
    EscrowCreateRequest,
    EscrowCreateResponse,
    EscrowReleaseRequest,
    EscrowRefundRequest,
    EscrowStatusResponse
)
from onchain.contracts import (
    get_escrow,
    release_escrow as contract_release_escrow,
    refund_escrow  as contract_refund_escrow,
    get_fee_bps,
    get_agent_wallet,
    get_agent_owner
)
from onchain.client import web3_client


def _compute_condition_hash(condition: str) -> str:
    """
    Compute the bytes32 condition hash from a plain text condition string.
    Must match how releaseEscrow computes it in the contract:
    keccak256(abi.encodePacked(bytes32(condition_bytes)))
    """
    condition_bytes = condition.encode("utf-8").ljust(32, b"\x00")[:32]
    return AsyncWeb3.keccak(condition_bytes).hex()


async def create_escrow(
    request: EscrowCreateRequest,
    payer_address: str,
    db: AsyncSession
) -> EscrowCreateResponse:
    """
    Create an escrow payment on behalf of the payer.
    The payer must have approved TrustGuardRouter to spend the token amount.
    """

    # Resolve payee address from ERC-8004 registry
    try:
        wallet = await get_agent_wallet(request.payee_agent_id)
        payee  = wallet if wallet and wallet != "0x" + "0" * 40 else \
                 await get_agent_owner(request.payee_agent_id)
    except Exception as e:
        raise ValueError(f"Could not resolve payee for agentId {request.payee_agent_id}: {e}")

    condition_hash = _compute_condition_hash(request.condition)
    current_fee    = await get_fee_bps()
    timeout_at     = datetime.utcnow() + timedelta(seconds=request.timeout_seconds)

    # Build and send the createEscrow transaction
    nonce     = await web3_client.get_nonce()
    gas_price = await web3_client.get_gas_price()

    condition_bytes = request.condition.encode("utf-8").ljust(32, b"\x00")[:32]

    tx = await web3_client.trustguard.functions.createEscrow(
        request.payee_agent_id,
        AsyncWeb3.to_checksum_address(request.token),
        int(request.amount_wei),
        request.timeout_seconds,
        condition_bytes
    ).build_transaction({
        "from":     web3_client.router_address,
        "nonce":    nonce,
        "gasPrice": gas_price,
        "chainId":  settings.celo_chain_id,
    })

    tx_hash = await web3_client.send_transaction(tx)

    # Derive escrow ID — matches contract logic
    escrow_id = AsyncWeb3.keccak(
        encode(
            ["address", "uint256", "address", "uint256", "uint256"],
            [
                AsyncWeb3.to_checksum_address(web3_client.router_address),
                request.payee_agent_id,
                AsyncWeb3.to_checksum_address(request.token),
                int(request.amount_wei),
                int(datetime.utcnow().timestamp())
            ]
        )
    ).hex()

    # Persist to local database
    escrow_record = Escrow(
        escrow_id      = escrow_id,
        payer_address  = web3_client.router_address.lower(),
        payee_address  = payee.lower(),
        payee_agent_id = request.payee_agent_id,
        token_address  = request.token.lower(),
        amount         = int(request.amount_wei),
        fee_bps        = current_fee,
        state          = "ACTIVE",
        condition_hash = condition_hash,
        timeout_at     = timeout_at,
        create_tx_hash = tx_hash,
    )
    db.add(escrow_record)
    await db.commit()

    return EscrowCreateResponse(
        escrow_id      = escrow_id,
        payer          = web3_client.router_address,
        payee          = payee,
        payee_agent_id = request.payee_agent_id,
        token          = request.token,
        amount_wei     = request.amount_wei,
        fee_bps        = current_fee,
        timeout_at     = timeout_at,
        condition_hash = condition_hash,
        tx_hash        = tx_hash,
        state          = "ACTIVE"
    )


async def release_escrow(
    request: EscrowReleaseRequest,
    db: AsyncSession
) -> dict:
    """Confirm delivery and release escrowed funds to the payee."""

    tx_hash = await contract_release_escrow(
        request.escrow_id,
        request.completion_proof
    )

    # Update local cache
    result = await db.execute(
        select(Escrow).where(Escrow.escrow_id == request.escrow_id)
    )
    escrow = result.scalar_one_or_none()
    if escrow:
        escrow.state           = "RELEASED"
        escrow.release_tx_hash = tx_hash
        await db.commit()

    return {"escrow_id": request.escrow_id, "state": "RELEASED", "tx_hash": tx_hash}


async def refund_escrow(
    request: EscrowRefundRequest,
    db: AsyncSession
) -> dict:
    """Refund an escrow to the payer."""

    tx_hash = await contract_refund_escrow(request.escrow_id)

    result = await db.execute(
        select(Escrow).where(Escrow.escrow_id == request.escrow_id)
    )
    escrow = result.scalar_one_or_none()
    if escrow:
        escrow.state          = "REFUNDED"
        escrow.refund_tx_hash = tx_hash
        await db.commit()

    return {"escrow_id": request.escrow_id, "state": "REFUNDED", "tx_hash": tx_hash}


async def get_escrow_status(escrow_id: str, db: AsyncSession) -> EscrowStatusResponse:
    """
    Return escrow status. Checks local cache first, falls back to contract read.
    """
    result = await db.execute(
        select(Escrow).where(Escrow.escrow_id == escrow_id)
    )
    escrow = result.scalar_one_or_none()

    if escrow:
        return EscrowStatusResponse(
            escrow_id      = escrow.escrow_id,
            state          = escrow.state,
            payer          = escrow.payer_address,
            payee          = escrow.payee_address,
            payee_agent_id = escrow.payee_agent_id,
            token          = escrow.token_address,
            amount_wei     = str(escrow.amount),
            timeout_at     = escrow.timeout_at,
            create_tx_hash = escrow.create_tx_hash,
            release_tx_hash = escrow.release_tx_hash,
            refund_tx_hash = escrow.refund_tx_hash,
            created_at     = escrow.created_at,
            updated_at     = escrow.updated_at,
        )

    # Fall back to contract read
    contract_data = await get_escrow(escrow_id)
    state_map     = {0: "ACTIVE", 1: "RELEASED", 2: "REFUNDED", 3: "DISPUTED"}

    return EscrowStatusResponse(
        escrow_id       = escrow_id,
        state           = state_map.get(contract_data["state"], "UNKNOWN"),
        payer           = contract_data["payer"],
        payee           = contract_data["payee"],
        payee_agent_id  = contract_data["payee_agent_id"],
        token           = contract_data["token"],
        amount_wei      = str(contract_data["amount"]),
        timeout_at      = datetime.utcfromtimestamp(contract_data["timeout"]),
        create_tx_hash  = None,
        release_tx_hash = None,
        refund_tx_hash  = None,
        created_at      = datetime.utcnow(),
        updated_at      = datetime.utcnow(),
    )