-- Run this once on first deploy if you prefer raw SQL over SQLAlchemy's create_all.
-- Railway Postgres supports all of these types natively.

CREATE TABLE IF NOT EXISTS agents (
    id                  SERIAL PRIMARY KEY,
    agent_id            BIGINT UNIQUE NOT NULL,
    owner_address       VARCHAR(42) NOT NULL,
    wallet_address      VARCHAR(42),
    card_uri            TEXT,
    name                VARCHAR(255),
    description         TEXT,
    a2a_endpoint        TEXT,
    mcp_endpoint        TEXT,
    supports_x402       BOOLEAN DEFAULT FALSE,
    trust_score         INTEGER DEFAULT 0,
    self_verified       BOOLEAN DEFAULT FALSE,
    self_proof_fresh    BOOLEAN DEFAULT FALSE,
    self_proof_expires_at TIMESTAMP,
    is_blacklisted      BOOLEAN DEFAULT FALSE,
    consecutive_failures INTEGER DEFAULT 0,
    first_seen_at       TIMESTAMP DEFAULT NOW(),
    last_probed_at      TIMESTAMP,
    last_updated_at     TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_agents_agent_id       ON agents(agent_id);
CREATE INDEX IF NOT EXISTS ix_agents_owner_address  ON agents(owner_address);
CREATE INDEX IF NOT EXISTS ix_agents_trust_score    ON agents(trust_score);
CREATE INDEX IF NOT EXISTS ix_agents_self_verified  ON agents(self_verified);

CREATE TABLE IF NOT EXISTS escrows (
    id              SERIAL PRIMARY KEY,
    escrow_id       VARCHAR(66) UNIQUE NOT NULL,
    payer_address   VARCHAR(42) NOT NULL,
    payee_address   VARCHAR(42) NOT NULL,
    payee_agent_id  BIGINT NOT NULL,
    token_address   VARCHAR(42) NOT NULL,
    amount          NUMERIC(78, 0) NOT NULL,
    fee_bps         INTEGER NOT NULL,
    state           VARCHAR(20) DEFAULT 'ACTIVE',
    condition_hash  VARCHAR(66) NOT NULL,
    timeout_at      TIMESTAMP NOT NULL,
    create_tx_hash  VARCHAR(66),
    release_tx_hash VARCHAR(66),
    refund_tx_hash  VARCHAR(66),
    created_at      TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_escrows_escrow_id      ON escrows(escrow_id);
CREATE INDEX IF NOT EXISTS ix_escrows_payer          ON escrows(payer_address);
CREATE INDEX IF NOT EXISTS ix_escrows_payee          ON escrows(payee_address);
CREATE INDEX IF NOT EXISTS ix_escrows_state          ON escrows(state);
CREATE INDEX IF NOT EXISTS ix_escrows_payee_agent_id ON escrows(payee_agent_id);

CREATE TABLE IF NOT EXISTS probes (
    id               SERIAL PRIMARY KEY,
    agent_address    VARCHAR(42) NOT NULL,
    agent_id         BIGINT NOT NULL,
    endpoint_probed  TEXT,
    probe_type       VARCHAR(20) NOT NULL,
    passed           BOOLEAN NOT NULL,
    evidence         TEXT,
    response_code    INTEGER,
    response_time_ms INTEGER,
    posted_onchain   BOOLEAN DEFAULT FALSE,
    tx_hash          VARCHAR(66),
    probed_at        TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_probes_agent_address ON probes(agent_address);
CREATE INDEX IF NOT EXISTS ix_probes_probed_at     ON probes(probed_at);

CREATE TABLE IF NOT EXISTS self_credentials (
    id               SERIAL PRIMARY KEY,
    agent_key        VARCHAR(132) UNIQUE NOT NULL,
    public_key       TEXT NOT NULL,
    network          VARCHAR(20) NOT NULL,
    self_agent_id    BIGINT,
    verified         BOOLEAN DEFAULT FALSE,
    registered_at    TIMESTAMP DEFAULT NOW(),
    proof_expires_at TIMESTAMP
);