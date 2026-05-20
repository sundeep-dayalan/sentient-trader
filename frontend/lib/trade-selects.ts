export const TRADE_SUMMARY_SELECT =
  "id, created_at, ticker, headline, article_url, sentiment_score, confidence_score, trade_action, order_id, quantity, is_simulated" as const;

export const LEGACY_TRADE_DETAIL_SELECT =
  `${TRADE_SUMMARY_SELECT}, reasoning, article_source, article_id, decision_trace` as const;

export const TRACE_DETAIL_SELECT =
  "decision_trace, reasoning, article_source, article_id" as const;

export const TRADE_STATS_SELECT =
  "trade_action, order_id, sentiment_score" as const;
