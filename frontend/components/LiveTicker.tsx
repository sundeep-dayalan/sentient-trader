"use client";

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase";
import { Trade }        from "@/lib/types";

const ACTION: Record<string, string> = {
  BUY:  "bg-positive-soft text-positive border-positive-border",
  SELL: "bg-negative-soft text-negative border-negative-border",
  HOLD: "bg-surface-2 text-muted border-line",
};

interface LiveTickerProps {
  initialTrades: Trade[];
  onTradeSelect: (trade: Trade) => void;
  selectedId:    string | null;
}

export default function LiveTicker({ initialTrades, onTradeSelect, selectedId }: LiveTickerProps) {
  const [trades, setTrades] = useState<Trade[]>(initialTrades);
  const [newIds, setNewIds]  = useState<Set<string>>(new Set());

  useEffect(() => {
    const supabase = createClient();
    const channel  = supabase
      .channel("trades-realtime")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "trades" }, payload => {
        const t = payload.new as Trade;
        setTrades(prev => [t, ...prev].slice(0, 20));
        setNewIds(prev => new Set(prev).add(t.id));
        setTimeout(() => setNewIds(prev => { const n = new Set(prev); n.delete(t.id); return n; }), 500);
      })
      .subscribe();
    return () => { supabase.removeChannel(channel); };
  }, []);

  return (
    <div className="bg-surface border border-line rounded-2xl overflow-hidden shadow-card flex flex-col" style={{ minHeight: 500 }}>

      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3.5 border-b border-line shrink-0">
        <span className="pulse-dot w-2 h-2 rounded-full bg-positive" />
        <span className="text-sm font-semibold">Live Feed</span>
        <span className="ml-auto text-[11px] font-mono text-muted bg-surface-2 border border-line px-2 py-0.5 rounded-full">
          {trades.length} events
        </span>
      </div>

      {/* Trade rows */}
      <div className="overflow-y-auto flex-1">
        {trades.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full py-20 gap-3 text-muted">
            <div className="w-10 h-10 rounded-full border border-line flex items-center justify-center">
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zm9.75-4.5a1.125 1.125 0 00-1.125 1.125v10.125a1.125 1.125 0 002.25 0V9.75A1.125 1.125 0 0012.75 8.625zm4.875 2.25a1.125 1.125 0 00-1.125 1.125v7.875a1.125 1.125 0 002.25 0v-7.875a1.125 1.125 0 00-1.125-1.125z" />
              </svg>
            </div>
            <p className="text-sm">Waiting for market events…</p>
          </div>
        )}

        {trades.map(trade => (
          <button
            key={trade.id}
            onClick={() => onTradeSelect(trade)}
            className={[
              "w-full text-left px-4 py-3.5 border-b border-line/50 transition-all duration-150",
              selectedId === trade.id
                ? "bg-selected border-l-2 border-l-accent"
                : "hover:bg-hover",
              newIds.has(trade.id) ? "slide-in" : "",
            ].join(" ")}
          >
            <div className="flex items-center gap-2 mb-1.5">
              <span className="font-mono font-bold text-[13px] text-accent w-16 shrink-0 truncate">
                {trade.ticker}
              </span>
              <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-md border ${ACTION[trade.trade_action]}`}>
                {trade.trade_action}
              </span>
              <span className="ml-auto text-[10px] text-muted font-mono shrink-0">
                {new Date(trade.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>

            <p className="text-xs text-secondary line-clamp-2 leading-relaxed mb-2">
              {trade.headline}
            </p>

            <div className="flex items-center gap-3">
              <span className="text-[10px] font-mono text-muted">
                sentiment{" "}
                <span className={trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}>
                  {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(2)}
                </span>
              </span>
              <span className="text-muted/40">·</span>
              <span className="text-[10px] font-mono text-muted">
                conf <span className="text-accent">{(trade.confidence_score * 100).toFixed(0)}%</span>
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
