from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class AgentEndpoint(BaseModel):
    type: str
    url: Optional[str] = None
    address: Optional[str] = None
    chain_id: Optional[int] = None


class AgentCardResponse(BaseModel):
    agent_id: int
    owner_address: str
    wallet_address: Optional[str]
    card_uri: Optional[str]
    name: Optional[str]
    description: Optional[str]
    a2a_endpoint: Optional[str]
    mcp_endpoint: Optional[str]
    supports_x402: bool
    trust_score: int
    self_verified: bool
    self_proof_fresh: bool
    is_blacklisted: bool
    last_probed_at: Optional[datetime]


class AgentScoreResponse(BaseModel):
    agent_address: str
    agent_id: Optional[int]
    trust_score: int
    total_interactions: int
    successful_settlements: int
    failed_verifications: int
    disputes_raised: int
    is_blacklisted: bool
    self_verified: bool
    self_proof_fresh: bool