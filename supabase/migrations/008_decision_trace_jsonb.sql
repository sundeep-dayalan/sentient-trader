-- =============================================================================
-- Migration 008: Generic Decision Core Trace Storage
-- =============================================================================
-- Replaces narrow committee_debate/model storage with one extensible JSONB
-- document for every raw Decision Core step:
--
--   decision_trace JSONB
--     {
--       schema_version,
--       news,
--       market_context,
--       llm_operations,              -- exact LLM messages + structured outputs
--       committee_debate,            -- rendered by the dashboard
--       portfolio_manager_decision,  -- synthesis model + final decision
--       risk_gate,
--       execution
--     }
--
-- Existing rows are backfilled from committee_debate, model, and the top-level
-- summary columns before the old narrow columns are dropped.
-- =============================================================================

ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS decision_trace JSONB;

UPDATE trades
SET decision_trace = jsonb_build_object(
    'schema_version', 1,
    'pipeline', 'decision_core',
    'legacy_migration', true,
    'news', jsonb_strip_nulls(jsonb_build_object(
        'ticker', ticker,
        'headline', headline,
        'source', article_source,
        'article_url', article_url,
        'article_id', article_id,
        'is_simulated', is_simulated
    )),
    'market_context', NULL,
    'llm_operations', '[]'::jsonb,
    'committee_debate', COALESCE(committee_debate, '[]'::jsonb),
    'portfolio_manager_decision', jsonb_strip_nulls(jsonb_build_object(
        'model', model,
        'sentiment', sentiment_score,
        'confidence', confidence_score,
        'reasoning', reasoning,
        'action', trade_action
    )),
    'risk_gate', jsonb_build_object(
        'should_trade', order_id IS NOT NULL,
        'trade_action', trade_action
    ),
    'execution', jsonb_strip_nulls(jsonb_build_object(
        'submitted', order_id IS NOT NULL,
        'order_id', order_id,
        'quantity', quantity,
        'is_simulated', is_simulated
    ))
)
WHERE decision_trace IS NULL
  AND (committee_debate IS NOT NULL OR model IS NOT NULL);

ALTER TABLE trades
    DROP COLUMN IF EXISTS committee_debate,
    DROP COLUMN IF EXISTS model;

CREATE INDEX IF NOT EXISTS idx_trades_decision_trace
    ON trades USING GIN (decision_trace);
