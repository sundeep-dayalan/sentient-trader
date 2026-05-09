"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AgentMonologue  from "@/components/AgentMonologue";
import CustomNewsForm  from "@/components/CustomNewsForm";
import LiveTicker      from "@/components/LiveTicker";
import PnLChart        from "@/components/PnLChart";
import PortfolioMiniCard from "@/components/PortfolioMiniCard";
import StatsBar        from "@/components/StatsBar";
import SystemStatus    from "@/components/SystemStatus";
import ThemeToggle     from "@/components/ThemeToggle";
import { BASE_PATH }   from "@/lib/config";
import { createClient } from "@/lib/supabase";
import { Trade }       from "@/lib/types";

const PAGE_SIZE = 20;
const NAV_ITEMS = ["Dashboard", "Signals", "Risk Gate", "Orders", "Portfolio", "Pipeline"] as const;
type ViewName = typeof NAV_ITEMS[number];

interface DashboardClientProps {
  initialTrades: Trade[];
}

export default function DashboardClient({ initialTrades }: DashboardClientProps) {
  const [trades,        setTrades]        = useState<Trade[]>(initialTrades);
  const [newIds,        setNewIds]        = useState<Set<string>>(new Set());
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(initialTrades[0] ?? null);
  const [hasMore,       setHasMore]       = useState(initialTrades.length === PAGE_SIZE);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSimulatorOpen, setIsSimulatorOpen] = useState(false);
  const [activeView,    setActiveView]    = useState<ViewName>("Dashboard");

  // Keep a stable ref to the latest trades tail so loadMore never goes stale
  // in the IntersectionObserver callback without needing to re-register it.
  const tailRef = useRef<string | null>(
    initialTrades.length > 0 ? initialTrades[initialTrades.length - 1].created_at : null
  );
  const hasMoreRef     = useRef(hasMore);
  const loadingMoreRef = useRef(false);

  useEffect(() => { hasMoreRef.current = hasMore; }, [hasMore]);

  useEffect(() => {
    if (!isSimulatorOpen) return;

    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsSimulatorOpen(false);
    };

    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isSimulatorOpen]);

  const latestTrade = trades[0] ?? null;
  const lastSignalTime = latestTrade
    ? new Date(latestTrade.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "Awaiting";
  const viewSubtitle: Partial<Record<ViewName, string>> = {
    Dashboard: "Overview of live AI trading activity.",
    Signals: "Detailed realtime signal history and decision trace.",
  };

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
    <div className="min-h-screen bg-background text-primary xl:h-screen xl:overflow-hidden">
      <div className="flex min-h-screen xl:h-screen">
        <aside className="hidden w-[236px] shrink-0 flex-col border-r border-line bg-surface px-4 py-5 xl:flex">
          <div className="flex items-center gap-3 px-2">
            <div className="relative flex h-9 w-9 items-center justify-center rounded-lg border border-accent-border bg-accent-soft shadow-glow">
              <span className="font-mono text-[12px] font-bold text-accent">ST</span>
              <span className="absolute -right-1 -top-1 h-2.5 w-2.5 rounded-full border border-background bg-positive" />
            </div>
            <div>
              <p className="text-sm font-semibold leading-none">Sentient Trader</p>
              <p className="mt-1 text-[11px] text-muted">AI market terminal</p>
            </div>
          </div>

          <div className="mt-5">
            <PortfolioMiniCard />
          </div>

          <nav className="mt-6 space-y-1">
            {NAV_ITEMS.map((item, index) => (
              <button
                key={item}
                onClick={() => setActiveView(item)}
                className={[
                  "flex w-full items-center gap-3 rounded-lg border px-3 py-2.5 text-left text-sm transition-all duration-200",
                  activeView === item
                    ? "border-accent-border bg-accent-soft text-primary shadow-glow"
                    : "border-transparent text-muted hover:border-line hover:bg-hover hover:text-secondary",
                ].join(" ")}
              >
                <span className={[
                  "h-1.5 w-1.5 rounded-full",
                  activeView === item ? "bg-accent" : "bg-muted opacity-50",
                ].join(" ")}
                />
                <span className="min-w-0 flex-1 truncate">{item}</span>
                {activeView === item && (
                  <span className="font-mono text-[10px] text-accent">
                    {String(index + 1).padStart(2, "0")}
                  </span>
                )}
              </button>
            ))}
          </nav>

        </aside>

        <div className="flex min-w-0 flex-1 flex-col xl:h-screen">
          <header className="z-50 shrink-0 border-b border-line bg-surface">
            <div className="flex min-h-16 items-center gap-3 px-4 py-3 md:px-6">
              <div className="flex min-w-0 items-center gap-3 xl:hidden">
                <div className="flex h-9 w-9 items-center justify-center rounded-lg border border-accent-border bg-accent-soft">
                  <span className="font-mono text-[12px] font-bold text-accent">ST</span>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-semibold">Sentient Trader</p>
                  <p className="text-[11px] text-muted">AI market terminal</p>
                </div>
              </div>

              <div className="hidden min-w-[260px] max-w-md flex-1 items-center gap-2 rounded-lg border border-line bg-surface-2 px-3 py-2 md:flex">
                <svg className="h-4 w-4 shrink-0 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15z" />
                </svg>
                <input
                  aria-label="Search signals"
                  className="min-w-0 flex-1 bg-transparent text-sm text-secondary outline-none placeholder:text-muted"
                  placeholder="Search signals, tickers, reasoning"
                />
              </div>

              <div className="ml-auto flex items-center gap-3">
                <button
                  onClick={() => setIsSimulatorOpen(true)}
                  aria-label="Open simulator"
                  className="cyber-button flex h-9 items-center justify-center gap-2 rounded-lg border border-accent bg-accent px-3 text-[12px] font-semibold text-white transition-all duration-200 hover:opacity-90"
                >
                  <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                    <path d="M8 5.14v14l11-7-11-7z" />
                  </svg>
                  <span className="hidden sm:inline">Simulate</span>
                </button>
                <SystemStatus />
                <ThemeToggle />
              </div>
            </div>
          </header>

          <main className="mx-auto flex w-full max-w-[1520px] flex-1 flex-col gap-4 overflow-y-auto px-4 py-4 md:px-6 xl:min-h-0 xl:overflow-hidden">
            <section className="glass-panel flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-lg px-4 py-3">
              <div>
                <h1 className="text-xl font-semibold leading-tight text-primary">{activeView}</h1>
                <p className="mt-0.5 text-[11px] text-muted">
                  {viewSubtitle[activeView] ?? "View coming online."}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded-md border border-positive-border bg-positive-soft px-2.5 py-1 text-[11px] font-semibold text-positive">
                  Autonomous online
                </span>
                <span className="rounded-md border border-cyan-border bg-cyan-soft px-2.5 py-1 text-[11px] font-semibold text-cyan">
                  Last signal {lastSignalTime}
                </span>
              </div>
            </section>

            {activeView === "Dashboard" && (
              <>
                <div className="shrink-0">
                  <StatsBar trades={trades} />
                </div>

                <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
                  <LiveTicker
                    trades={trades}
                    newIds={newIds}
                    onTradeSelect={setSelectedTrade}
                    selectedId={selectedTrade?.id ?? null}
                    onLoadMore={loadMore}
                    isLoadingMore={isLoadingMore}
                    hasMore={hasMore}
                  />

                  <div className="grid min-h-0 min-w-0 gap-4 xl:grid-rows-[minmax(0,1fr)_240px]">
                    <AgentMonologue trade={selectedTrade} />
                    <PnLChart />
                  </div>
                </div>
              </>
            )}

            {activeView === "Signals" && (
              <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(420px,0.9fr)_minmax(0,1.1fr)]">
                <LiveTicker
                  trades={trades}
                  newIds={newIds}
                  onTradeSelect={setSelectedTrade}
                  selectedId={selectedTrade?.id ?? null}
                  onLoadMore={loadMore}
                  isLoadingMore={isLoadingMore}
                  hasMore={hasMore}
                />
                <AgentMonologue trade={selectedTrade} />
              </div>
            )}

            {activeView !== "Dashboard" && activeView !== "Signals" && (
              <div className="glass-panel flex min-h-0 flex-1 items-center justify-center rounded-lg p-8 text-center">
                <div>
                  <p className="text-sm font-semibold text-secondary">{activeView}</p>
                  <p className="mt-1 text-xs text-muted">This workspace is ready for the next UI pass.</p>
                </div>
              </div>
            )}
          </main>
        </div>
      </div>

      {isSimulatorOpen && (
        <div
          className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center px-4 py-6 backdrop-blur-md"
          role="presentation"
          onMouseDown={() => setIsSimulatorOpen(false)}
        >
          <div
            className="glass-panel w-full max-w-md rounded-lg p-4 shadow-card-md"
            role="dialog"
            aria-modal="true"
            aria-label="Simulate signal"
            onMouseDown={event => event.stopPropagation()}
          >
            <div className="mb-4 flex items-center justify-between gap-3 border-b border-line pb-3">
              <div>
                <p className="text-sm font-semibold">Simulate Signal</p>
                <p className="mt-0.5 text-[11px] text-muted">Manual market headline test</p>
              </div>
              <button
                onClick={() => setIsSimulatorOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface-2 text-muted transition-all duration-200 hover:border-accent-border hover:text-primary"
                aria-label="Close simulator"
              >
                <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <CustomNewsForm variant="modal" />
          </div>
        </div>
      )}
    </div>
  );
}
