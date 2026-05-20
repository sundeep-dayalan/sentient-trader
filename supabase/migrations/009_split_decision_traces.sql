-- =============================================================================
-- Migration 009: Split Large Decision Traces Out of Realtime Trade Rows
-- =============================================================================
-- `trades` is Realtime-enabled, so INSERT payloads are broadcast to dashboard
-- clients. Keeping the full Decision Core JSONB on that table makes each live
-- event expensive. Move traces to a one-to-one side table and keep `trades`
-- optimized for the live feed.
-- =============================================================================

CREATE TABLE IF NOT EXISTS trade_decision_traces (
    trade_id       UUID        PRIMARY KEY REFERENCES trades(id) ON DELETE CASCADE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decision_trace JSONB       NOT NULL
);

INSERT INTO trade_decision_traces (trade_id, created_at, decision_trace)
SELECT id, created_at, decision_trace
FROM trades
WHERE decision_trace IS NOT NULL
ON CONFLICT (trade_id) DO UPDATE
SET decision_trace = EXCLUDED.decision_trace;

ALTER TABLE trade_decision_traces ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Public read access on trade_decision_traces"
    ON trade_decision_traces
    FOR SELECT
    TO anon
    USING (true);

DROP INDEX IF EXISTS idx_trades_decision_trace;

ALTER TABLE trades
    DROP COLUMN IF EXISTS decision_trace;
