/**
 * Shared TypeScript types used across the frontend.
 * These mirror the Supabase `trades` table columns, with optional legacy fields
 * kept so older development rows still render during the decision_trace rollout.
 */

export interface PersonaOpinion {
  name: string; // "Momentum Trader" | "Value Investor" | "Risk Manager"
  stance: 'BULLISH' | 'BEARISH' | 'NEUTRAL';
  conviction: number; // 0.0–1.0, shown as a conviction bar in the UI
  view: string; // one-sentence headline take (always visible on card)
  reasoning: string; // full 2-3 sentence reasoning (shown below the take)
  model?: string | null; // LLM model that powered this persona
  catalyst_strength?: string | null;
  evidence_quality?: string | null;
  time_horizon?: string | null;
  key_evidence?: string[];
  missing_data?: string[];
  risk_level?: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | null;
  risk_confidence_cap?: number | null;
  disqualifying_conditions?: string[];
}

export interface ArticleQuality {
  score?: number;
  grade?: 'HIGH' | 'MEDIUM' | 'LOW' | string;
  category?: string;
  reasons?: string[];
  flags?: string[];
  has_summary?: boolean;
}

export interface RiskGateTrace {
  step?: string;
  inputs?: {
    action?: 'BUY' | 'SELL' | 'HOLD';
    sentiment?: number;
    confidence?: number;
    calibrated_confidence?: number;
    is_simulated?: boolean;
  } | null;
  thresholds?: Record<string, number>;
  checks?: Record<string, boolean>;
  article_quality?: ArticleQuality;
  committee_metrics?: {
    agreement?: number;
    calibrated_confidence?: number;
    confidence_cap?: number;
    thesis_quality?: string;
    cap_reasons?: string[];
    high_conviction_dissenters?: string[];
    risk_level?: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL' | string | null;
    risk_disqualifying_conditions?: string[];
  };
  execution_plan?: {
    action?: string;
    side?: string | null;
    quantity?: number;
    position_intent?: string;
    estimated_notional?: number | null;
    buying_power?: number | null;
    position_qty?: number;
    blocked_reasons?: string[];
  };
  should_trade?: boolean;
  blockers?: string[];
  reason?: string;
}

export interface LLMOperationTrace {
  step: string;
  kind: 'persona_analysis' | 'portfolio_manager_synthesis' | string;
  response_schema?: string;
  messages?: Array<{ role: string; content: string }>;
  input?: unknown;
  output?: unknown;
  model?: string | null;
  error?: string | null;
  recorded_at?: string;
}

export interface DecisionTrace {
  schema_version?: number;
  pipeline?: string;
  recorded_at?: string;
  legacy_migration?: boolean;
  news?: unknown;
  market_context?: unknown;
  article_quality?: ArticleQuality;
  llm_operations?: LLMOperationTrace[];
  committee_debate?: PersonaOpinion[];
  portfolio_manager_decision?: {
    model?: string | null;
    sentiment?: number;
    confidence?: number;
    reasoning?: string;
    action?: 'BUY' | 'SELL' | 'HOLD';
    thesis_quality?: string | null;
    primary_risk?: string | null;
  } | null;
  risk_gate?: RiskGateTrace | unknown;
  execution?: unknown;
  enhanced_features?: unknown;
  price_confirmation?: unknown;
  error?: string | null;
}

export interface Trade {
  id: string;
  created_at: string;
  ticker: string;
  headline: string;
  article_url: string | null;
  article_source?: string | null;
  article_id?: string | null;
  sentiment_score: number; // -1.0 to 1.0
  confidence_score: number; // 0.0 to 1.0
  calibrated_confidence?: number | null; // execution confidence after caps
  confidence_cap?: number | null;
  reasoning?: string; // loaded with full trace, not feed rows
  trade_action: 'BUY' | 'SELL' | 'HOLD';
  pm_recommendation?: 'BUY' | 'SELL' | 'HOLD' | null;
  risk_should_trade?: boolean | null;
  executed_action?: 'BUY' | 'SELL' | null;
  order_id: string | null; // Alpaca order UUID — null if HOLD
  client_order_id?: string | null;
  order_status?: string | null;
  execution_error?: string | null;
  gate_reason?: string | null;
  decision_path?: string | null;
  processing_started_at?: string | null;
  processing_finished_at?: string | null;
  quantity: number;
  is_simulated: boolean; // true when injected via the Simulate button
  decision_trace?: DecisionTrace | PersonaOpinion[] | null; // generic Decision Core JSONB trace
  committee_debate?: PersonaOpinion[] | null; // legacy pre-008 rows
  model?: string | null; // legacy pre-008 synthesis model
}

export interface DashboardStats {
  analyzed: number;
  executed: number;
  buyOrders: number;
  sellOrders: number;
  riskGated: number;
  preScreened?: number;
  fullDebates?: number;
  avgSentiment: number;
}

// Server-side signal counts for the feed filter chips. All-time when no range is
// applied, otherwise scoped to the selected date range. Independent of the
// cursor-loaded rows in the UI. Served by GET /trades/summary.
export interface SignalCounts {
  all: number;
  buy: number;
  sell: number;
  hold: number;
  sim: number;
}

export interface PortfolioPoint {
  t: number; // epoch milliseconds — used for the time-scale X-axis
  timestamp: string; // ISO string — used for tooltips
  equity: number; // dollar value
}

export interface PortfolioSummary {
  equity: number;
  profitLoss: number;
  profitLossPct: number;
  baseValue: number | null;
  baseValueAsOf: string | null;
}

export interface PortfolioAccountSummary {
  id: string | null;
  accountNumber: string | null;
  status: string | null;
  currency: string | null;
  createdAt: string | null;
  paper: boolean;
}

/** One scenario injected by the "Simulate Market Shock" button */
export interface SimulationScenario {
  ticker: string;
  headline: string;
  source: string;
}

/** A conviction bucket from the /calibration endpoint */
export interface CalibrationBucket {
  action: 'BUY' | 'SELL';
  bucket: string;
  signals: number;
  avg_return_15m: number | null;
  avg_return_1h: number | null;
  avg_return_eod: number | null;
  avg_edge_eod: number | null;
  win_rate_eod: number | null;
}

export interface CalibrationActionSummary {
  action: 'BUY' | 'SELL';
  signals: number;
  avg_return_eod: number | null;
  avg_edge_eod: number | null;
  win_rate_eod: number | null;
}

export interface Calibration {
  labeledSignals: number;
  buckets: CalibrationBucket[];
  overall: CalibrationActionSummary[];
}
