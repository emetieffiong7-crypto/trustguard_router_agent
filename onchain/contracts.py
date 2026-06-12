from web3 import AsyncWeb3
from eth_abi import encode
from config import settings
import asyncio
from typing import Optional
from onchain.client import web3_client
import time


# ---------------------------------------------------------------------------
# TrustGuardRouter write calls
# ---------------------------------------------------------------------------

async def record_verification_probe(
    agent_address: str,
    agent_id: int,
    passed: bool,
    evidence: str
) -> str:
    """Post a verification probe result onchain. Returns tx hash."""
    nonce     = await web3_client.get_nonce()
    gas_price = await web3_client.get_gas_price()

    tx = await web3_client.trustguard.functions.recordVerificationProbe(
        AsyncWeb3.to_checksum_address(agent_address),
        agent_id,
        passed,
        evidence
    ).build_transaction({
        "from":     web3_client.router_address,
        "nonce":    nonce,
        "gasPrice": gas_price,
        "chainId":  settings.celo_chain_id,
    })

    return await web3_client.send_transaction(tx)


async def release_escrow(escrow_id: str, completion_proof: str) -> str:
    """Release an active escrow after confirming delivery. Returns tx hash."""
    nonce     = await web3_client.get_nonce()
    gas_price = await web3_client.get_gas_price()

    # Convert proof string to bytes32
    proof_bytes = completion_proof.encode("utf-8").ljust(32, b"\x00")[:32]

    tx = await web3_client.trustguard.functions.releaseEscrow(
        bytes.fromhex(escrow_id.removeprefix("0x")),
        proof_bytes
    ).build_transaction({
        "from":     web3_client.router_address,
        "nonce":    nonce,
        "gasPrice": gas_price,
        "chainId":  settings.celo_chain_id,
    })

    return await web3_client.send_transaction(tx)


async def refund_escrow(escrow_id: str) -> str:
    """Trigger a refund on an active escrow. Returns tx hash."""
    nonce     = await web3_client.get_nonce()
    gas_price = await web3_client.get_gas_price()

    tx = await web3_client.trustguard.functions.refundEscrow(
        bytes.fromhex(escrow_id.removeprefix("0x"))
    ).build_transaction({
        "from":     web3_client.router_address,
        "nonce":    nonce,
        "gasPrice": gas_price,
        "chainId":  settings.celo_chain_id,
    })

    return await web3_client.send_transaction(tx)


async def blacklist_agent(agent_address: str, reason: str) -> str:
    """Blacklist an agent onchain. Returns tx hash."""
    nonce     = await web3_client.get_nonce()
    gas_price = await web3_client.get_gas_price()

    tx = await web3_client.trustguard.functions.blacklistAgent(
        AsyncWeb3.to_checksum_address(agent_address),
        reason
    ).build_transaction({
        "from":     web3_client.router_address,
        "nonce":    nonce,
        "gasPrice": gas_price,
        "chainId":  settings.celo_chain_id,
    })

    return await web3_client.send_transaction(tx)


# ---------------------------------------------------------------------------
# TrustGuardRouter read calls
# ---------------------------------------------------------------------------

async def get_router_score(agent_address: str) -> int:
    return await web3_client.trustguard.functions.getRouterScore(
        AsyncWeb3.to_checksum_address(agent_address)
    ).call()


async def get_trust_score(agent_address: str) -> dict:
    result = await web3_client.trustguard.functions.getTrustScore(
        AsyncWeb3.to_checksum_address(agent_address)
    ).call()
    return {
        "total_interactions":    result[0],
        "successful_settlements": result[1],
        "failed_verifications":  result[2],
        "disputes_raised":       result[3],
        "blacklisted":           result[4],
    }


async def get_escrow(escrow_id: str) -> dict:
    result = await web3_client.trustguard.functions.getEscrow(
        bytes.fromhex(escrow_id.removeprefix("0x"))
    ).call()
    return {
        "payer":          result[0],
        "payee":          result[1],
        "payee_agent_id": result[2],
        "token":          result[3],
        "amount":         result[4],
        "timeout":        result[5],
        "condition_hash": result[6].hex(),
        "state":          result[7],
    }


async def is_agent_self_verified_and_fresh(agent_id: int) -> bool:
    return await web3_client.trustguard.functions.isAgentSelfVerifiedAndFresh(
        agent_id
    ).call()


async def get_fee_bps() -> int:
    return await web3_client.trustguard.functions.feeBps().call()


# ---------------------------------------------------------------------------
# ERC-8004 Identity Registry read calls
# ---------------------------------------------------------------------------

async def get_agent_owner(agent_id: int) -> str:
    return await web3_client.identity_registry.functions.ownerOf(agent_id).call()


async def get_agent_wallet(agent_id: int) -> str:
    return await web3_client.identity_registry.functions.getAgentWallet(agent_id).call()


async def get_agent_card_uri(agent_id: int) -> str:
    return await web3_client.identity_registry.functions.tokenURI(agent_id).call()


# ---------------------------------------------------------------------------
# ERC-8004 Reputation Registry read calls
# ---------------------------------------------------------------------------

async def get_reputation_summary(agent_id: int) -> dict:
    """
    Fetch aggregated reputation from the shared ERC-8004 Reputation Registry.
    First gets all clients who gave feedback, then fetches the summary.
    """
    try:
        clients = await web3_client.reputation_registry.functions.getClients(
            agent_id
        ).call()

        if not clients:
            return {"count": 0, "sum": 0, "decimals": 0, "score": 0}

        count, total, decimals = await web3_client.reputation_registry.functions.getSummary(
            agent_id, clients
        ).call()

        score = int((total / count)) if count > 0 else 0
        return {
            "count":    count,
            "sum":      total,
            "decimals": decimals,
            "score":    score
        }
    except Exception:
        return {"count": 0, "sum": 0, "decimals": 0, "score": 0}


# ---------------------------------------------------------------------------
# Self Agent Registry read calls
# ---------------------------------------------------------------------------

async def check_self_verification(owner_address: str) -> dict:
    """
    Check Self Agent ID verification status for an agent owner address.
    agentKey = bytes32(uint256(uint160(ownerAddress))) as the Self registry expects.
    """
    try:
        agent_key = bytes.fromhex(
            owner_address.lower().removeprefix("0x").zfill(64)
        )

        is_verified = await web3_client.self_registry.functions.isVerifiedAgent(
            agent_key
        ).call()

        if not is_verified:
            return {
                "verified":          False,
                "proof_fresh":       False,
                "proof_expires_at":  None,
                "self_agent_id":     None
            }

        self_agent_id = await web3_client.self_registry.functions.getAgentId(
            agent_key
        ).call()

        proof_fresh      = await web3_client.self_registry.functions.isProofFresh(
            self_agent_id
        ).call()
        proof_expires_at = await web3_client.self_registry.functions.proofExpiresAt(
            self_agent_id
        ).call()

        return {
            "verified":         True,
            "proof_fresh":      proof_fresh,
            "proof_expires_at": proof_expires_at,
            "self_agent_id":    self_agent_id
        }

    except Exception:
        return {
            "verified":         False,
            "proof_fresh":      False,
            "proof_expires_at": None,
            "self_agent_id":    None
        }
    

async def get_self_verification_with_api_fallback(
    agent_id:      int,
    owner_address: str,
) -> dict:
    """
    Get Self verification status using both onchain RPC and Self REST API.
    The REST API is used as enrichment and fallback — it returns richer data
    including proof provider, verification strength, and sybil metrics.

    Returns a unified verification dict combining both sources.
    """
    # Start both queries concurrently
    onchain_task = asyncio.create_task(
        check_self_verification(owner_address)
    )
    api_task = asyncio.create_task(
        _fetch_self_api_verification(agent_id)
    )

    onchain_result, api_result = await asyncio.gather(
        onchain_task, api_task, return_exceptions=True
    )

    # Handle exceptions from either source
    if isinstance(onchain_result, Exception):
        onchain_result = {
            "verified": False, "proof_fresh": False,
            "proof_expires_at": None, "self_agent_id": None
        }
    if isinstance(api_result, Exception):
        api_result = None

    # Combine: onchain is source of truth for verification status
    # API adds richness where available
    result = {
        "verified":              onchain_result.get("verified", False),
        "proof_fresh":           onchain_result.get("proof_fresh", False),
        "proof_expires_at":      onchain_result.get("proof_expires_at"),
        "self_agent_id":         onchain_result.get("self_agent_id"),
        "verification_strength": None,
        "proof_provider":        None,
        "sybil_count":           None,
        "registered_at":         None,
        "source":                "onchain",
    }

    # If onchain says not verified but API says verified, trust API
    # (subgraph may not have indexed yet)
    if api_result:
        api_verified = api_result.get("isVerified") or api_result.get("verified", False)

        if api_verified and not result["verified"]:
            result["verified"]   = True
            result["proof_fresh"] = api_result.get("isProofFresh", False)
            result["source"]     = "self_api_fallback"

        # Enrich with API-only fields regardless
        result["verification_strength"] = api_result.get("verificationStrength")
        result["proof_provider"]        = api_result.get("proofProvider")
        result["sybil_count"]           = api_result.get("agentCountForHuman")
        result["registered_at"]         = api_result.get("registeredAt")

        if not result["source"] == "self_api_fallback":
            result["source"] = "onchain+api"

    return result


async def _fetch_self_api_verification(agent_id: int) -> Optional[dict]:
    """Helper that calls Self REST API for verification data."""
    try:
        from self_id.client import self_id_client
        return await self_id_client.verify_agent(agent_id)
    except Exception:
        return None