import { computeDashboardStats } from '@/lib/dashboardStats';
import { DashboardStats, Trade } from '@/lib/types';

interface StatsBarProps {
  trades: Trade[];
  stats?: DashboardStats | null;
}

const ACCENT_COLORS: Record<string, { value: string; dot: string }> = {
  analyzed: { value: 'text-primary', dot: 'bg-accent' },
  executed: { value: 'text-accent', dot: 'bg-accent' },
  buy: { value: 'text-positive', dot: 'bg-positive' },
  sell: { value: 'text-negative', dot: 'bg-negative' },
  hold: { value: 'text-muted', dot: 'bg-muted' },
  prescreen: { value: 'text-warning', dot: 'bg-warning' },
  sentiment: { value: 'text-primary', dot: 'bg-cyan' },
};

export default function StatsBar({ trades, stats }: StatsBarProps) {
  const fallbackStats = computeDashboardStats(trades);
  const dashboardStats = stats ?? fallbackStats;
  const avg = dashboardStats.avgSentiment;

  const statItems = [
    { key: 'analyzed', label: 'Analyzed', value: dashboardStats.analyzed, sub: 'events scanned' },
    { key: 'executed', label: 'Executed', value: dashboardStats.executed, sub: 'orders placed' },
    { key: 'buy', label: 'Buy Recs', value: dashboardStats.buyOrders, sub: 'PM recommendations' },
    {
      key: 'sell',
      label: 'Sell Recs',
      value: dashboardStats.sellOrders,
      sub: 'PM recommendations',
    },
    { key: 'hold', label: 'Gated/Held', value: dashboardStats.riskGated, sub: 'no order sent' },
    {
      key: 'prescreen',
      label: 'Pre-screened',
      value: dashboardStats.preScreened ?? 0,
      sub: 'LLM skipped',
    },
    {
      key: 'sentiment',
      label: 'Avg Sentiment',
      value: (avg >= 0 ? '+' : '') + avg.toFixed(2),
      sub: avg >= 0 ? 'bullish lean' : 'bearish lean',
    },
  ];

  return (
    <div className="grid grid-cols-[repeat(auto-fit,minmax(132px,1fr))] gap-3">
      {statItems.map((s) => {
        const c = ACCENT_COLORS[s.key];
        return (
          <div key={s.label} className="metric-card glass-panel-subtle rounded-xl px-4 py-2.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-muted">{s.label}</span>
              <span className={`h-2 w-2 shrink-0 rounded-full ${c.dot} opacity-70`} />
            </div>
            <div className={`mt-1.5 font-mono text-xl font-bold leading-none ${c.value}`}>
              {s.value}
            </div>
            <p className="mt-1 text-[11px] text-muted">{s.sub}</p>
          </div>
        );
      })}
    </div>
  );
}
