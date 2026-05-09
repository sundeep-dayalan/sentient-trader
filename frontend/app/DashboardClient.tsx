"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AgentMonologue  from "@/components/AgentMonologue";
import CustomNewsForm  from "@/components/CustomNewsForm";
import LiveTicker      from "@/components/LiveTicker";
import PnLChart        from "@/components/PnLChart";
import StatsBar        from "@/components/StatsBar";
import SystemStatus    from "@/components/SystemStatus";
import ThemeToggle     from "@/components/ThemeToggle";
import { BASE_PATH }   from "@/lib/config";
import { createClient } from "@/lib/supabase";
import { Trade }       from "@/lib/types";

const PAGE_SIZE = 20;
const STACK = ["LangGraph", "Groq Qwen3-32B", "Alpaca", "Redis Streams", "Supabase", "Fly.io"];

interface DashboardClientProps {
  initialTrades: Trade[];
}

export default function DashboardClient({ initialTrades }: DashboardClientProps) {
  const [trades,        setTrades]        = useState<Trade[]>(initialTrades);
  const [newIds,        setNewIds]        = useState<Set<string>>(new Set());
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(initialTrades[0] ?? null);
  const [hasMore,       setHasMore]       = useState(initialTrades.length === PAGE_SIZE);
  const [isLoadingMore, setIsLoadingMore] = useState(false);

  // Keep a stable ref to the latest trades tail so loadMore never goes stale
  // in the IntersectionObserver callback without needing to re-register it.
  const tailRef = useRef<string | null>(
    initialTrades.length > 0 ? initialTrades[initialTrades.length - 1].created_at : null
  );
  const hasMoreRef     = useRef(hasMore);
  const loadingMoreRef = useRef(false);

  useEffect(() => { hasMoreRef.current = hasMore; }, [hasMore]);

  // ── Supabase Realtime ─────────────────────────────────────────────────────
  useEffect(() => {
    const supabase = createClient();
    const channel  = supabase
      .channel("trades-realtime")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "trades" }, payload => {
        const t = payload.new as Trade;
        setTrades(prev => [t, ...prev]);
        setNewIds(prev => new Set(prev).add(t.id));
        setTimeout(() => setNewIds(prev => { const n = new Set(prev); n.delete(t.id); return n; }), 500);
      })
      .subscribe();

    return () => { supabase.removeChannel(channel); };
  }, []);

  // ── Pagination ────────────────────────────────────────────────────────────
  // Stable function: reads from refs so the IntersectionObserver never needs
  // to be torn down and re-created when trades or hasMore change.
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current || !tailRef.current) return;

    loadingMoreRef.current = true;
    setIsLoadingMore(true);

    try {
      const res = await fetch(
        `${BASE_PATH}/api/trades?before=${encodeURIComponent(tailRef.current)}`
      );
      if (!res.ok) return;

      const { trades: more, hasMore: next } = await res.json() as {
        trades: Trade[];
        hasMore: boolean;
      };

      if (more.length > 0) {
        tailRef.current = more[more.length - 1].created_at;
        setTrades(prev => [...prev, ...more]);
      }
      setHasMore(next);
      hasMoreRef.current = next;
    } finally {
      loadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []); // intentionally empty — uses refs for all mutable state

  return (
    <div className="min-h-screen bg-background">

      {/* ── Top navigation bar ─────────────────────────────────────────── */}
      <header className="sticky top-0 z-50 h-14 border-b border-line bg-surface/80 backdrop-blur-xl">
        <div className="max-w-[1400px] mx-auto px-4 md:px-6 h-full flex items-center gap-4">

          <div className="flex items-center gap-2.5 shrink-0">
            <div className="w-7 h-7 rounded-lg bg-accent-soft border border-accent-border flex items-center justify-center">
              <span className="text-accent text-[11px] font-bold font-mono">ST</span>
            </div>
            <span className="font-semibold text-sm">
              Sentient<span className="text-accent">Trader</span>
            </span>
          </div>

          <div className="w-px h-4 bg-line" />

          <div className="flex items-center gap-1.5">
            <span className="pulse-dot w-1.5 h-1.5 rounded-full bg-positive" />
            <span className="text-[11px] font-semibold text-positive tracking-wider">LIVE</span>
          </div>

          <div className="ml-auto flex items-center gap-3">
            <SystemStatus />
            <ThemeToggle />
          </div>
        </div>
      </header>

      {/* ── Page content ───────────────────────────────────────────────── */}
      <main className="max-w-[1400px] mx-auto px-4 md:px-6 py-6 space-y-5">

        <StatsBar trades={trades} />

        <div className="grid grid-cols-1 lg:grid-cols-[400px_1fr] gap-4">

          <LiveTicker
            trades={trades}
            newIds={newIds}
            onTradeSelect={setSelectedTrade}
            selectedId={selectedTrade?.id ?? null}
            onLoadMore={loadMore}
            isLoadingMore={isLoadingMore}
            hasMore={hasMore}
          />

          <div className="flex flex-col gap-4">
            <AgentMonologue trade={selectedTrade} />
            <PnLChart />
            <CustomNewsForm />
          </div>
        </div>
      </main>

      <footer className="max-w-[1400px] mx-auto px-4 md:px-6 py-5 border-t border-line">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted font-medium mr-1">Stack</span>
          {STACK.map(s => (
            <span key={s} className="text-[11px] text-muted px-2 py-0.5 bg-surface-2 border border-line rounded-md">
              {s}
            </span>
          ))}
        </div>
      </footer>
    </div>
  );
}
