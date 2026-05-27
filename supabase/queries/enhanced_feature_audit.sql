-- ============================================================================
-- Sentient Trader — Enhanced Feature Audit Queries
-- ============================================================================
-- Run these in the Supabase SQL Editor to verify enhanced features are
-- working as expected. Each query answers a specific production question.
-- ============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. FEATURE HEALTH CHECK: Which features are active and firing?
-- ─────────────────────────────────────────────────────────────────────────────
-- Shows total activations per feature over the last 7 days.
-- If a feature shows 0 activations but is enabled in config, investigate.

SELECT
    activation->>'feature' AS feature,
    activation->>'outcome' AS outcome,
    COUNT(*) AS count
FROM sentient_trader.trade_decision_traces tdt,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE tdt.created_at >= NOW() - INTERVAL '7 days'
  AND tdt.decision_trace->'enhanced_features' IS NOT NULL
GROUP BY 1, 2
ORDER BY 1, 3 DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 2. CIRCUIT BREAKER TRIPS: Did the circuit breaker ever block trading?
-- ─────────────────────────────────────────────────────────────────────────────
-- If the circuit breaker tripped, this shows when and at what P&L %.

SELECT
    t.ticker,
    t.created_at,
    activation->>'outcome' AS outcome,
    activation->'details'->>'daily_pnl_pct' AS daily_pnl_pct,
    activation->>'impact' AS impact
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'circuit_breaker'
  AND activation->>'outcome' = 'tripped'
ORDER BY t.created_at DESC
LIMIT 20;


-- ─────────────────────────────────────────────────────────────────────────────
-- 3. BRACKET ORDER PERFORMANCE: Are stop-losses and take-profits placing?
-- ─────────────────────────────────────────────────────────────────────────────
-- Shows every bracket order placed, whether TP/SL order IDs were captured.

SELECT
    t.ticker,
    t.created_at,
    t.executed_action,
    activation->'details'->>'take_profit_order_id' AS tp_order,
    activation->'details'->>'stop_loss_order_id' AS sl_order,
    activation->>'outcome' AS outcome,
    activation->>'error' AS error
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'bracket_orders'
  AND activation->>'activated' = 'true'
ORDER BY t.created_at DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────────────────────
-- 4. DYNAMIC SIZING IMPACT: How is conviction-scaled sizing performing?
-- ─────────────────────────────────────────────────────────────────────────────
-- Shows the scale factor and resulting quantity for each trade.
-- Helps verify that high-confidence trades get larger positions.

SELECT
    t.ticker,
    t.created_at,
    t.executed_action,
    t.sentiment_score,
    t.confidence_score,
    activation->'details'->>'scale_factor' AS scale_factor,
    activation->'details'->>'quantity' AS sized_qty,
    activation->>'impact' AS impact
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'dynamic_sizing'
  AND activation->>'activated' = 'true'
ORDER BY t.created_at DESC
LIMIT 30;


-- ─────────────────────────────────────────────────────────────────────────────
-- 5. TECHNICAL INDICATORS COVERAGE: How often are indicators available?
-- ─────────────────────────────────────────────────────────────────────────────
-- Shows the ratio of signals where technical data was available vs missing.

SELECT
    activation->>'outcome' AS outcome,
    COUNT(*) AS count,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
FROM sentient_trader.trade_decision_traces tdt,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'technical_indicators'
  AND tdt.created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1
ORDER BY 2 DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 6. SOURCE CREDIBILITY ADJUSTMENTS: Which sources are getting penalized?
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    tdt.decision_trace->'news'->>'source' AS news_source,
    activation->>'outcome' AS outcome,
    activation->'details'->>'score_after' AS quality_score,
    COUNT(*) AS count
FROM sentient_trader.trade_decision_traces tdt,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'source_credibility'
  AND activation->>'activated' = 'true'
  AND tdt.created_at >= NOW() - INTERVAL '7 days'
GROUP BY 1, 2, 3
ORDER BY 4 DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 7. CONCENTRATION LIMIT BLOCKS: Which tickers hit position limits?
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    t.ticker,
    t.created_at,
    activation->'details'->'blockers' AS blockers,
    activation->>'impact' AS impact
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'concentration_limits'
  AND activation->>'outcome' = 'blocked'
ORDER BY t.created_at DESC
LIMIT 20;


-- ─────────────────────────────────────────────────────────────────────────────
-- 8. FILL VERIFICATION: Are orders actually filling? At what prices?
-- ─────────────────────────────────────────────────────────────────────────────

SELECT
    t.ticker,
    t.created_at,
    t.executed_action,
    activation->'details'->>'status' AS fill_status,
    activation->'details'->>'filled_qty' AS filled_qty,
    activation->'details'->>'filled_avg_price' AS avg_fill_price,
    activation->>'impact' AS impact
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id,
     jsonb_array_elements(
         tdt.decision_trace->'enhanced_features'->'activations'
     ) AS activation
WHERE activation->>'feature' = 'fill_verification'
  AND tdt.created_at >= NOW() - INTERVAL '7 days'
ORDER BY t.created_at DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 9. SEMANTIC DEDUP SAVINGS: How many duplicate headlines were caught?
-- ─────────────────────────────────────────────────────────────────────────────
-- Note: Semantic dedup hits are logged but don't create trade rows since
-- they're caught in the cache before analysis. Check agent logs for:
--   grep "Semantic dedup hit" agent.log | wc -l
-- Or use the Redis counter approach:
--   redis-cli KEYS "enhanced_metrics:*:semantic_dedup:*"


-- ─────────────────────────────────────────────────────────────────────────────
-- 10. OVERALL FEATURE SUMMARY: Daily feature dashboard
-- ─────────────────────────────────────────────────────────────────────────────
-- Shows a daily summary of all features — run this to get a quick health check.

SELECT
    DATE(t.created_at) AS day,
    tdt.decision_trace->'enhanced_features'->>'total_features_enabled' AS features_enabled,
    tdt.decision_trace->'enhanced_features'->>'total_features_activated' AS features_activated,
    tdt.decision_trace->'enhanced_features'->'summary'->'active' AS active_list,
    tdt.decision_trace->'enhanced_features'->'summary'->'errors' AS error_list,
    COUNT(*) AS signals
FROM sentient_trader.trade_decision_traces tdt
JOIN sentient_trader.trades t ON t.id = tdt.trade_id
WHERE tdt.decision_trace->'enhanced_features' IS NOT NULL
  AND t.created_at >= NOW() - INTERVAL '30 days'
GROUP BY 1, 2, 3, 4, 5
ORDER BY 1 DESC;


-- ─────────────────────────────────────────────────────────────────────────────
-- 11. FEATURE PERFORMANCE vs OUTCOMES: Are enhanced features improving P&L?
-- ─────────────────────────────────────────────────────────────────────────────
-- Compare return_eod for signals with vs without enhanced features.
-- Requires signal_outcomes to be populated.

SELECT
    CASE
        WHEN tdt.decision_trace->'enhanced_features' IS NOT NULL
        THEN 'enhanced'
        ELSE 'baseline'
    END AS mode,
    COUNT(*) AS signals,
    ROUND(AVG(so.return_eod::numeric) * 100, 2) AS avg_return_eod_pct,
    ROUND(AVG(so.return_1h::numeric) * 100, 2) AS avg_return_1h_pct,
    SUM(CASE WHEN so.return_eod > 0 THEN 1 ELSE 0 END) AS winners,
    SUM(CASE WHEN so.return_eod <= 0 THEN 1 ELSE 0 END) AS losers,
    ROUND(
        100.0 * SUM(CASE WHEN so.return_eod > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0),
        1
    ) AS win_rate_pct
FROM sentient_trader.trades t
JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
LEFT JOIN sentient_trader.trade_decision_traces tdt ON tdt.trade_id = t.id
WHERE t.executed_action IN ('BUY', 'SELL')
  AND so.label_status = 'COMPLETED'
GROUP BY 1
ORDER BY 1;




-- EXTRA
SELECT
    pm_recommendation,
    COUNT(*) AS total,
    ROUND(AVG(CASE WHEN so.return_1h > 0 AND pm_recommendation = 'BUY' THEN 1
                    WHEN so.return_1h < 0 AND pm_recommendation = 'SELL' THEN 1
                    ELSE 0 END) * 100, 1) AS directional_accuracy_pct
FROM sentient_trader.trades t
JOIN sentient_trader.signal_outcomes so ON so.trade_id = t.id
WHERE t.executed_action IS NOT NULL
  AND so.label_status IN ('LABELED', 'COMPLETED')
GROUP BY pm_recommendation;