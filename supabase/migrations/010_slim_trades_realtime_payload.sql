-- =============================================================================
-- Migration 010: Keep Realtime Trades Rows Feed-Only
-- =============================================================================
-- Move detail-only text fields off the Realtime-enabled `trades` table.
-- `article_url` stays on `trades` because the feed exposes a direct article
-- link. Consensus reasoning/source metadata is loaded with the full trace.
-- =============================================================================

ALTER TABLE trade_decision_traces
    ADD COLUMN IF NOT EXISTS reasoning TEXT,
    ADD COLUMN IF NOT EXISTS article_source TEXT,
    ADD COLUMN IF NOT EXISTS article_id TEXT;

INSERT INTO trade_decision_traces (
    trade_id,
    created_at,
    decision_trace,
    reasoning,
    article_source,
    article_id
)
SELECT
    trades.id,
    trades.created_at,
    '{}'::jsonb,
    trades.reasoning,
    trades.article_source,
    trades.article_id
FROM trades
WHERE NOT EXISTS (
    SELECT 1
    FROM trade_decision_traces
    WHERE trade_decision_traces.trade_id = trades.id
);

UPDATE trade_decision_traces
SET
    reasoning      = trades.reasoning,
    article_source = trades.article_source,
    article_id     = trades.article_id
FROM trades
WHERE trade_decision_traces.trade_id = trades.id;

DROP INDEX IF EXISTS idx_trades_article_id;

ALTER TABLE trades
    DROP COLUMN IF EXISTS reasoning,
    DROP COLUMN IF EXISTS article_source,
    DROP COLUMN IF EXISTS article_id;
