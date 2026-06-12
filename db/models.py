from datetime import datetime
from sqlalchemy import (
    String, Integer, BigInteger, Boolean,
    DateTime, Text, Numeric, Index
)
from sqlalchemy.orm import Mapped, mapped_column
from db.base import Base


class Agent(Base):
    """
    Local cache of ERC-8004 agent metadata and trust scores.
    Not the source of truth — the contract and registry are.
    Used to avoid repeated RPC calls during discovery and probing.
    """
    __tablename__ = "agents"

    id: Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ERC-8004 identity
    agent_id: Mapped[int]        = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    owner_address: Mapped[str]   = mapped_column(String(42), nullable=False, index=True)
    wallet_address: Mapped[str]  = mapped_column(String(42), nullable=True)
    card_uri: Mapped[str]        = mapped_column(Text, nullable=True)

    # Parsed from agent card JSON
    name: Mapped[str]            = mapped_column(String(255), nullable=True)
    description: Mapped[str]     = mapped_column(Text, nullable=True)
    a2a_endpoint: Mapped[str]    = mapped_column(Text, nullable=True)
    mcp_endpoint: Mapped[str]    = mapped_column(Text, nullable=True)
    supports_x402: Mapped[bool]  = mapped_column(Boolean, default=False)

    # Trust signals
    trust_score: Mapped[int]           = mapped_column(Integer, default=0)
    self_verified: Mapped[bool]        = mapped_column(Boolean, default=False)
    self_proof_fresh: Mapped[bool]     = mapped_column(Boolean, default=False)
    self_proof_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    is_blacklisted: Mapped[bool]       = mapped_column(Boolean, default=False)
    consecutive_failures: Mapped[int]  = mapped_column(Integer, default=0)

    # Score breakdown — stored for transparency and debugging
    score_age_component: Mapped[int]   = mapped_column(Integer, default=0, nullable=True)
    score_card_component: Mapped[int]  = mapped_column(Integer, default=0, nullable=True)
    score_rep_component: Mapped[int]   = mapped_column(Integer, default=0, nullable=True)
    score_self_component: Mapped[int]  = mapped_column(Integer, default=0, nullable=True)
    score_probe_component: Mapped[int] = mapped_column(Integer, default=0, nullable=True)

    # Timestamps
    # registered_at — actual onchain registration time from subgraph or estimated
    # first_seen_at — when TrustGuard first became aware of this agent
    registered_at: Mapped[datetime]   = mapped_column(DateTime, nullable=True, index=True)
    first_seen_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    last_probed_at: Mapped[datetime]  = mapped_column(DateTime, nullable=True)
    last_scored_at: Mapped[datetime]  = mapped_column(DateTime, nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_agents_trust_score",   "trust_score"),
        Index("ix_agents_self_verified", "self_verified"),
        Index("ix_agents_registered_at", "registered_at"),
    )


class Escrow(Base):
    """
    Local mirror of escrow state. The contract is the source of truth.
    """
    __tablename__ = "escrows"

    id: Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    escrow_id: Mapped[str]        = mapped_column(String(66), unique=True, nullable=False, index=True)
    payer_address: Mapped[str]    = mapped_column(String(42), nullable=False, index=True)
    payee_address: Mapped[str]    = mapped_column(String(42), nullable=False, index=True)
    payee_agent_id: Mapped[int]   = mapped_column(BigInteger, nullable=False)
    token_address: Mapped[str]    = mapped_column(String(42), nullable=False)
    amount: Mapped[str]           = mapped_column(Numeric(precision=78, scale=0), nullable=False)
    fee_bps: Mapped[int]          = mapped_column(Integer, nullable=False)
    state: Mapped[str]            = mapped_column(String(20), default="ACTIVE", index=True)
    condition_hash: Mapped[str]   = mapped_column(String(66), nullable=False)
    timeout_at: Mapped[datetime]  = mapped_column(DateTime, nullable=False)
    create_tx_hash: Mapped[str]   = mapped_column(String(66), nullable=True)
    release_tx_hash: Mapped[str]  = mapped_column(String(66), nullable=True)
    refund_tx_hash: Mapped[str]   = mapped_column(String(66), nullable=True)
    created_at: Mapped[datetime]  = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime]  = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_escrows_state",          "state"),
        Index("ix_escrows_payee_agent_id", "payee_agent_id"),
    )


class Probe(Base):
    """
    History of every verification probe run by TrustGuard.
    """
    __tablename__ = "probes"

    id: Mapped[int]               = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_address: Mapped[str]    = mapped_column(String(42), nullable=False, index=True)
    agent_id: Mapped[int]         = mapped_column(BigInteger, nullable=False)
    endpoint_probed: Mapped[str]  = mapped_column(Text, nullable=True)
    probe_type: Mapped[str]       = mapped_column(String(20), nullable=False)
    passed: Mapped[bool]          = mapped_column(Boolean, nullable=False)
    evidence: Mapped[str]         = mapped_column(Text, nullable=True)
    response_code: Mapped[int]    = mapped_column(Integer, nullable=True)
    response_time_ms: Mapped[int] = mapped_column(Integer, nullable=True)
    posted_onchain: Mapped[bool]  = mapped_column(Boolean, default=False)
    tx_hash: Mapped[str]          = mapped_column(String(66), nullable=True)
    probed_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_probes_agent_passed", "agent_address", "passed"),
    )


class SelfCredential(Base):
    """
    Stores TrustGuard's own Self Agent ID credentials after registration.
    """
    __tablename__ = "self_credentials"

    id: Mapped[int]                 = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_key: Mapped[str]          = mapped_column(String(132), unique=True, nullable=False)
    public_key: Mapped[str]         = mapped_column(Text, nullable=False)
    network: Mapped[str]            = mapped_column(String(20), nullable=False)
    self_agent_id: Mapped[int]      = mapped_column(BigInteger, nullable=True)
    verified: Mapped[bool]          = mapped_column(Boolean, default=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    proof_expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)


class ApiKey(Base):
    """
    Per-caller API keys for developers and agents accessing TrustGuard.
    """
    __tablename__ = "api_keys"

    id: Mapped[int]              = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str]             = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str]           = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool]      = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_used_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    request_count: Mapped[int]   = mapped_column(Integer, default=0)