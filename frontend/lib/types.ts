/**
 * Shared TypeScript types used across the frontend.
 * These mirror the Supabase `trades` table columns, with optional legacy fields
 * kept so older development rows still render during the decision_trace rollout.
 */

export interface PersonaOpinion {
  name:       string;                               // "Momentum Trader" | "Value Investor" | "Risk Manager"
  stance:     "BULLISH" | "BEARISH" | "NEUTRAL";
  conviction: number;                               // 0.0–1.0, shown as a conviction bar in the UI
  view:       string;                               // one-sentence headline take (always visible on card)
  reasoning:  string;                               // full 2-3 sentence reasoning (shown below the take)
  model?:     string | null;                        // LLM model that powered this persona
}

export interface LLMOperationTrace {
  step: string;
  kind: "persona_analysis" | "portfolio_manager_synthesis" | string;
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
  llm_operations?: LLMOperationTrace[];
  committee_debate?: PersonaOpinion[];
  portfolio_manager_decision?: {
    model?: string | null;
    sentiment?: number;
    confidence?: number;
    reasoning?: string;
    action?: "BUY" | "SELL" | "HOLD";
  } | null;
  risk_gate?: unknown;
  execution?: unknown;
  error?: string | null;
}

export interface Trade {
  id: string;
  created_at: string;
  ticker: string;
  headline: string;
  article_url: string | null;
  article_source: string | null;
  article_id: string | null;
  sentiment_score: number;   // -1.0 to 1.0
  confidence_score: number;  // 0.0 to 1.0
  reasoning: string;         // committee consensus summary (one-liner shown on trade cards)
  trade_action: "BUY" | "SELL" | "HOLD";
  order_id: string | null;   // Alpaca order UUID — null if HOLD
  quantity: number;
  is_simulated: boolean;     // true when injected via the Simulate button
  decision_trace?: DecisionTrace | PersonaOpinion[] | null; // generic Decision Core JSONB trace
  committee_debate?: PersonaOpinion[] | null;              // legacy pre-008 rows
  model?: string | null;                                   // legacy pre-008 synthesis model
}

export interface DashboardStats {
  analyzed: number;
  executed: number;
  buyOrders: number;
  sellOrders: number;
  riskGated: number;
  avgSentiment: number;
}

export interface PortfolioPoint {
  timestamp: string;  // ISO string
  equity: number;     // dollar value
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
