"use client";

/**
 * LiveTicker — scrolling feed of the 20 most recent AI decisions.
 *
 * Subscribes to the Supabase `trades` table in real-time via Postgres
 * change notifications. When the agent writes a new row on the backend,
 * it appears here instantly — no polling, no page refresh.
 *
 * Clicking a row loads that trade's reasoning into the AgentMonologue panel.
 */

import { useEffect, useState } from "react";
import { createClient } from "@/lib/supabase";
import { Trade } from "@/lib/types";

// Color scheme for each trade action badge
const ACTION_STYLES: Record<string, string> = {
  BUY:  "bg-positive/20 text-positive border border-positive/30",
  SELL: "bg-negative/20 text-negative border border-negative/30",
  HOLD: "bg-muted/10  text-muted   border border-muted/20",
};

interface LiveTickerProps {
  initialTrades: Trade[];
  onTradeSelect: (trade: Trade) => void;
  selectedId:    string | null;
}

export default function LiveTicker({
  initialTrades,
  onTradeSelect,
  selectedId,
}: LiveTickerProps) {
  const [trades, setTrades] = useState<Trade[]>(initialTrades);

  // IDs of rows that just arrived — used to trigger the slide-in CSS animation
  const [newIds, setNewIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    const supabase = createClient();

    const channel = supabase
      .channel("trades-realtime")
      .on(
        "postgres_changes",
        { event: "INSERT", schema: "public", table: "trades" },
        (payload) => {
          const incoming = payload.new as Trade;

          // Prepend and keep the list at 20 rows maximum
          setTrades((prev) => [incoming, ...prev].slice(0, 20));

          // Tag as new so the CSS animation fires, then clear after it finishes
          setNewIds((prev) => new Set(prev).add(incoming.id));
          setTimeout(() => {
            setNewIds((prev) => {
              const next = new Set(prev);
              next.delete(incoming.id);
              return next;
            });
          }, 600);
        }
      )
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  return (
    <div className="bg-surface border border-line rounded-lg overflow-hidden">

      {/* Header row */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-line">
        <span className="pulse-dot w-2 h-2 rounded-full bg-positive shrink-0" />
        <span className="text-[11px] font-bold tracking-widest text-muted uppercase">
          Live Feed
        </span>
        <span className="ml-auto text-[11px] text-muted">{trades.length} events</span>
      </div>

      {/* Trade list */}
      <div className="overflow-y-auto max-h-[500px]">
        {trades.length === 0 && (
          <div className="px-4 py-10 text-center text-muted text-sm">
            Waiting for market events...
          </div>
        )}

        {trades.map((trade) => (
          <button
            key={trade.id}
            onClick={() => onTradeSelect(trade)}
            className={[
              "w-full text-left px-4 py-3 border-b border-line/50",
              "hover:bg-line/40 transition-colors duration-150",
              selectedId === trade.id ? "bg-line/60 border-l-2 border-l-accent" : "",
              newIds.has(trade.id) ? "slide-in" : "",
            ].join(" ")}
          >
            {/* Top row: ticker chip + action badge + timestamp */}
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-accent font-bold text-sm w-14 shrink-0 truncate">
                {trade.ticker}
              </span>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded ${ACTION_STYLES[trade.trade_action]}`}>
                {trade.trade_action}
              </span>
              <span className="ml-auto text-[10px] text-muted shrink-0">
                {new Date(trade.created_at).toLocaleTimeString()}
              </span>
            </div>

            {/* Headline — truncated to 2 lines */}
            <p className="text-[11px] text-primary/70 line-clamp-2 leading-relaxed">
              {trade.headline}
            </p>

            {/* Score row */}
            <div className="flex gap-4 mt-1.5">
              <span className="text-[10px] text-muted">
                sent{" "}
                <span className={trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}>
                  {trade.sentiment_score >= 0 ? "+" : ""}
                  {trade.sentiment_score.toFixed(2)}
                </span>
              </span>
              <span className="text-[10px] text-muted">
                conf <span className="text-accent">{(trade.confidence_score * 100).toFixed(0)}%</span>
              </span>
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
