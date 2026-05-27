-- ============================================================================
-- Sentient Trader - PnL / Performance Debug Standalone Queries
-- ============================================================================
-- Supabase SQL Editor often shows only the last result set, and temp views can
-- disappear between runs. In this file, each numbered block is self-contained.
--
-- Highlight and run ONE numbered block at a time.
-- Default window: current New York trading day through now.
-- ============================================================================

-- ============================================================================
-- 1) Executed-order summary: are actual executions positive or bleeding?
-- ============================================================================
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
        t.executed_action,
        t.quantity,
        t.order_status,
        COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
        COALESCE(so.label_status, 'MISSING') AS label_status,
        so.signal_price,
        so.return_15m,
        so.return_1h,
        so.return_eod,
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
),
pnl_debug_base AS (
    SELECT
        *,
        CASE WHEN signal_price IS NOT NULL AND directional_return_15m IS NOT NULL
            THEN quantity * signal_price * directional_return_15m
        END AS approx_directional_pnl_15m,
        CASE WHEN signal_price IS NOT NULL AND directional_return_1h IS NOT NULL
            THEN quantity * signal_price * directional_return_1h
        END AS approx_directional_pnl_1h,
        CASE WHEN signal_price IS NOT NULL AND directional_return_eod IS NOT NULL
            THEN quantity * signal_price * directional_return_eod
        END AS approx_directional_pnl_eod
    FROM base
)
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


-- ============================================================================
-- 2) Worst executed trades first.
-- ============================================================================
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
        t.executed_action,
        t.quantity,
        t.order_id,
        t.order_status,
        t.execution_error,
        COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
        COALESCE(so.label_status, 'MISSING') AS label_status,
        so.signal_price,
        so.return_15m,
        so.return_1h,
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
),
pnl_debug_base AS (
    SELECT
        *,
        CASE WHEN signal_price IS NOT NULL AND directional_return_15m IS NOT NULL
            THEN quantity * signal_price * directional_return_15m
        END AS approx_directional_pnl_15m,
        CASE WHEN signal_price IS NOT NULL AND directional_return_1h IS NOT NULL
            THEN quantity * signal_price * directional_return_1h
        END AS approx_directional_pnl_1h,
        CASE WHEN signal_price IS NOT NULL AND directional_return_eod IS NOT NULL
            THEN quantity * signal_price * directional_return_eod
        END AS approx_directional_pnl_eod
    FROM base
)
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


-- ============================================================================
-- 3) Performance by ticker: who is doing the damage?
-- ============================================================================
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
),
base AS (
    SELECT
        t.ticker,
        t.created_at,
        t.executed_action,
        t.quantity,
        COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
        so.signal_price,
        so.return_15m,
        so.return_1h,
        so.return_eod,
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
),
pnl_debug_base AS (
    SELECT
        *,
        CASE WHEN signal_price IS NOT NULL AND directional_return_eod IS NOT NULL
            THEN quantity * signal_price * directional_return_eod
        END AS approx_directional_pnl_eod
    FROM base
)
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


-- ============================================================================
-- 5) Blocked BUY/SELL calls: did the gate save you or block winners?
-- ============================================================================
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
),
pnl_debug_base AS (
    SELECT
        COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
        t.executed_action,
        CASE
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_15m
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_15m
        END AS directional_return_15m,
        CASE
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_1h
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_1h
        END AS directional_return_1h,
        CASE
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'BUY' THEN so.return_eod
            WHEN COALESCE(t.pm_recommendation, t.trade_action) = 'SELL' THEN -so.return_eod
        END AS directional_return_eod
    FROM sentient_trader.trades t
    LEFT JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
    CROSS JOIN params p
    WHERE t.created_at >= p.start_at
      AND t.created_at < p.end_at
)
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


-- ============================================================================
-- 7) Label health: if MISSING/PARTIAL is high, stats are incomplete.
-- ============================================================================
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    COALESCE(so.label_status, 'MISSING') AS label_status,
    CASE
        WHEN (t.created_at AT TIME ZONE 'America/New_York')::time < TIME '09:30' THEN 'premarket'
        WHEN (t.created_at AT TIME ZONE 'America/New_York')::time <= TIME '16:00' THEN 'regular_market'
        ELSE 'after_hours'
    END AS market_session,
    COALESCE(so.label_error, 'none') AS label_error,
    COUNT(*) AS count
FROM sentient_trader.trades t
LEFT JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
CROSS JOIN params p
WHERE t.created_at >= p.start_at
  AND t.created_at < p.end_at
GROUP BY 1, 2, 3
ORDER BY count DESC;


-- ============================================================================
-- 11) Executed trade decision details: why did the AI buy these names?
-- ============================================================================
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    t.created_at AT TIME ZONE 'America/New_York' AS created_at_et,
    t.ticker,
    t.headline,
    COALESCE(t.pm_recommendation, t.trade_action) AS pm_recommendation,
    t.executed_action,
    t.quantity,
    t.sentiment_score,
    t.confidence_score AS raw_confidence,
    t.calibrated_confidence,
    t.risk_should_trade,
    t.gate_reason,
    t.order_status,
    t.execution_error,
    so.label_status,
    so.signal_price,
    ROUND((so.return_15m * 100)::numeric, 3) AS raw_return_15m_pct,
    ROUND((so.return_1h * 100)::numeric, 3) AS raw_return_1h_pct,
    tdt.reasoning,
    tdt.decision_trace->'portfolio_manager_decision'->>'reasoning' AS pm_reasoning
FROM sentient_trader.trades t
LEFT JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
LEFT JOIN sentient_trader.trade_decision_traces tdt ON tdt.trade_id = t.id
CROSS JOIN params p
WHERE t.created_at >= p.start_at
  AND t.created_at < p.end_at
  AND t.executed_action IS NOT NULL
ORDER BY t.created_at DESC;


-- ============================================================================
-- 12) Submitted orders with stale/missing outcome labels.
-- ============================================================================
WITH params AS (
    SELECT
        ((NOW() AT TIME ZONE 'America/New_York')::date AT TIME ZONE 'America/New_York') AS start_at,
        NOW() AS end_at
)
SELECT
    t.created_at AT TIME ZONE 'America/New_York' AS created_at_et,
    t.ticker,
    t.executed_action,
    t.quantity,
    t.order_status,
    t.order_id,
    COALESCE(so.label_status, 'MISSING') AS label_status,
    so.label_error,
    ROUND(EXTRACT(EPOCH FROM (NOW() - t.created_at)) / 60.0, 1) AS age_minutes,
    CASE
        WHEN t.order_status::text ILIKE '%PENDING%' THEN 'check_alpaca_order_fill'
        WHEN so.trade_id IS NULL AND t.created_at <= NOW() - INTERVAL '20 minutes' THEN 'outcome_missing_after_20m'
        WHEN so.label_status = 'NO_BARS' THEN 'market_data_missing_for_signal_window'
        ELSE 'ok_or_still_young'
    END AS diagnosis
FROM sentient_trader.trades t
LEFT JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
CROSS JOIN params p
WHERE t.created_at >= p.start_at
  AND t.created_at < p.end_at
  AND t.executed_action IS NOT NULL
ORDER BY t.created_at DESC;
