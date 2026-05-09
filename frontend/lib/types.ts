/**
 * Shared TypeScript types used across the frontend.
 * These mirror the Supabase `trades` table columns exactly.
 */

export interface Trade {
  id: string;
  created_at: string;
  ticker: string;
  headline: string;
  sentiment_score: number;   // -1.0 to 1.0
  confidence_score: number;  // 0.0 to 1.0
  reasoning: string;
  trade_action: "BUY" | "SELL" | "HOLD";
  order_id: string | null;   // Alpaca order UUID — null if HOLD
  quantity: number;
}

export interface PortfolioPoint {
  timestamp: string;  // ISO string
  equity: number;     // dollar value
}

/** One scenario injected by the "Simulate Market Shock" button */
export interface SimulationScenario {
  ticker: string;
  headline: string;
  source: string;
}
