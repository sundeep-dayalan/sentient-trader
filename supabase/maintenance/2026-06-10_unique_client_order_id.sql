-- =============================================================================
-- Maintenance: enforce uniqueness on trades.client_order_id
-- =============================================================================
-- Why: client_order_id is the idempotency key for one signal+action. The Redis
-- stream delivers at-least-once, so a crash between the Supabase insert and
-- XACK could re-run the pipeline and insert a second trades row for the same
-- signal (the Alpaca order itself was already deduped by the broker). The old
-- index was non-unique, so nothing stopped the duplicate audit row — inflating
-- /stats and showing double entries in the dashboard feed.
--
-- This script:
--   1. Deletes duplicate rows, keeping the OLDEST row per client_order_id
--      (the original insert; its decision trace cascades for deleted dupes).
--   2. Replaces the non-unique partial index with a full unique index.
--      NULL client_order_id rows (HOLD / blocked signals) are unaffected —
--      Postgres unique indexes treat NULLs as distinct.
--
-- Run against BOTH schemas when ready (dev first):
--   SET search_path TO sentient_trader_dev, public;  -- then re-run for prod
-- =============================================================================

SET search_path TO sentient_trader, public;

BEGIN;

WITH ranked AS (
    SELECT id,
           row_number() OVER (
               PARTITION BY client_order_id
               ORDER BY created_at ASC, id ASC
           ) AS rn
    FROM trades
    WHERE client_order_id IS NOT NULL
)
DELETE FROM trades
WHERE id IN (SELECT id FROM ranked WHERE rn > 1);

DROP INDEX IF EXISTS idx_trades_client_order_id;
CREATE UNIQUE INDEX idx_trades_client_order_id
    ON trades (client_order_id);

COMMIT;
