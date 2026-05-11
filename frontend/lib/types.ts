/**
 * Shared TypeScript types used across the frontend.
 * These mirror the Supabase `trades` table columns exactly.
 */

export interface PersonaOpinion {
  name:       string;                               // "Momentum Trader" | "Value Investor" | "Risk Manager"
  stance:     "BULLISH" | "BEARISH" | "NEUTRAL";
  conviction: number;                               // 0.0–1.0, shown as a conviction bar in the UI
  view:       string;                               // one-sentence headline take (always visible on card)
  reasoning:  string;                               // full 2-3 sentence reasoning (shown below the take)
  model?:     string | null;                        // LLM model that powered this persona
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
  committee_debate: PersonaOpinion[] | null;  // three persona opinions; null for pre-migration rows
  model?: string | null;     // LLM model that powered the synthesis
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
