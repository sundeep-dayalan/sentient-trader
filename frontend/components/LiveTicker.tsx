"use client";

import { useEffect, useRef } from "react";
import { Trade } from "@/lib/types";

const ACTION_STYLE: Record<string, string> = {
  BUY:  "bg-positive-soft text-positive border-positive-border",
  SELL: "bg-negative-soft text-negative border-negative-border",
  HOLD: "bg-surface-2 text-muted border-line",
};

interface LiveTickerProps {
  trades:        Trade[];
  newIds:        Set<string>;
  onTradeSelect: (trade: Trade) => void;
  selectedId:    string | null;
  onLoadMore:    () => void;
  isLoadingMore: boolean;
  hasMore:       boolean;
  previewLimit?: number;
}

export default function LiveTicker({
  trades,
  newIds,
  onTradeSelect,
  selectedId,
  onLoadMore,
  isLoadingMore,
  hasMore,
  previewLimit,
}: LiveTickerProps) {
  const sentinelRef   = useRef<HTMLDivElement>(null);
  const visibleTrades = previewLimit ? trades.slice(0, previewLimit) : trades;
  const isPreview     = Boolean(previewLimit);
  const onLoadMoreRef = useRef(onLoadMore);
  useEffect(() => { onLoadMoreRef.current = onLoadMore; }, [onLoadMore]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) onLoadMoreRef.current(); },
      { rootMargin: "0px 0px 120px 0px", threshold: 0 }
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="glass-panel flex h-full min-h-[360px] flex-col overflow-hidden rounded-2xl xl:min-h-0">

      {/* Header */}
      <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="pulse-dot h-2.5 w-2.5 rounded-full bg-positive" />
            <div>
              <p className="text-sm font-bold text-[var(--dashboard-text)]">Signal Feed</p>
              <p className="mt-0.5 text-xs text-[var(--dashboard-subtle)]">Headlines analyzed by the agent</p>
            </div>
          </div>
          <span className="rounded-full bg-[var(--dashboard-control)] px-3 py-1 text-[11px] font-semibold text-[var(--dashboard-subtle)]">
            {isPreview ? `${visibleTrades.length} latest` : `${trades.length} events`}
          </span>
        </div>
      </div>

      {/* Trade rows */}
      <div className="modern-scroll flex-1 overflow-y-auto p-3 pr-2">
        {trades.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-20 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface-2 text-muted">
              <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zm9.75-4.5a1.125 1.125 0 00-1.125 1.125v10.125a1.125 1.125 0 002.25 0V9.75A1.125 1.125 0 0012.75 8.625zm4.875 2.25a1.125 1.125 0 00-1.125 1.125v7.875a1.125 1.125 0 002.25 0v-7.875a1.125 1.125 0 00-1.125-1.125z" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-primary">No signals yet</p>
              <p className="mt-1 text-xs text-muted">Waiting for market events…</p>
            </div>
          </div>
        )}

        {visibleTrades.map(trade => (
          <button
            key={trade.id}
            onClick={() => onTradeSelect(trade)}
            className={[
              "mb-2 w-full rounded-xl border p-3.5 text-left transition-all duration-150",
              selectedId === trade.id
                ? "border-accent-border bg-selected shadow-sm"
                : "border-[var(--dashboard-border)] bg-[var(--dashboard-row)] hover:border-accent-border hover:bg-hover",
              newIds.has(trade.id) ? "slide-in" : "",
            ].join(" ")}
          >
            <div className="mb-2.5 flex items-center gap-2">
              <span className="font-mono text-[13px] font-bold text-accent">{trade.ticker}</span>
              <span className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${ACTION_STYLE[trade.trade_action]}`}>
                {trade.trade_action}
              </span>
              {trade.is_simulated && (
                <span className="rounded-full border border-warning-border bg-warning-soft px-2 py-0.5 text-[10px] font-semibold text-warning">
                  SIM
                </span>
              )}
              <span className="ml-auto shrink-0 text-[11px] text-muted">
                {new Date(trade.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>

            <p className="mb-3 line-clamp-2 text-xs leading-relaxed text-secondary">
              {trade.headline}
            </p>

            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11px]">
                <span className="text-muted">sentiment</span>
                <span className={`font-mono font-semibold ${trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}`}>
                  {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(2)}
                </span>
              </div>
              <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11px]">
                <span className="text-muted">conf</span>
                <span className="font-mono font-semibold text-accent">{(trade.confidence_score * 100).toFixed(0)}%</span>
              </div>
            </div>
          </button>
        ))}

        {/* Infinite scroll sentinel */}
        {!isPreview && (
          <div ref={sentinelRef} className="flex items-center justify-center px-4 py-4">
            {isLoadingMore && (
              <div className="flex items-center gap-2 text-xs text-muted">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-muted" />
                Loading more signals…
              </div>
            )}
            {!isLoadingMore && !hasMore && trades.length > 0 && (
              <span className="text-[11px] text-muted opacity-50">You've reached the beginning</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
