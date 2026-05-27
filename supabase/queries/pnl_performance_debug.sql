-- ============================================================================
-- Sentient Trader - PnL / Performance Debug Queries
-- ============================================================================
-- The Portfolio PnL chart is not backed by Supabase rows. The backend reads
-- Alpaca directly:
--   /v2/account/portfolio/history?period=...&timeframe=...&intraday_reporting=extended_hours
--
-- Use these Supabase queries to explain what likely drove the equity curve:
-- executed orders, blocked directional calls, post-signal returns, risk gates,
-- outcome-label health, and feature activations.
--
-- Default window: current New York trading day through now.
-- To inspect another window, edit the `params` CTE in the temp view below.
-- ============================================================================

DROP VIEW IF EXISTS pg_temp.pnl_debug_base;

CREATE TEMP VIEW pnl_debug_base AS
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
),
base AS (
    SELECT
        t.id,
        t.ticker,
        t.created_at,
        t.created_at AT TIME ZONE 'America/New_York' AS created_at_et,
        CASE
            WHEN (t.created_at AT TIME ZONE 'America/New_York')::time < TIME '09:30' THEN 'premarket'
            WHEN (t.created_at AT TIME ZONE 'America/New_York')::time <= TIME '16:00' THEN 'regular_market'
            ELSE 'after_hours'
        END AS market_session,
        COALESCE(t.decision_path, 'unknown') AS decision_path,
        COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
        t.trade_action,
        t.executed_action,
        t.quantity,
        t.order_id,
        t.order_status,
        t.execution_error,
        t.gate_reason,
        t.sentiment_score,
        t.confidence_score AS raw_confidence,
        t.calibrated_confidence,
        t.confidence_cap,
        t.risk_should_trade,
        t.is_simulated,
        COALESCE(so.label_status, 'MISSING') AS label_status,
        so.signal_price,
        so.price_15m,
        so.return_15m,
        so.price_1h,
        so.return_1h,
        so.price_eod,
        so.return_eod,
        so.label_error,
        CASE
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_15m
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_15m
        END AS directional_return_15m,
        CASE
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_1h
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_1h
        END AS directional_return_1h,
        CASE
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_eod
            WHEN COALESCE(t.executed_action, t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_eod
        END AS directional_return_eod
    FROM sentient_trader.trades t
    LEFT JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
    CROSS JOIN params p
    WHERE t.created_at >= p.start_at
      AND t.created_at < p.end_at
)
SELECT
    *,
    CASE
        WHEN signal_price IS NOT NULL AND directional_return_15m IS NOT NULL
            THEN quantity * signal_price * directional_return_15m
    END AS approx_directional_pnl_15m,
    CASE
        WHEN signal_price IS NOT NULL AND directional_return_1h IS NOT NULL
            THEN quantity * signal_price * directional_return_1h
    END AS approx_directional_pnl_1h,
    CASE
        WHEN signal_price IS NOT NULL AND directional_return_eod IS NOT NULL
            THEN quantity * signal_price * directional_return_eod
    END AS approx_directional_pnl_eod
FROM base;

-- 1) Today's headline: are executed orders positive or bleeding?
SELECT
    COUNT(*) FILTER (WHERE executed_action IS NOT NULL) AS executed_orders,
    COUNT(*) FILTER (WHERE executed_action = 'BUY') AS buys,
    COUNT(*) FILTER (WHERE executed_action = 'SELL') AS sells,
    ROUND(AVG(directional_return_15m)::numeric * 100, 3) AS avg_dir_return_15m_pct,
    ROUND(AVG(directional_return_1h)::numeric * 100, 3) AS avg_dir_return_1h_pct,
    ROUND(AVG(directional_return_eod)::numeric * 100, 3) AS avg_dir_return_eod_pct,
    ROUND(SUM(approx_directional_pnl_15m)::numeric, 2) AS approx_pnl_15m,
    ROUND(SUM(approx_directional_pnl_1h)::numeric, 2) AS approx_pnl_1h,
    ROUND(SUM(approx_directional_pnl_eod)::numeric, 2) AS approx_pnl_eod,
    ROUND(100.0 * AVG(
        CASE
            WHEN directional_return_eod > 0 THEN 1.0
            WHEN directional_return_eod IS NOT NULL THEN 0.0
        END
    ), 1) AS eod_win_rate_pct
FROM pnl_debug_base
WHERE executed_action IS NOT NULL;

-- 2) The exact executed trades ranked worst first.
SELECT
    created_at_et,
    ticker,
    executed_action,
    quantity,
    order_status,
    signal_price,
    ROUND((directional_return_15m * 100)::numeric, 3) AS dir_return_15m_pct,
    ROUND((directional_return_1h * 100)::numeric, 3) AS dir_return_1h_pct,
    ROUND((directional_return_eod * 100)::numeric, 3) AS dir_return_eod_pct,
    ROUND(approx_directional_pnl_15m::numeric, 2) AS approx_pnl_15m,
    ROUND(approx_directional_pnl_1h::numeric, 2) AS approx_pnl_1h,
    ROUND(approx_directional_pnl_eod::numeric, 2) AS approx_pnl_eod,
    label_status,
    label_error,
    execution_error,
    order_id
FROM pnl_debug_base
WHERE executed_action IS NOT NULL
ORDER BY approx_directional_pnl_eod ASC NULLS LAST, created_at DESC;

-- 3) Performance by ticker: who is doing the damage?
SELECT
    ticker,
    COUNT(*) FILTER (WHERE executed_action IS NOT NULL) AS executed_orders,
    ROUND(AVG(directional_return_15m)::numeric * 100, 3) AS avg_dir_return_15m_pct,
    ROUND(AVG(directional_return_1h)::numeric * 100, 3) AS avg_dir_return_1h_pct,
    ROUND(AVG(directional_return_eod)::numeric * 100, 3) AS avg_dir_return_eod_pct,
    ROUND(SUM(approx_directional_pnl_eod)::numeric, 2) AS approx_pnl_eod,
    ROUND(100.0 * AVG(
        CASE
            WHEN directional_return_eod > 0 THEN 1.0
            WHEN directional_return_eod IS NOT NULL THEN 0.0
        END
    ), 1) AS eod_win_rate_pct
FROM pnl_debug_base
WHERE executed_action IS NOT NULL
GROUP BY ticker
ORDER BY approx_pnl_eod ASC NULLS LAST;

-- 4) Performance by decision path and recommendation.
SELECT
    decision_path,
    pm_recommendation,
    executed_action,
    COUNT(*) AS signals,
    ROUND(AVG(calibrated_confidence)::numeric, 3) AS avg_calibrated_confidence,
    ROUND(AVG(directional_return_1h)::numeric * 100, 3) AS avg_dir_return_1h_pct,
    ROUND(AVG(directional_return_eod)::numeric * 100, 3) AS avg_dir_return_eod_pct,
    ROUND(SUM(approx_directional_pnl_eod)::numeric, 2) AS approx_pnl_eod
FROM pnl_debug_base
GROUP BY decision_path, pm_recommendation, executed_action
ORDER BY approx_pnl_eod ASC NULLS LAST, signals DESC;

-- 5) Blocked BUY/SELL calls: was the gate saving you or blocking winners?
SELECT
    pm_recommendation,
    COUNT(*) AS blocked_directional_calls,
    ROUND(AVG(directional_return_15m)::numeric * 100, 3) AS avg_dir_return_15m_pct,
    ROUND(AVG(directional_return_1h)::numeric * 100, 3) AS avg_dir_return_1h_pct,
    ROUND(AVG(directional_return_eod)::numeric * 100, 3) AS avg_dir_return_eod_pct,
    ROUND(100.0 * AVG(
        CASE
            WHEN directional_return_eod > 0 THEN 1.0
            WHEN directional_return_eod IS NOT NULL THEN 0.0
        END
    ), 1) AS eod_would_have_won_pct
FROM pnl_debug_base
WHERE pm_recommendation IN ('BUY', 'SELL')
  AND executed_action IS NULL
GROUP BY pm_recommendation
ORDER BY pm_recommendation;

-- 6) Why directional calls did not execute.
SELECT
    CASE
        WHEN gate_reason ILIKE '%No long position%' THEN 'no_long_position_for_sell'
        WHEN gate_reason ILIKE '%Calibrated confidence%' THEN 'calibrated_confidence_too_low'
        WHEN gate_reason ILIKE '%Directional sentiment%' THEN 'directional_sentiment_too_weak'
        WHEN gate_reason ILIKE '%Article quality%' THEN 'article_quality_too_weak'
        WHEN gate_reason ILIKE '%circuit breaker%' THEN 'daily_loss_circuit_breaker'
        WHEN gate_reason ILIKE '%concentration%' THEN 'concentration_limit'
        WHEN gate_reason ILIKE '%Simulated%' THEN 'simulated_signal'
        WHEN gate_reason IS NULL THEN 'no_gate_reason'
        ELSE 'other'
    END AS block_reason,
    COUNT(*) AS count
FROM pnl_debug_base
WHERE pm_recommendation IN ('BUY', 'SELL')
  AND executed_action IS NULL
GROUP BY 1
ORDER BY count DESC;

-- 7) Label health: if this is weak, performance numbers are stale/incomplete.
SELECT
    label_status,
    market_session,
    COALESCE(label_error, 'none') AS label_error,
    COUNT(*) AS count
FROM pnl_debug_base
GROUP BY label_status, market_session, COALESCE(label_error, 'none')
ORDER BY count DESC;

-- 8) Circuit breaker / risk feature activations during the same window.
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    t.created_at AT TIME ZONE 'America/New_York' AS created_at_et,
    t.ticker,
    activation.value->>'feature' AS feature,
    activation.value->>'outcome' AS outcome,
    activation.value->'details' AS details,
    activation.value->>'impact' AS impact,
    activation.value->>'error' AS error
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id
CROSS JOIN params p
CROSS JOIN LATERAL jsonb_array_elements(
    COALESCE(tdt.decision_trace->'enhanced_features'->'activations', '[]'::jsonb)
) AS activation(value)
WHERE t.created_at >= p.start_at
  AND t.created_at < p.end_at
  AND activation.value->>'feature' IN (
      'circuit_breaker',
      'concentration_limits',
      'dynamic_sizing',
      'fill_verification',
      'bracket_orders',
      'trailing_stops'
  )
ORDER BY t.created_at DESC;

-- 9) Fill verification and bracket-order details.
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    t.created_at AT TIME ZONE 'America/New_York' AS created_at_et,
    t.ticker,
    t.executed_action,
    fill_activation->'details'->>'status' AS fill_status,
    fill_activation->'details'->>'filled_qty' AS filled_qty,
    fill_activation->'details'->>'filled_avg_price' AS filled_avg_price,
    bracket_activation->'details'->>'take_profit_order_id' AS take_profit_order_id,
    bracket_activation->'details'->>'stop_loss_order_id' AS stop_loss_order_id,
    t.order_status,
    t.execution_error,
    t.order_id
FROM sentient_trader.trades t
LEFT JOIN sentient_trader.trade_decision_traces tdt ON tdt.trade_id = t.id
CROSS JOIN params p
LEFT JOIN LATERAL (
    SELECT activation.value
    FROM jsonb_array_elements(
        COALESCE(tdt.decision_trace->'enhanced_features'->'activations', '[]'::jsonb)
    ) AS activation(value)
    WHERE activation.value->>'feature' = 'fill_verification'
    LIMIT 1
) fill(fill_activation) ON TRUE
LEFT JOIN LATERAL (
    SELECT activation.value
    FROM jsonb_array_elements(
        COALESCE(tdt.decision_trace->'enhanced_features'->'activations', '[]'::jsonb)
    ) AS activation(value)
    WHERE activation.value->>'feature' = 'bracket_orders'
    LIMIT 1
) bracket(bracket_activation) ON TRUE
WHERE t.created_at >= p.start_at
  AND t.created_at < p.end_at
  AND t.executed_action IS NOT NULL
ORDER BY t.created_at DESC;

-- 10) Outcome scheduler health: confirms whether signal_outcomes is updating.
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    scheduler_name,
    status,
    COUNT(*) AS runs,
    MAX(started_at) AS last_started_at,
    MAX(finished_at) AS last_finished_at,
    ROUND(AVG(duration_ms)::numeric, 2) AS avg_duration_ms,
    SUM(rows_processed) AS rows_processed,
    MAX(error_message) FILTER (WHERE status = 'ERROR') AS latest_error
FROM sentient_trader.scheduler_runs, params p
WHERE started_at >= p.start_at
  AND started_at < p.end_at
GROUP BY scheduler_name, status
ORDER BY scheduler_name, status;
