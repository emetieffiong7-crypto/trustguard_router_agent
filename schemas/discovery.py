from pydantic import BaseModel
from typing import Optional, List


class DiscoveryRequest(BaseModel):
    capability: Optional[str]  = None   # a2a, x402, payments, fx — filters by capability tag
    min_score: Optional[int]   = 0
    self_verified_only: bool   = False
    limit: int                 = 10


class DiscoveredAgent(BaseModel):
    agent_id: int
    owner_address: str
    wallet_address: Optional[str]
    name: Optional[str]
    description: Optional[str]
    a2a_endpoint: Optional[str]
    supports_x402: bool
    trust_score: int
    self_verified: bool
    self_proof_fresh: bool
    total_interactions: int
    success_rate: float
    card_uri: Optional[str]


class DiscoveryResponse(BaseModel):
    results: List[DiscoveredAgent]
    total: int
    filtered_by_capability: Optional[str]
    min_score_applied: int