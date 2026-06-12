from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class EscrowCreateRequest(BaseModel):
    payee_agent_id: int     = Field(..., description="ERC-8004 agentId of the payee agent")
    token: str              = Field(..., description="ERC20 token address (USDm or USDC)")
    amount_wei: str         = Field(..., description="Amount in wei as a string to avoid precision loss")
    timeout_seconds: int    = Field(default=86400, description="Seconds until payer can reclaim funds")
    condition: str          = Field(..., description="Plain text condition string. Its bytes32 hash becomes the conditionHash.")


class EscrowCreateResponse(BaseModel):
    escrow_id: str
    payer: str
    payee: str
    payee_agent_id: int
    token: str
    amount_wei: str
    fee_bps: int
    timeout_at: datetime
    condition_hash: str
    tx_hash: str
    state: str


class EscrowReleaseRequest(BaseModel):
    escrow_id: str  = Field(..., description="The escrow ID returned from createEscrow")
    completion_proof: str = Field(
        ...,
        description="The raw proof string whose bytes32 encoding hashes to the conditionHash"
    )


class EscrowRefundRequest(BaseModel):
    escrow_id: str


class EscrowStatusResponse(BaseModel):
    escrow_id: str
    state: str
    payer: str
    payee: str
    payee_agent_id: int
    token: str
    amount_wei: str
    timeout_at: datetime
    create_tx_hash: Optional[str]
    release_tx_hash: Optional[str]
    refund_tx_hash: Optional[str]
    created_at: datetime
    updated_at: datetime