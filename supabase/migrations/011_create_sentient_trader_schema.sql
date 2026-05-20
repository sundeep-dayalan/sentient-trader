-- =============================================================================
-- Migration 011: Sentient Trader Dedicated Schema
-- =============================================================================
-- Creates the current app tables in the dedicated sentient_trader schema.
-- This is intended for the self-hosted Supabase cutover where historical rows
-- are imported manually after the app starts pointing at the new instance.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS sentient_trader;

CREATE TABLE IF NOT EXISTS sentient_trader.trades (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at       TIMESTAMPTZ NOT NULL    DEFAULT now(),
    ticker           TEXT        NOT NULL,
    headline         TEXT        NOT NULL,
    sentiment_score  FLOAT4      NOT NULL,
    confidence_score FLOAT4      NOT NULL,
    trade_action     TEXT        NOT NULL
                     CHECK (trade_action IN ('BUY', 'SELL', 'HOLD')),
    order_id         TEXT,
    quantity         INT4        NOT NULL    DEFAULT 1,
    article_url      TEXT,
    is_simulated     BOOLEAN     NOT NULL    DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_created_at
    ON sentient_trader.trades (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_ticker
    ON sentient_trader.trades (ticker);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_is_simulated
    ON sentient_trader.trades (is_simulated);

CREATE TABLE IF NOT EXISTS sentient_trader.trade_decision_traces (
    trade_id       UUID        PRIMARY KEY REFERENCES sentient_trader.trades(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_trace JSONB       NOT NULL,
    reasoning      TEXT,
    article_source TEXT,
    article_id     TEXT
);

CREATE TABLE IF NOT EXISTS sentient_trader.agent_config (
    id         INT         PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    config     JSONB       NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO sentient_trader.agent_config (id, config) VALUES (1, '{
  "buy_sentiment_threshold":  0.8,
  "sell_sentiment_threshold": -0.8,
  "confidence_threshold":     0.9,
  "order_qty":                1,
  "model_override":           null,
  "momentum_system_prompt":   "You are a systematic momentum trader with deep expertise in technical analysis and price action. You trade breakouts, trend continuations, and news-driven gaps. Your edge is identifying when a catalyst accelerates or reverses an existing move. Be direct and opinionated - you hate missing a move more than you fear being wrong.",
  "value_system_prompt":      "You are a fundamental value investor trained in Graham-Dodd analysis with deep sector expertise. You care about earnings quality, competitive moats, balance sheet strength, and margin of safety. You have just read a momentum trader''s take on this headline. Agree only if the fundamentals confirm the directional call - push back hard if they don''t. You are not afraid to be contrarian when the data warrants it.",
  "risk_system_prompt":       "You are the chief risk officer at a systematic hedge fund. Your mandate is capital preservation, not alpha generation. You have read both a momentum trader and a value investor debate this headline. Your job is to stress-test their conclusions: find the tail risk, regulatory risk, or macro factor that neither of them considered. You are not a permabear - but you demand to know what could go catastrophically wrong.",
  "synthesis_system_prompt":  "You are the portfolio manager who has just watched three analysts debate a market headline. Weigh each analyst''s conviction score and the strength of their reasoning. A high-conviction dissenter (0.8+) should meaningfully lower your confidence even if outvoted. Acknowledge the key tension in the debate. Make a final, accountable trade decision. HOLD is a valid answer - but justify it, do not hide behind it."
}') ON CONFLICT (id) DO NOTHING;

ALTER TABLE sentient_trader.trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.trade_decision_traces ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.agent_config ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public read access on trades" ON sentient_trader.trades;
CREATE POLICY "Public read access on trades"
    ON sentient_trader.trades
    FOR SELECT
    TO anon
    USING (true);

DROP POLICY IF EXISTS "Public read access on trade_decision_traces" ON sentient_trader.trade_decision_traces;
CREATE POLICY "Public read access on trade_decision_traces"
    ON sentient_trader.trade_decision_traces
    FOR SELECT
    TO anon
    USING (true);

DROP POLICY IF EXISTS "Public read on agent_config" ON sentient_trader.agent_config;
CREATE POLICY "Public read on agent_config"
    ON sentient_trader.agent_config
    FOR SELECT
    TO anon
    USING (true);

DROP POLICY IF EXISTS "Authenticated update on agent_config" ON sentient_trader.agent_config;
CREATE POLICY "Authenticated update on agent_config"
    ON sentient_trader.agent_config
    FOR UPDATE
    TO authenticated
    USING (auth.uid() IS NOT NULL)
    WITH CHECK (auth.uid() IS NOT NULL);

GRANT USAGE ON SCHEMA sentient_trader TO anon, authenticated, service_role;
GRANT SELECT ON sentient_trader.trades TO anon, authenticated;
GRANT SELECT ON sentient_trader.trade_decision_traces TO anon, authenticated;
GRANT SELECT ON sentient_trader.agent_config TO anon, authenticated;
GRANT UPDATE ON sentient_trader.agent_config TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA sentient_trader TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA sentient_trader
    GRANT SELECT ON TABLES TO anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA sentient_trader
    GRANT ALL ON TABLES TO service_role;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_publication
        WHERE pubname = 'supabase_realtime'
    ) AND NOT EXISTS (
        SELECT 1
        FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'sentient_trader'
          AND tablename = 'trades'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE sentient_trader.trades;
    END IF;
END $$;
