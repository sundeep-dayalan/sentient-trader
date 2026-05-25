import { DashboardStats, Trade } from '@/lib/types';

export type TradeStatsRow = Pick<Trade, 'trade_action' | 'order_id' | 'sentiment_score'> & {
  pm_recommendation?: Trade['pm_recommendation'];
  executed_action?: Trade['executed_action'];
  risk_should_trade?: Trade['risk_should_trade'];
  decision_path?: Trade['decision_path'];
  decision_trace?: Trade['decision_trace'];
};

export function isRiskGated(row: TradeStatsRow): boolean {
  if (row.risk_should_trade === false && !row.executed_action && !row.order_id?.trim()) {
    return true;
  }
  const trace = row.decision_trace;
  if (trace && !Array.isArray(trace) && typeof trace === 'object') {
    const riskGate = trace.risk_gate as { should_trade?: boolean } | undefined;
    if (riskGate?.should_trade === false && !row.order_id?.trim()) return true;
  }
  return row.trade_action === 'HOLD';
}

export function computeDashboardStats(rows: TradeStatsRow[]): DashboardStats {
  let buyOrders = 0;
  let sellOrders = 0;
  let riskGated = 0;
  let executed = 0;
  let preScreened = 0;
  let fullDebates = 0;
  let sentimentTotal = 0;

  rows.forEach((row) => {
    const recommendation = row.pm_recommendation ?? row.trade_action;
    if (recommendation === 'BUY') buyOrders += 1;
    if (recommendation === 'SELL') sellOrders += 1;
    if (isRiskGated(row)) riskGated += 1;
    if (row.executed_action || row.order_id?.trim()) executed += 1;
    if (row.decision_path === 'pre_screen') preScreened += 1;
    if (row.decision_path === 'full_debate') fullDebates += 1;

    const sentiment = Number(row.sentiment_score);
    sentimentTotal += Number.isFinite(sentiment) ? sentiment : 0;
  });

  return {
    analyzed: rows.length,
    executed,
    buyOrders,
    sellOrders,
    riskGated,
    preScreened,
    fullDebates,
    avgSentiment: rows.length > 0 ? sentimentTotal / rows.length : 0,
  };
}
