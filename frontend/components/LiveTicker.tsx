"use client";

import { useEffect, useRef } from "react";
import { Trade } from "@/lib/types";

const ACTION: Record<string, string> = {
  BUY:  "bg-positive-soft text-positive border-positive-border",
  SELL: "bg-negative-soft text-negative border-negative-border",
  HOLD: "bg-surface-3 text-muted border-line",
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
  const sentinelRef = useRef<HTMLDivElement>(null);
  const visibleTrades = previewLimit ? trades.slice(0, previewLimit) : trades;
  const isPreview = Boolean(previewLimit);

  // Keep a stable ref to onLoadMore so the observer is created exactly once
  // and never needs to be torn down just because the callback identity changed.
  const onLoadMoreRef = useRef(onLoadMore);
  useEffect(() => { onLoadMoreRef.current = onLoadMore; }, [onLoadMore]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) onLoadMoreRef.current();
      },
      {
        // Trigger 120px before the sentinel actually hits the viewport edge
        // so there's no visible pause while data loads.
        rootMargin: "0px 0px 120px 0px",
        threshold: 0,
      }
    );

    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []); // single setup — stable via ref above

  return (
    <div className="glass-panel flex h-full min-h-[360px] flex-col overflow-hidden rounded-lg xl:min-h-0">

      {/* Header */}
      <div className="shrink-0 border-b border-line px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="pulse-dot h-2 w-2 rounded-full bg-positive shadow-glow-positive" />
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold">Recent news signals</p>
            <p className="mt-0.5 text-[11px] text-muted">Latest market headlines analyzed by the agent</p>
          </div>
          <span className="rounded-md border border-cyan-border bg-cyan-soft px-2 py-1 font-mono text-[10px] text-cyan">
            {isPreview ? `${visibleTrades.length} latest` : `${trades.length} events`}
          </span>
        </div>
      </div>

      {/* Trade rows */}
      <div className="flex-1 overflow-y-auto p-2">
        {trades.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-20 text-muted">
            <div className="flex h-11 w-11 items-center justify-center rounded-lg border border-line bg-surface-2">
              <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zm9.75-4.5a1.125 1.125 0 00-1.125 1.125v10.125a1.125 1.125 0 002.25 0V9.75A1.125 1.125 0 0012.75 8.625zm4.875 2.25a1.125 1.125 0 00-1.125 1.125v7.875a1.125 1.125 0 002.25 0v-7.875a1.125 1.125 0 00-1.125-1.125z" />
              </svg>
            </div>
            <p className="text-sm">Waiting for market events...</p>
          </div>
        )}

        {visibleTrades.map(trade => {
          const isSimulated = trade.is_simulated;

          return (
            <button
              key={trade.id}
              onClick={() => onTradeSelect(trade)}
              className={[
                "mb-2 w-full rounded-lg border px-3.5 py-2.5 text-left transition-all duration-200",
                selectedId === trade.id
                  ? "border-accent-border bg-selected shadow-glow"
                  : "border-line bg-surface hover:border-cyan-border hover:bg-hover",
                newIds.has(trade.id) ? "slide-in" : "",
              ].join(" ")}
            >
              <div className="mb-2 flex items-center gap-2">
                <span className="w-16 shrink-0 truncate font-mono text-[13px] font-bold text-accent">
                  {trade.ticker}
                </span>

                <span className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold ${ACTION[trade.trade_action]}`}>
                  {trade.trade_action}
                </span>

                {isSimulated && (
                  <span className="rounded border border-warning-border bg-warning-soft px-1.5 py-0.5 text-[9px] font-semibold text-warning">
                    SIMULATED
                  </span>
                )}

                <span className="ml-auto shrink-0 font-mono text-[10px] text-muted">
                  {new Date(trade.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </span>
              </div>

              <p className="mb-2.5 line-clamp-2 text-xs leading-relaxed text-secondary">
                {trade.headline}
              </p>

              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-line bg-surface-2 px-2 py-1 font-mono text-[10px] text-muted">
                  sentiment{" "}
                  <span className={trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}>
                    {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(2)}
                  </span>
                </span>
                <span className="rounded-md border border-line bg-surface-2 px-2 py-1 font-mono text-[10px] text-muted">
                  conf <span className="text-accent">{(trade.confidence_score * 100).toFixed(0)}%</span>
                </span>
              </div>
            </button>
          );
        })}

        {/* ── Infinite scroll sentinel ──────────────────────────────── */}
        {!isPreview && (
          <div ref={sentinelRef} className="flex items-center justify-center px-4 py-3">
            {isLoadingMore && (
              <div className="flex items-center gap-2 text-[11px] text-muted">
                <span className="h-3 w-3 animate-spin rounded-full border-2 border-line border-t-muted" />
                Loading more...
              </div>
            )}
            {!isLoadingMore && !hasMore && trades.length > 0 && (
              <span className="font-mono text-[10px] text-muted opacity-60">end of history</span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
