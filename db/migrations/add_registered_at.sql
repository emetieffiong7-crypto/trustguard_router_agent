-- Add registered_at column for actual onchain registration timestamp
ALTER TABLE agents ADD COLUMN registered_at TIMESTAMP;
ALTER TABLE agents ADD COLUMN last_scored_at TIMESTAMP;
ALTER TABLE agents ADD COLUMN score_age_component INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN score_card_component INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN score_rep_component INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN score_self_component INTEGER DEFAULT 0;
ALTER TABLE agents ADD COLUMN score_probe_component INTEGER DEFAULT 0;

-- Index for age-based sorting
CREATE INDEX IF NOT EXISTS ix_agents_registered_at ON agents(registered_at);