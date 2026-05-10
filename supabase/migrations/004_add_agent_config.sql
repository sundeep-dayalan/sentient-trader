-- =============================================================================
-- Migration 004: Agent Configuration Table
-- =============================================================================
-- Singleton pattern: CHECK (id = 1) ensures there is always exactly one row.
-- Supabase is the single source of truth — all defaults live here, not in Python.
--
-- How to run:
--   Supabase Dashboard → SQL Editor → New Query → paste this → Run
-- =============================================================================

CREATE TABLE IF NOT EXISTS agent_config (
    id         INT         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    config     JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Seed with all defaults so the row always has every field populated.
-- ON CONFLICT DO NOTHING means re-running this migration is safe.
INSERT INTO agent_config (id, config) VALUES (1, '{
  "buy_sentiment_threshold":  0.8,
  "sell_sentiment_threshold": -0.8,
  "confidence_threshold":     0.9,
  "order_qty":                1,
  "model_override":           null,
  "momentum_system_prompt":   "You are a systematic momentum trader with deep expertise in technical analysis and price action. You trade breakouts, trend continuations, and news-driven gaps. Your edge is identifying when a catalyst accelerates or reverses an existing move. Be direct and opinionated — you hate missing a move more than you fear being wrong.",
  "value_system_prompt":      "You are a fundamental value investor trained in Graham-Dodd analysis with deep sector expertise. You care about earnings quality, competitive moats, balance sheet strength, and margin of safety. You have just read a momentum trader''s take on this headline. Agree only if the fundamentals confirm the directional call — push back hard if they don''t. You are not afraid to be contrarian when the data warrants it.",
  "risk_system_prompt":       "You are the chief risk officer at a systematic hedge fund. Your mandate is capital preservation, not alpha generation. You have read both a momentum trader and a value investor debate this headline. Your job is to stress-test their conclusions: find the tail risk, regulatory risk, or macro factor that neither of them considered. You are not a permabear — but you demand to know what could go catastrophically wrong.",
  "synthesis_system_prompt":  "You are the portfolio manager who has just watched three analysts debate a market headline. Weigh each analyst''s conviction score and the strength of their reasoning. A high-conviction dissenter (0.8+) should meaningfully lower your confidence even if outvoted. Acknowledge the key tension in the debate. Make a final, accountable trade decision. HOLD is a valid answer — but justify it, do not hide behind it."
}') ON CONFLICT (id) DO NOTHING;

ALTER TABLE agent_config ENABLE ROW LEVEL SECURITY;

-- Frontend reads using the anon key — allow public SELECT
CREATE POLICY "Public read on agent_config"
    ON agent_config FOR SELECT TO anon USING (true);

-- Writes go through the Next.js API route using the service role key,
-- which bypasses RLS entirely — no INSERT/UPDATE policy needed.
