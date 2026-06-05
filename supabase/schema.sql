-- =============================================================================
-- Sentient Trader — Master Database Setup
-- =============================================================================
-- Single-file setup for a fresh Supabase / Postgres environment. Run this once
-- in the Supabase SQL Editor (or via psql) to create the entire application
-- schema: tables, indexes, the seeded agent config, row-level security, the
-- dashboard stats function, and realtime.
--
-- ACCESS MODEL
--   The browser never touches these tables directly. Every read and write goes
--   through the FastAPI backend using the Supabase *service role*. RLS is
--   enabled on every table and no privileges are granted to anon/authenticated,
--   so the public anon key cannot read or write application data. The service
--   role bypasses RLS and is the only client with table access.
--
-- SCHEMA NAME
--   Production uses `sentient_trader`. Local/dev can use a separate schema via
--   SUPABASE_DB_SCHEMA (e.g. `sentient_trader_dev`) — to target a different
--   schema, find/replace `sentient_trader` below before running.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS sentient_trader;
GRANT USAGE ON SCHEMA sentient_trader TO anon, authenticated, service_role;


-- =============================================================================
-- Trading Decisions
-- =============================================================================

CREATE TABLE IF NOT EXISTS sentient_trader.trades (
    id                     UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at             TIMESTAMPTZ NOT NULL    DEFAULT now(),
    ticker                 TEXT        NOT NULL,
    headline               TEXT        NOT NULL,
    sentiment_score        FLOAT4      NOT NULL,
    confidence_score       FLOAT4      NOT NULL,
    trade_action           TEXT        NOT NULL
                           CHECK (trade_action IN ('BUY', 'SELL', 'HOLD')),
    pm_recommendation      TEXT        CHECK (pm_recommendation IN ('BUY', 'SELL', 'HOLD')),
    calibrated_confidence  FLOAT4,
    confidence_cap         FLOAT4,
    risk_should_trade      BOOLEAN,
    executed_action        TEXT        CHECK (executed_action IN ('BUY', 'SELL')),
    order_id               TEXT,
    client_order_id        TEXT,
    order_status           TEXT,
    execution_error        TEXT,
    gate_reason            TEXT,
    decision_path          TEXT,
    processing_started_at  TIMESTAMPTZ,
    processing_finished_at TIMESTAMPTZ,
    quantity               INT4        NOT NULL    DEFAULT 1,
    article_url            TEXT,
    is_simulated           BOOLEAN     NOT NULL    DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_created_at
    ON sentient_trader.trades (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_ticker
    ON sentient_trader.trades (ticker);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_is_simulated
    ON sentient_trader.trades (is_simulated);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_decision_path
    ON sentient_trader.trades (decision_path);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_executed_action
    ON sentient_trader.trades (executed_action)
    WHERE executed_action IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_client_order_id
    ON sentient_trader.trades (client_order_id)
    WHERE client_order_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS sentient_trader.trade_decision_traces (
    trade_id       UUID        PRIMARY KEY REFERENCES sentient_trader.trades(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_trace JSONB       NOT NULL,
    reasoning      TEXT,
    article_source TEXT,
    article_id     TEXT
);

CREATE TABLE IF NOT EXISTS sentient_trader.signal_outcomes (
    trade_id        UUID PRIMARY KEY REFERENCES sentient_trader.trades(id) ON DELETE CASCADE,
    ticker          TEXT        NOT NULL,
    signal_at       TIMESTAMPTZ NOT NULL,
    signal_price    FLOAT8,
    price_15m       FLOAT8,
    return_15m      FLOAT8,
    price_1h        FLOAT8,
    return_1h       FLOAT8,
    price_eod       FLOAT8,
    return_eod      FLOAT8,
    label_status    TEXT        NOT NULL DEFAULT 'LABELED'
                    CHECK (label_status IN ('LABELED', 'PARTIAL', 'NO_BARS', 'ERROR')),
    label_error     TEXT,
    label_attempts  INT4        NOT NULL DEFAULT 0,
    last_attempt_at TIMESTAMPTZ,
    labeled_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_signal_outcomes_ticker
    ON sentient_trader.signal_outcomes (ticker);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_signal_outcomes_signal_at
    ON sentient_trader.signal_outcomes (signal_at DESC);

CREATE TABLE IF NOT EXISTS sentient_trader.scheduler_runs (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    scheduler_name TEXT        NOT NULL,
    status         TEXT        NOT NULL DEFAULT 'RUNNING'
                   CHECK (status IN ('RUNNING', 'SUCCESS', 'ERROR')),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    duration_ms    INT4,
    rows_processed INT4,
    worker_name    TEXT,
    error_message  TEXT,
    metadata       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_scheduler_runs_name_started
    ON sentient_trader.scheduler_runs (scheduler_name, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentient_trader_scheduler_runs_status_started
    ON sentient_trader.scheduler_runs (status, started_at DESC);


-- =============================================================================
-- Agent Configuration  (single row, id = 1 — the only source of trading params)
-- =============================================================================

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
  "llm_provider": {
    "type": "groq-always-free"
  },
  "momentum_system_prompt":   "You are a systematic momentum trader with deep expertise in technical analysis and price action. You trade breakouts, trend continuations, and news-driven gaps. Your edge is identifying when a catalyst accelerates or reverses an existing move. Be direct and opinionated - you hate missing a move more than you fear being wrong.",
  "value_system_prompt":      "You are a fundamental value investor trained in Graham-Dodd analysis with deep sector expertise. You care about earnings quality, competitive moats, balance sheet strength, and margin of safety. You have just read a momentum trader''s take on this headline. Agree only if the fundamentals confirm the directional call - push back hard if they don''t. You are not afraid to be contrarian when the data warrants it.",
  "risk_system_prompt":       "You are the chief risk officer at a systematic hedge fund. Your mandate is capital preservation, not alpha generation. You have read both a momentum trader and a value investor debate this headline. Your job is to stress-test their conclusions: find the tail risk, regulatory risk, or macro factor that neither of them considered. You are not a permabear - but you demand to know what could go catastrophically wrong.",
  "synthesis_system_prompt":  "You are the portfolio manager who has just watched three analysts debate a market headline. Weigh each analyst''s conviction score and the strength of their reasoning. A high-conviction dissenter (0.8+) should meaningfully lower your confidence even if outvoted. Acknowledge the key tension in the debate. Make a final, accountable trade decision. HOLD is a valid answer - but justify it, do not hide behind it."
}') ON CONFLICT (id) DO NOTHING;


-- =============================================================================
-- Durable News Ingestion  (store-first, then publish to the Redis stream)
-- =============================================================================

CREATE TABLE IF NOT EXISTS sentient_trader.raw_news_articles (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    received_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    provider             TEXT        NOT NULL,
    source_article_id    TEXT,
    article_source       TEXT,
    headline             TEXT        NOT NULL,
    normalized_headline  TEXT        NOT NULL,
    headline_hash        TEXT        NOT NULL,
    summary              TEXT,
    content              TEXT,
    author               TEXT,
    article_url          TEXT,
    url_hash             TEXT,
    symbols              TEXT[]      NOT NULL DEFAULT '{}',
    source_created_at    TIMESTAMPTZ NOT NULL,
    source_updated_at    TIMESTAMPTZ,
    raw_payload          JSONB       NOT NULL DEFAULT '{}'::jsonb,
    canonical_article_id UUID        REFERENCES sentient_trader.raw_news_articles(id),
    dedupe_reason        TEXT,
    is_duplicate         BOOLEAN     NOT NULL DEFAULT false
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sentient_raw_news_provider_article_id
    ON sentient_trader.raw_news_articles (provider, source_article_id)
    WHERE source_article_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sentient_raw_news_url_hash
    ON sentient_trader.raw_news_articles (url_hash)
    WHERE url_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_sentient_raw_news_headline_hash_created_at
    ON sentient_trader.raw_news_articles (headline_hash, source_created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentient_raw_news_symbols
    ON sentient_trader.raw_news_articles USING GIN (symbols);

CREATE TABLE IF NOT EXISTS sentient_trader.news_article_symbols (
    article_id UUID        NOT NULL REFERENCES sentient_trader.raw_news_articles(id) ON DELETE CASCADE,
    ticker     TEXT        NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (article_id, ticker)
);

CREATE TABLE IF NOT EXISTS sentient_trader.news_outbox (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    article_id      UUID        NOT NULL REFERENCES sentient_trader.raw_news_articles(id) ON DELETE CASCADE,
    ticker          TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'RETRYING', 'FAILED', 'PUBLISHED')),
    attempts        INT         NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    redis_entry_id  TEXT,
    published_at    TIMESTAMPTZ,
    last_error      TEXT,
    message_payload JSONB       NOT NULL,
    UNIQUE (article_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_sentient_news_outbox_status_next_attempt
    ON sentient_trader.news_outbox (status, next_attempt_at);

CREATE TABLE IF NOT EXISTS sentient_trader.ingestion_events (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type TEXT        NOT NULL,
    article_id UUID        REFERENCES sentient_trader.raw_news_articles(id) ON DELETE SET NULL,
    outbox_id  UUID        REFERENCES sentient_trader.news_outbox(id) ON DELETE SET NULL,
    ticker     TEXT,
    detail     JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_sentient_ingestion_events_created_at
    ON sentient_trader.ingestion_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sentient_ingestion_events_type
    ON sentient_trader.ingestion_events (event_type);

CREATE TABLE IF NOT EXISTS sentient_trader.ingestion_cursors (
    provider                   TEXT        PRIMARY KEY,
    updated_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_article_created_at    TIMESTAMPTZ,
    last_source_article_id     TEXT,
    last_websocket_article_at  TIMESTAMPTZ,
    last_backfill_completed_at TIMESTAMPTZ
);


-- =============================================================================
-- Dashboard Stats  (exact single-pass aggregation for the /stats endpoint)
-- =============================================================================
-- Computes every dashboard metric in one atomic query so counts are exact and
-- cheap regardless of table size (PostgREST caps row responses, so counting in
-- the app understated and jittered the numbers).

CREATE OR REPLACE FUNCTION sentient_trader.dashboard_stats()
RETURNS jsonb
LANGUAGE sql
STABLE
AS $$
  SELECT jsonb_build_object(
    'analyzed', count(*),
    'executed', count(*) FILTER (
        WHERE coalesce(nullif(trim(executed_action), ''),
                       nullif(trim(order_id), '')) IS NOT NULL),
    'buyOrders', count(*) FILTER (
        WHERE coalesce(pm_recommendation, trade_action) = 'BUY'),
    'sellOrders', count(*) FILTER (
        WHERE coalesce(pm_recommendation, trade_action) = 'SELL'),
    'riskGated', count(*) FILTER (
        WHERE (risk_should_trade IS FALSE
               AND coalesce(nullif(trim(executed_action), ''),
                            nullif(trim(order_id), '')) IS NULL)
           OR trade_action = 'HOLD'),
    'preScreened', count(*) FILTER (WHERE decision_path = 'pre_screen'),
    'fullDebates', count(*) FILTER (WHERE decision_path = 'full_debate'),
    'avgSentiment', coalesce(sum(sentiment_score), 0) / nullif(count(*), 0)
  )
  FROM sentient_trader.trades;
$$;


-- =============================================================================
-- Security  (backend-only access — see ACCESS MODEL at the top)
-- =============================================================================
-- RLS is enabled on every table. Only the service role (used exclusively by the
-- FastAPI backend) is granted table access. anon/authenticated get nothing, so
-- the public anon key embedded in the frontend cannot read or write app data.

ALTER TABLE sentient_trader.trades                ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.trade_decision_traces ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.signal_outcomes       ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.scheduler_runs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.agent_config          ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.raw_news_articles     ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.news_article_symbols  ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.news_outbox           ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.ingestion_events      ENABLE ROW LEVEL SECURITY;
ALTER TABLE sentient_trader.ingestion_cursors     ENABLE ROW LEVEL SECURITY;

-- Service role: full access (bypasses RLS). The backend is the only client.
GRANT ALL ON ALL TABLES IN SCHEMA sentient_trader TO service_role;
GRANT EXECUTE ON FUNCTION sentient_trader.dashboard_stats() TO service_role;

ALTER DEFAULT PRIVILEGES IN SCHEMA sentient_trader
    GRANT ALL ON TABLES TO service_role;


-- =============================================================================
-- Realtime  (optional — publish trades for live dashboard subscriptions)
-- =============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_publication WHERE pubname = 'supabase_realtime'
    ) AND NOT EXISTS (
        SELECT 1 FROM pg_publication_tables
        WHERE pubname = 'supabase_realtime'
          AND schemaname = 'sentient_trader'
          AND tablename = 'trades'
    ) THEN
        ALTER PUBLICATION supabase_realtime ADD TABLE sentient_trader.trades;
    END IF;
END $$;
