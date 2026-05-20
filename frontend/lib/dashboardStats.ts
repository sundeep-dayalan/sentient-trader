import { DashboardStats, Trade } from "@/lib/types";

export type TradeStatsRow = Pick<Trade, "trade_action" | "order_id" | "sentiment_score"> & {
  decision_trace?: Trade["decision_trace"];
};

export function isRiskGated(row: TradeStatsRow): boolean {
  const trace = row.decision_trace;
  if (trace && !Array.isArray(trace) && typeof trace === "object") {
    const riskGate = trace.risk_gate as { should_trade?: boolean } | undefined;
    if (riskGate?.should_trade === false && !row.order_id?.trim()) return true;
  }
  return row.trade_action === "HOLD";
}

export function computeDashboardStats(rows: TradeStatsRow[]): DashboardStats {
  let buyOrders = 0;
  let sellOrders = 0;
  let riskGated = 0;
  let executed = 0;
  let sentimentTotal = 0;

  rows.forEach(row => {
    if (row.trade_action === "BUY") buyOrders += 1;
    if (row.trade_action === "SELL") sellOrders += 1;
    if (isRiskGated(row)) riskGated += 1;
    if (row.order_id?.trim()) executed += 1;

    const sentiment = Number(row.sentiment_score);
    sentimentTotal += Number.isFinite(sentiment) ? sentiment : 0;
  });

  return {
    analyzed: rows.length,
    executed,
    buyOrders,
    sellOrders,
    riskGated,
    avgSentiment: rows.length > 0 ? sentimentTotal / rows.length : 0,
  };
}
