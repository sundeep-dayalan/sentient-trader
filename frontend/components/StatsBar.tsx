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
    { label: "Analyzed",     value: trades.length,                              sub: "events",                         accent: "text-secondary",  indicator: null        },
    { label: "Executed",     value: buys + sells,                               sub: "orders placed",                  accent: "text-accent",     indicator: "accent"    },
    { label: "Buy Orders",   value: buys,                                       sub: "long positions",                 accent: "text-positive",   indicator: "positive"  },
    { label: "Sell Orders",  value: sells,                                      sub: "short positions",                accent: "text-negative",   indicator: "negative"  },
    { label: "Held",         value: holds,                                      sub: "skipped",                        accent: "text-muted",      indicator: null        },
    { label: "Avg Sentiment",value: (avg >= 0 ? "+" : "") + avg.toFixed(2),    sub: avg >= 0 ? "bullish bias" : "bearish bias", accent: avg >= 0 ? "text-positive" : "text-negative", indicator: null },
  ];

  const indicatorColor: Record<string, string> = {
    accent:   "bg-accent",
    positive: "bg-positive",
    negative: "bg-negative",
  };

  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
      {stats.map(s => (
        <div key={s.label} className="bg-surface border border-line rounded-2xl px-4 py-4 shadow-card flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            {s.indicator && (
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${indicatorColor[s.indicator]}`} />
            )}
            <span className="text-[11px] font-medium text-muted truncate">{s.label}</span>
          </div>
          <div className={`text-2xl font-bold font-mono tracking-tight leading-none ${s.accent}`}>
            {s.value}
          </div>
          <div className="text-[10px] text-muted">{s.sub}</div>
        </div>
      ))}
    </div>
  );
}
