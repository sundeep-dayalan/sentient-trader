"use client";

import { Trade } from "@/lib/types";

interface StatsBarProps { trades: Trade[]; }

export default function StatsBar({ trades }: StatsBarProps) {
  const buys  = trades.filter(t => t.trade_action === "BUY").length;
  const sells = trades.filter(t => t.trade_action === "SELL").length;
  const holds = trades.filter(t => t.trade_action === "HOLD").length;
  const avg   = trades.length > 0
    ? trades.reduce((s, t) => s + t.sentiment_score, 0) / trades.length
    : 0;

  const stats = [
    { label: "Analyzed",      value: trades.length,                           sub: "events scanned",       accent: "text-secondary", indicator: "bg-cyan",     rail: "from-cyan"      },
    { label: "Executed",      value: buys + sells,                            sub: "orders placed",        accent: "text-accent",    indicator: "bg-accent",   rail: "from-accent"    },
    { label: "Buy Orders",    value: buys,                                    sub: "long bias",            accent: "text-positive",  indicator: "bg-positive", rail: "from-positive"  },
    { label: "Sell Orders",   value: sells,                                   sub: "short bias",           accent: "text-negative",  indicator: "bg-negative", rail: "from-negative"  },
    { label: "Held",          value: holds,                                   sub: "risk gated",           accent: "text-muted",     indicator: "bg-muted",    rail: "from-muted"     },
    { label: "Avg Sentiment", value: (avg >= 0 ? "+" : "") + avg.toFixed(2), sub: avg >= 0 ? "bullish read" : "bearish read", accent: avg >= 0 ? "text-positive" : "text-negative", indicator: avg >= 0 ? "bg-positive" : "bg-negative", rail: avg >= 0 ? "from-positive" : "from-negative" },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 min-[520px]:grid-cols-3 xl:grid-cols-6">
      {stats.map(s => (
        <div key={s.label} className="metric-card glass-panel-subtle rounded-lg px-4 py-3">
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-[11px] font-semibold uppercase text-muted">{s.label}</span>
            <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${s.indicator}`} />
          </div>
          <div className={`mt-2.5 font-mono text-xl font-semibold leading-none ${s.accent}`}>
            {s.value}
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={`h-px flex-1 bg-gradient-to-r ${s.rail} to-transparent`} />
            <span className="shrink-0 text-[10px] text-muted">{s.sub}</span>
          </div>
        </div>
      ))}
    </div>
  );
}
