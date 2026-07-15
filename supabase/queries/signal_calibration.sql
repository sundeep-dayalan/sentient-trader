-- =============================================================================
-- signal_calibration(min_signals int) — robust calibration RPC
-- =============================================================================
-- Powers the dashboard "Signal Calibration" panel. Winsorizes returns to
-- +/- 15% and excludes sub-$3 tickers so penny-stock microstructure noise
-- can't produce phantom bucket means (README Bug Log: BUG-2026-07-15-01).
-- Drop-in: same single-arg signature, same JSON output shape.
-- Apply with:  psql "$DATABASE_URL" -f supabase/queries/signal_calibration.sql
CREATE OR REPLACE FUNCTION signal_calibration(min_signals int DEFAULT 1)
RETURNS jsonb
LANGUAGE sql
STABLE
SET search_path = sentient_trader, public
AS $$
  -- Robust calibration (see README Bug Log: BUG-2026-07-15-01). Raw averages
  -- were dominated by penny-stock outliers: a $1.68 stock ticking $0.37 is a
  -- +22% "return", and ~70 such labels dragged the SELL/<0.50 bucket to a
  -- phantom -26% mean while its median was +0.9%. Two defenses:
  --   1. WINSORIZE every return to +/- 0.15 before averaging, so a single
  --      tail print can't dominate a bucket. Sign is preserved, so win rates
  --      are unaffected; only magnitude is capped.
  --   2. PRICE FLOOR: exclude sub-$3 tickers whose percentage moves are
  --      microstructure noise, not signal (permissive on NULL price —
  --      winsorization is the backstop there).
  -- The signal_outcomes table still stores the RAW returns; only this display
  -- aggregate is robustified.
  WITH labeled AS (
    SELECT
      coalesce(t.pm_recommendation, t.trade_action)               AS action,
      coalesce(t.calibrated_confidence, t.confidence_score)::float8 AS conviction,
      CASE WHEN coalesce(t.pm_recommendation, t.trade_action) = 'SELL'
           THEN -1 ELSE 1 END                                      AS dir,
      greatest(-0.15, least(0.15, o.return_15m))                   AS return_15m,
      greatest(-0.15, least(0.15, o.return_1h))                    AS return_1h,
      greatest(-0.15, least(0.15, o.return_eod))                   AS return_eod
    FROM signal_outcomes o
    JOIN trades t ON t.id = o.trade_id
    WHERE o.label_status IN ('LABELED', 'PARTIAL')
      AND coalesce(t.pm_recommendation, t.trade_action) IN ('BUY', 'SELL')
      AND o.return_eod IS NOT NULL
      AND (o.signal_price IS NULL OR o.signal_price >= 3)
  ),
  bucketed AS (
    SELECT
      action, dir, return_15m, return_1h, return_eod,
      CASE
        WHEN conviction >= 0.90 THEN '0.90+'
        WHEN conviction >= 0.80 THEN '0.80-0.90'
        WHEN conviction >= 0.70 THEN '0.70-0.80'
        WHEN conviction >= 0.50 THEN '0.50-0.70'
        ELSE '<0.50'
      END AS bucket
    FROM labeled
  ),
  per_bucket AS (
    SELECT
      action, bucket,
      count(*)                                                   AS signals,
      avg(return_15m)                                            AS avg_return_15m,
      avg(return_1h)                                             AS avg_return_1h,
      avg(return_eod)                                            AS avg_return_eod,
      avg(dir * return_eod)                                      AS avg_edge_eod,
      avg(CASE WHEN dir * return_eod > 0 THEN 1.0 ELSE 0.0 END)  AS win_rate_eod
    FROM bucketed
    GROUP BY action, bucket
    HAVING count(*) >= min_signals
  ),
  per_action AS (
    SELECT
      action,
      count(*)                                                   AS signals,
      avg(return_eod)                                            AS avg_return_eod,
      avg(dir * return_eod)                                      AS avg_edge_eod,
      avg(CASE WHEN dir * return_eod > 0 THEN 1.0 ELSE 0.0 END)  AS win_rate_eod
    FROM bucketed
    GROUP BY action
  )
  SELECT jsonb_build_object(
    'labeledSignals', (SELECT count(*) FROM bucketed),
    'buckets', coalesce(

