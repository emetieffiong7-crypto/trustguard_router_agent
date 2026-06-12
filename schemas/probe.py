from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class ProbeRequest(BaseModel):
    agent_address: str = Field(..., description="Wallet address of the agent to probe")
    agent_id: Optional[int] = Field(
        None,
        description="ERC-8004 agentId. If omitted the backend resolves it from the registry."
    )
    capability_type: Optional[str] = Field(
        None,
        description="Specific capability to probe: a2a, x402, card. Probes all if omitted."
    )


class ProbeResult(BaseModel):
    agent_address: str
    agent_id: int
    overall_passed: bool
    card_reachable: bool
    a2a_passed: Optional[bool]
    x402_passed: Optional[bool]
    self_verified: bool
    self_proof_fresh: bool
    trust_score: int
    evidence: str
    response_time_ms: Optional[int]
    posted_onchain: bool
    tx_hash: Optional[str]
    probed_at: datetime