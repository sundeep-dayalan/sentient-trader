"use client";

import { Trade } from "@/lib/types";

interface AgentMonologueProps { trade: Trade | null; }

export default function AgentMonologue({ trade }: AgentMonologueProps) {
  if (!trade) {
    return (
      <div className="glass-panel flex h-full min-h-[280px] items-center justify-center rounded-lg p-8 xl:min-h-0">
        <div className="max-w-sm text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-lg border border-line bg-surface-2">
            <svg className="h-5 w-5 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <p className="mt-4 text-sm font-semibold text-secondary">Decision core on standby</p>
          <p className="mt-1 text-xs text-muted">Select a signal to inspect the agent reasoning trace.</p>
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
    : "bg-surface-3 border-line";

  const sentimentPct = Math.min(100, Math.max(0, ((trade.sentiment_score + 1) / 2) * 100));
  const confidencePct = Math.round(trade.confidence_score * 100);

  return (
    <div className="glass-panel flex h-full min-h-[360px] flex-col overflow-hidden rounded-lg xl:min-h-0">

      {/* Card header */}
      <div className="shrink-0 border-b border-line px-5 py-3.5">
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-[10px] font-semibold uppercase text-muted">Decision Core</p>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <span className="font-mono text-2xl font-semibold text-accent">{trade.ticker}</span>
              <span className="rounded-md border border-line bg-surface-2 px-2 py-1 text-[11px] text-secondary">
                {new Date(trade.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          </div>
          <span className={`rounded-md border px-3 py-1.5 text-xs font-bold ${actionColor} ${actionBg}`}>
            {trade.trade_action}
          </span>
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4">

        {/* Headline */}
        <div className="rounded-lg border border-line bg-surface-2 p-3.5">
          <p className="mb-2 text-[10px] font-semibold uppercase text-muted">Market Headline</p>
          <p className="border-l-2 border-accent pl-3 text-sm leading-relaxed text-primary">
            &quot;{trade.headline}&quot;
          </p>
        </div>

        {/* Sentiment + Confidence side by side */}
        <div className="grid gap-4 md:grid-cols-2">
          <div className="rounded-lg border border-line bg-surface p-3.5">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-[10px] font-semibold uppercase text-muted">Sentiment Vector</p>
              <span className={`font-mono text-xs font-bold ${trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}`}>
                {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(3)}
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full border border-line bg-surface-3">
              <div className="absolute bottom-0 left-1/2 top-0 w-px bg-muted opacity-50" />
              <div
                className={`absolute top-0 h-full w-2 rounded-full ${trade.sentiment_score >= 0 ? "bg-positive" : "bg-negative"}`}
                style={{ left: `calc(${sentimentPct}% - 4px)` }}
              />
            </div>
            <div className="mt-2 flex justify-between text-[9px] text-muted">
              <span>Bearish</span><span>Bullish</span>
            </div>
          </div>

          <div className="rounded-lg border border-line bg-surface p-3.5">
            <div className="mb-3 flex items-center justify-between">
              <p className="text-[10px] font-semibold uppercase text-muted">Confidence</p>
              <span className="font-mono text-xs font-bold text-accent">
                {confidencePct}%
              </span>
            </div>
            <div className="h-2 overflow-hidden rounded-full border border-line bg-surface-3">
              <div
                className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-all duration-700"
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <div className="mt-2 flex justify-between text-[9px] text-muted">
              <span>Low</span><span>High</span>
            </div>
          </div>
        </div>

        {/* AI Reasoning */}
        <div className="rounded-lg border border-line bg-surface p-3.5">
          <p className="mb-2 text-[10px] font-semibold uppercase text-muted">AI Reasoning</p>
          <p className="text-sm leading-relaxed text-secondary">{trade.reasoning}</p>
        </div>

        {/* Alpaca order confirmation */}
        {trade.order_id && (
          <div className="space-y-1 rounded-lg border border-positive-border bg-positive-soft px-4 py-3">
            <div className="flex items-center gap-2">
              <svg className="h-3.5 w-3.5 shrink-0 text-positive" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
              <p className="text-[10px] font-semibold uppercase text-positive">Order Executed via Alpaca</p>
            </div>
            <p className="break-all font-mono text-[11px] text-positive opacity-80">{trade.order_id}</p>
            <p className="text-[10px] text-positive opacity-75">
              {trade.quantity} share(s) | Paper Trading | Market Order
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
