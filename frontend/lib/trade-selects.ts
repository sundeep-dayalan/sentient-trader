export const TRADE_SUMMARY_SELECT =
  'id, created_at, ticker, headline, article_url, sentiment_score, confidence_score, calibrated_confidence, confidence_cap, trade_action, pm_recommendation, risk_should_trade, executed_action, order_id, client_order_id, order_status, execution_error, gate_reason, decision_path, processing_started_at, processing_finished_at, quantity, is_simulated' as const;

export const LEGACY_TRADE_DETAIL_SELECT =
  `${TRADE_SUMMARY_SELECT}, reasoning, article_source, article_id, decision_trace` as const;

export const TRACE_DETAIL_SELECT = 'decision_trace, reasoning, article_source, article_id' as const;

export const TRADE_STATS_SELECT =
  'trade_action, pm_recommendation, executed_action, order_id, risk_should_trade, decision_path, sentiment_score' as const;
