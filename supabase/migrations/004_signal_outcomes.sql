-- =============================================================================
-- Post-signal outcome labels
-- =============================================================================
-- Stores realized returns after each signal so replay/audit analysis can
-- calibrate the risk gate against actual post-signal movement.
-- =============================================================================

CREATE TABLE IF NOT EXISTS sentient_trader.signal_outcomes (
    trade_id          UUID PRIMARY KEY REFERENCES sentient_trader.trades(id) ON DELETE CASCADE,
    ticker            TEXT        NOT NULL,
    signal_at         TIMESTAMPTZ NOT NULL,
    signal_price      FLOAT8,
    price_15m         FLOAT8,
    return_15m        FLOAT8,
    price_1h          FLOAT8,
    return_1h         FLOAT8,
    price_eod         FLOAT8,
    return_eod        FLOAT8,
    label_status      TEXT        NOT NULL DEFAULT 'LABELED'
                      CHECK (label_status IN ('LABELED', 'PARTIAL', 'NO_BARS', 'ERROR')),
    label_error       TEXT,
    label_attempts    INT4        NOT NULL DEFAULT 0,
    last_attempt_at   TIMESTAMPTZ,
    labeled_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_signal_outcomes_ticker
    ON sentient_trader.signal_outcomes (ticker);

CREATE INDEX IF NOT EXISTS idx_sentient_trader_signal_outcomes_signal_at
    ON sentient_trader.signal_outcomes (signal_at DESC);

ALTER TABLE sentient_trader.signal_outcomes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Public read access on signal_outcomes" ON sentient_trader.signal_outcomes;
CREATE POLICY "Public read access on signal_outcomes"
    ON sentient_trader.signal_outcomes
    FOR SELECT
    USING (true);

GRANT SELECT ON sentient_trader.signal_outcomes TO anon, authenticated;
GRANT ALL ON sentient_trader.signal_outcomes TO service_role;
