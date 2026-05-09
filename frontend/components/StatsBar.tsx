"use client";

/**
 * StatsBar — six at-a-glance metrics across the top of the dashboard.
 * Gives a recruiter an instant read on how active the agent has been.
 */

import { Trade } from "@/lib/types";

interface StatsBarProps {
  trades: Trade[];
}

export default function StatsBar({ trades }: StatsBarProps) {
  const buys  = trades.filter((t) => t.trade_action === "BUY").length;
  const sells = trades.filter((t) => t.trade_action === "SELL").length;
  const holds = trades.filter((t) => t.trade_action === "HOLD").length;

  const avgSentiment =
    trades.length > 0
      ? trades.reduce((sum, t) => sum + t.sentiment_score, 0) / trades.length
      : 0;

  const stats = [
    {
      label: "ANALYZED",
      value: trades.length,
      color: "text-accent",
    },
    {
      label: "EXECUTED",
      value: buys + sells,
      color: "text-accent",
    },
    {
      label: "BUY ORDERS",
      value: buys,
      color: "text-positive",
    },
    {
      label: "SELL ORDERS",
      value: sells,
      color: "text-negative",
    },
    {
      label: "HELD",
      value: holds,
      color: "text-muted",
    },
    {
      label: "AVG SENTIMENT",
      value: (avgSentiment >= 0 ? "+" : "") + avgSentiment.toFixed(2),
      color: avgSentiment >= 0 ? "text-positive" : "text-negative",
    },
  ];

  return (
    <div className="grid grid-cols-3 md:grid-cols-6 gap-px bg-line rounded-lg overflow-hidden mb-6">
      {stats.map((stat) => (
        <div key={stat.label} className="bg-surface px-4 py-3">
          <div className="text-[10px] text-muted tracking-widest mb-1">
            {stat.label}
          </div>
          <div className={`text-2xl font-bold ${stat.color}`}>{stat.value}</div>
        </div>
      ))}
    </div>
  );
}
