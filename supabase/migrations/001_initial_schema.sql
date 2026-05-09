-- =============================================================================
-- Sentient Trader: Initial Database Schema
-- =============================================================================
-- How to run:
--   Supabase Dashboard → SQL Editor → New Query → paste this → Run
--
-- After running, enable Realtime so the frontend live feed works:
--   Database → Replication → Tables → enable "trades"
-- =============================================================================


-- The heart of the system: every AI decision gets logged here.
-- Both executed trades (BUY/SELL) and non-trades (HOLD) are recorded
-- so the dashboard can show the agent's full reasoning, not just wins.
CREATE TABLE IF NOT EXISTS trades (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL    DEFAULT now(),

    -- The news article the AI analyzed
    ticker           TEXT        NOT NULL,
    headline         TEXT        NOT NULL,

    -- The AI's structured output
    sentiment_score  FLOAT4      NOT NULL,   -- -1.0 (bearish) to +1.0 (bullish)
    confidence_score FLOAT4      NOT NULL,   -- 0.0 (uncertain) to 1.0 (certain)
    reasoning        TEXT        NOT NULL,   -- AI's explanation in plain English
    trade_action     TEXT        NOT NULL    -- "BUY", "SELL", or "HOLD"
                     CHECK (trade_action IN ('BUY', 'SELL', 'HOLD')),

    -- Order details — only populated when a trade was actually executed
    order_id         TEXT,                   -- Alpaca order UUID (NULL if HOLD)
    quantity         INT4        NOT NULL    DEFAULT 1
);


-- Fast chronological lookups (the dashboard queries the 20 most recent)
CREATE INDEX idx_trades_created_at ON trades (created_at DESC);

-- Useful for per-ticker analytics queries down the line
CREATE INDEX idx_trades_ticker ON trades (ticker);


-- =============================================================================
-- Row Level Security
-- =============================================================================
-- Backend writes using the service role key (bypasses RLS entirely).
-- Frontend reads using the anon key (needs an explicit SELECT policy).

ALTER TABLE trades ENABLE ROW LEVEL SECURITY;

-- Allow the browser to read all trades — no login required.
-- The anon key is safe to expose because this policy only grants SELECT.
CREATE POLICY "Public read access on trades"
    ON trades
    FOR SELECT
    TO anon
    USING (true);

-- The backend (service role) can INSERT without any policy because
-- the service role key bypasses RLS by design.
