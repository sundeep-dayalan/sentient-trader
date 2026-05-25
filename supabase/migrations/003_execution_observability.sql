-- =============================================================================
-- Execution observability and calibration fields
-- =============================================================================
-- Keep trade_action as the legacy Portfolio Manager recommendation, and add
-- explicit columns for execution state, calibrated confidence, pre-screen/full
-- debate path, and exact processing timestamps.
-- =============================================================================

ALTER TABLE sentient_trader.trades
    ADD COLUMN IF NOT EXISTS pm_recommendation TEXT
        CHECK (pm_recommendation IN ('BUY', 'SELL', 'HOLD')),
    ADD COLUMN IF NOT EXISTS calibrated_confidence FLOAT4,
    ADD COLUMN IF NOT EXISTS confidence_cap FLOAT4,
    ADD COLUMN IF NOT EXISTS risk_should_trade BOOLEAN,
    ADD COLUMN IF NOT EXISTS executed_action TEXT
        CHECK (executed_action IN ('BUY', 'SELL')),
    ADD COLUMN IF NOT EXISTS client_order_id TEXT,
    ADD COLUMN IF NOT EXISTS order_status TEXT,
    ADD COLUMN IF NOT EXISTS execution_error TEXT,
    ADD COLUMN IF NOT EXISTS gate_reason TEXT,
    ADD COLUMN IF NOT EXISTS decision_path TEXT,
    ADD COLUMN IF NOT EXISTS processing_started_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS processing_finished_at TIMESTAMPTZ;

UPDATE sentient_trader.trades
SET pm_recommendation = trade_action
WHERE pm_recommendation IS NULL;

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_decision_path
    ON sentient_trader.trades (decision_path);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_executed_action
    ON sentient_trader.trades (executed_action)
    WHERE executed_action IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_sentient_trader_trades_client_order_id
    ON sentient_trader.trades (client_order_id)
    WHERE client_order_id IS NOT NULL;
