"use client";

import { Trade } from "@/lib/types";

interface StatsBarProps { trades: Trade[]; }

const ACCENT_COLORS: Record<string, { value: string; sub: string; bar: string; dot: string }> = {
  analyzed:  { value: "text-primary",  sub: "text-muted",    bar: "bg-accent",    dot: "bg-accent"    },
  executed:  { value: "text-accent",   sub: "text-muted",    bar: "bg-accent",    dot: "bg-accent"    },
  buy:       { value: "text-positive", sub: "text-muted",    bar: "bg-positive",  dot: "bg-positive"  },
  sell:      { value: "text-negative", sub: "text-muted",    bar: "bg-negative",  dot: "bg-negative"  },
  hold:      { value: "text-muted",    sub: "text-muted",    bar: "bg-muted",     dot: "bg-muted"     },
  sentiment: { value: "text-primary",  sub: "text-muted",    bar: "bg-cyan",      dot: "bg-cyan"      },
};

export default function StatsBar({ trades }: StatsBarProps) {
  const buys  = trades.filter(t => t.trade_action === "BUY").length;
  const sells = trades.filter(t => t.trade_action === "SELL").length;
  const holds = trades.filter(t => t.trade_action === "HOLD").length;
  const avg   = trades.length > 0
    ? trades.reduce((s, t) => s + t.sentiment_score, 0) / trades.length
    : 0;

  const stats = [
    { key: "analyzed",  label: "Analyzed",      value: trades.length,                             sub: "events scanned"                                  },
    { key: "executed",  label: "Executed",       value: buys + sells,                              sub: "orders placed"                                   },
    { key: "buy",       label: "Buy Orders",     value: buys,                                      sub: "long bias"                                       },
    { key: "sell",      label: "Sell Orders",    value: sells,                                     sub: "short bias"                                      },
    { key: "hold",      label: "Risk Gated",     value: holds,                                     sub: "held signals"                                    },
    { key: "sentiment", label: "Avg Sentiment",  value: (avg >= 0 ? "+" : "") + avg.toFixed(2),   sub: avg >= 0 ? "bullish lean" : "bearish lean"        },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 min-[520px]:grid-cols-3 xl:grid-cols-6">
      {stats.map(s => {
        const c = ACCENT_COLORS[s.key];
        return (
          <div key={s.label} className="metric-card glass-panel-subtle rounded-2xl px-4 py-4">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-semibold text-muted">{s.label}</span>
              <span className={`h-2 w-2 shrink-0 rounded-full ${c.dot} opacity-70`} />
            </div>
            <div className={`mt-3 font-mono text-2xl font-bold leading-none ${c.value}`}>
              {s.value}
            </div>
            <div className="mt-3">
              <div className="h-0.5 w-full overflow-hidden rounded-full bg-surface-3">
                <div className={`h-full w-1/2 rounded-full ${c.bar} opacity-60`} />
              </div>
              <p className="mt-1.5 text-[11px] text-muted">{s.sub}</p>
            </div>
          </div>
        );
      })}
    </div>
  );
}
