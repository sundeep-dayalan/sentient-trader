-- =============================================================================
-- Migration 003: Multi-Persona Committee Debate Storage
-- =============================================================================
-- Adds two columns to the trades table:
--
--   committee_debate  JSONB  — stores the three persona opinions produced by
--                             the LLM committee before consensus is reached.
--                             Shape: [{"name": str, "stance": str, "view": str}, ...]
--                             Null for rows written before this migration.
--
--   is_simulated      BOOL   — marks rows injected by the Simulate button vs.
--                             real live news headlines. Was being written by
--                             the backend but was never added to the schema.
--
-- How to run:
--   Supabase Dashboard → SQL Editor → New Query → paste this → Run
-- =============================================================================

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS committee_debate JSONB,
    ADD COLUMN IF NOT EXISTS is_simulated     BOOLEAN NOT NULL DEFAULT false;

-- Index for fast filtering of simulated vs. live runs on the dashboard
CREATE INDEX IF NOT EXISTS idx_trades_is_simulated
    ON trades (is_simulated);
