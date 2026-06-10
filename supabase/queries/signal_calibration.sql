-- Signal calibration / edge report.
--
-- Powers the dashboard "Calibration" panel and the GET /calibration endpoint.
-- For every LABELED/PARTIAL outcome, joins back to the trade and buckets by the
-- committee's conviction, then reports forward returns and hit rate. `edge_eod`
-- flips the sign for SELL signals, so a positive number always means the call
-- was directionally correct.
--
-- Run ad hoc in the Supabase SQL editor (swap the schema if you use a dev one):

WITH labeled AS (
  SELECT
    coalesce(t.pm_recommendation, t.trade_action)                AS action,
    coalesce(t.calibrated_confidence, t.confidence_score)::float8 AS conviction,
    CASE WHEN coalesce(t.pm_recommendation, t.trade_action) = 'SELL'
         THEN -1 ELSE 1 END                                       AS dir,
    o.return_15m, o.return_1h, o.return_eod
  FROM sentient_trader.signal_outcomes o
  JOIN sentient_trader.trades t ON t.id = o.trade_id
  WHERE o.label_status IN ('LABELED', 'PARTIAL')
    AND coalesce(t.pm_recommendation, t.trade_action) IN ('BUY', 'SELL')
    AND o.return_eod IS NOT NULL
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
)
SELECT
  action,
  bucket,
  count(*)                                                  AS signals,
  round((avg(return_15m) * 100)::numeric, 3)                AS avg_return_15m_pct,
  round((avg(return_1h)  * 100)::numeric, 3)                AS avg_return_1h_pct,
  round((avg(return_eod) * 100)::numeric, 3)                AS avg_return_eod_pct,
  round((avg(dir * return_eod) * 100)::numeric, 3)          AS avg_edge_eod_pct,
  round(avg(CASE WHEN dir * return_eod > 0 THEN 1.0 ELSE 0.0 END)::numeric, 3) AS win_rate_eod
FROM bucketed
GROUP BY action, bucket
ORDER BY action, bucket DESC;

-- Equivalent one-shot via the RPC the API uses:
--   SELECT sentient_trader.signal_calibration(min_signals => 1);
