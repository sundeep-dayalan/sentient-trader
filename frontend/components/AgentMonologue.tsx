"use client";

import { Trade } from "@/lib/types";

interface AgentMonologueProps { trade: Trade | null; }

export default function AgentMonologue({ trade }: AgentMonologueProps) {
  if (!trade) {
    return (
      <div className="bg-surface border border-line rounded-2xl p-8 flex items-center justify-center shadow-card" style={{ minHeight: 220 }}>
        <div className="text-center space-y-3">
          <div className="w-12 h-12 rounded-2xl bg-surface-2 border border-line mx-auto flex items-center justify-center">
            <svg className="w-5 h-5 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <div>
            <p className="text-sm font-medium text-secondary">Agent Reasoning</p>
            <p className="text-xs text-muted mt-1">Select a trade to see the AI&apos;s analysis</p>
          </div>
        </div>
      </div>
    );
  }

  const isBuy  = trade.trade_action === "BUY";
  const isSell = trade.trade_action === "SELL";
  const actionColor = isBuy ? "text-positive" : isSell ? "text-negative" : "text-muted";
  const actionBg    = isBuy
    ? "bg-positive-soft border-positive-border"
    : isSell
    ? "bg-negative-soft border-negative-border"
    : "bg-surface-2 border-line";

  const sentimentPct = ((trade.sentiment_score + 1) / 2) * 100;

  return (
    <div className="bg-surface border border-line rounded-2xl overflow-hidden shadow-card">

      {/* Card header */}
      <div className="flex items-center gap-3 px-5 py-4 border-b border-line">
        <div className="flex-1 min-w-0">
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">Agent Reasoning</p>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-base font-bold font-mono text-accent">{trade.ticker}</span>
            <span className="text-xs text-muted">·</span>
            <span className="text-xs text-secondary">
              {new Date(trade.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
            </span>
          </div>
        </div>
        <span className={`text-xs font-bold px-3 py-1.5 rounded-xl border ${actionColor} ${actionBg}`}>
          {trade.trade_action}
        </span>
      </div>

      <div className="p-5 space-y-5">

        {/* Headline */}
        <div>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">Headline</p>
          <p className="text-sm text-primary leading-relaxed pl-3 border-l-2 border-accent italic">
            &ldquo;{trade.headline}&rdquo;
          </p>
        </div>

        {/* Sentiment + Confidence side by side */}
        <div className="grid grid-cols-2 gap-5">
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">Sentiment</p>
              <span className={`text-xs font-bold font-mono ${trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}`}>
                {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(3)}
              </span>
            </div>
            <div className="relative h-1.5 bg-surface-2 rounded-full overflow-hidden border border-line">
              <div className="absolute left-1/2 top-0 bottom-0 w-px bg-muted/30" />
              <div
                className={`absolute top-0 h-full w-2 rounded-full ${trade.sentiment_score >= 0 ? "bg-positive" : "bg-negative"}`}
                style={{ left: `calc(${sentimentPct}% - 4px)` }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-muted mt-1">
              <span>Bearish</span><span>Bullish</span>
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-[10px] font-semibold text-muted uppercase tracking-wider">Confidence</p>
              <span className="text-xs font-bold font-mono text-accent">
                {(trade.confidence_score * 100).toFixed(0)}%
              </span>
            </div>
            <div className="h-1.5 bg-surface-2 rounded-full overflow-hidden border border-line">
              <div
                className="h-full bg-accent rounded-full transition-all duration-700"
                style={{ width: `${trade.confidence_score * 100}%` }}
              />
            </div>
            <div className="flex justify-between text-[9px] text-muted mt-1">
              <span>Low</span><span>High</span>
            </div>
          </div>
        </div>

        {/* AI Reasoning */}
        <div>
          <p className="text-[10px] font-semibold text-muted uppercase tracking-wider mb-2">AI Reasoning</p>
          <p className="text-sm text-secondary leading-relaxed">{trade.reasoning}</p>
        </div>

        {/* Alpaca order confirmation */}
        {trade.order_id && (
          <div className="bg-positive-soft border border-positive-border rounded-xl px-4 py-3 space-y-1">
            <div className="flex items-center gap-2">
              <svg className="w-3.5 h-3.5 text-positive shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              <p className="text-[10px] font-semibold text-positive uppercase tracking-wider">Order Executed via Alpaca</p>
            </div>
            <p className="text-[11px] font-mono text-positive/80 break-all">{trade.order_id}</p>
            <p className="text-[10px] text-positive/60">
              {trade.quantity} share(s) · Paper Trading · Market Order
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
