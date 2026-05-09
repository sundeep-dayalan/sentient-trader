"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AgentMonologue  from "@/components/AgentMonologue";
import CustomNewsForm  from "@/components/CustomNewsForm";
import LiveTicker      from "@/components/LiveTicker";
import OrdersPage      from "@/components/OrdersPage";
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

const NAV_ICONS: Record<string, React.ReactNode> = {
  Dashboard: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  ),
  Signals: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  "Risk Gate": (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  ),
  Orders: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="8" y1="6" x2="21" y2="6" /><line x1="8" y1="12" x2="21" y2="12" /><line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" /><line x1="3" y1="12" x2="3.01" y2="12" /><line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  ),
  Portfolio: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <line x1="18" y1="20" x2="18" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  ),
  Pipeline: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="18" cy="18" r="3" /><circle cx="6" cy="6" r="3" /><path d="M6 21V9a9 9 0 0 0 9 9" />
    </svg>
  ),
};

export default function DashboardClient({ initialTrades }: DashboardClientProps) {
  const [trades,        setTrades]        = useState<Trade[]>(initialTrades);
  const [newIds,        setNewIds]        = useState<Set<string>>(new Set());
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(initialTrades[0] ?? null);
  const [hasMore,       setHasMore]       = useState(initialTrades.length === PAGE_SIZE);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [isSimulatorOpen, setIsSimulatorOpen] = useState(false);
  const [activeView,    setActiveView]    = useState<ViewName>("Dashboard");

  const tailRef        = useRef<string | null>(
    initialTrades.length > 0 ? initialTrades[initialTrades.length - 1].created_at : null
  );
  const hasMoreRef     = useRef(hasMore);
  const loadingMoreRef = useRef(false);

  useEffect(() => { hasMoreRef.current = hasMore; }, [hasMore]);

  useEffect(() => {
    if (!isSimulatorOpen) return;
    const closeOnEscape = (e: KeyboardEvent) => { if (e.key === "Escape") setIsSimulatorOpen(false); };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isSimulatorOpen]);

  const latestTrade = trades[0] ?? null;
  const lastSignalTime = latestTrade
    ? new Date(latestTrade.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : "Awaiting";

  const viewSubtitle: Partial<Record<ViewName, string>> = {
    Dashboard: "Live AI trading activity overview",
    Signals: "Real-time signal history and decision trace",
    Orders: "Alpaca order execution and account context",
  };

  // ── Supabase Realtime ─────────────────────────────────────────
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

  // ── Pagination ────────────────────────────────────────────────
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current || !tailRef.current) return;
    loadingMoreRef.current = true;
    setIsLoadingMore(true);
    try {
      const res = await fetch(`${BASE_PATH}/api/trades?before=${encodeURIComponent(tailRef.current)}`);
      if (!res.ok) return;
      const { trades: more, hasMore: next } = await res.json() as { trades: Trade[]; hasMore: boolean };
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
  }, []);

  return (
    <div className="min-h-screen bg-background text-primary xl:h-screen xl:overflow-hidden">
      <div className="flex min-h-screen xl:h-screen">

        {/* ── Sidebar ──────────────────────────────────────────────── */}
        <aside className="hidden w-[240px] shrink-0 flex-col border-r border-line bg-surface px-5 py-6 xl:flex">

          {/* Brand */}
          <div className="flex items-center gap-3 px-1">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white shadow-sm">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </div>
            <div>
              <p className="text-sm font-bold leading-none text-primary">Sentient Trader</p>
              <p className="mt-0.5 text-[11px] font-medium text-muted">AI Market Intelligence</p>
            </div>
          </div>

          {/* Portfolio card */}
          <div className="mt-6">
            <PortfolioMiniCard />
          </div>

          {/* Navigation */}
          <nav className="mt-6 space-y-0.5">
            <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-widest text-muted">Navigation</p>
            {NAV_ITEMS.map(item => (
              <button
                key={item}
                onClick={() => setActiveView(item)}
                className={[
                  "flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm font-medium transition-all duration-150",
                  activeView === item
                    ? "bg-accent-soft text-accent"
                    : "text-secondary hover:bg-hover hover:text-primary",
                ].join(" ")}
              >
                <span className={activeView === item ? "text-accent" : "text-muted"}>
                  {NAV_ICONS[item]}
                </span>
                {item}
              </button>
            ))}
          </nav>

          {/* Status dot at bottom */}
          <div className="mt-auto pt-4">
            <div className="flex items-center gap-2 rounded-xl border border-positive-border bg-positive-soft px-3 py-2.5">
              <span className="pulse-dot h-2 w-2 shrink-0 rounded-full bg-positive" />
              <div className="min-w-0">
                <p className="text-xs font-semibold text-positive">Autonomous</p>
                <p className="mt-0.5 text-[10px] text-positive opacity-70">Last signal {lastSignalTime}</p>
              </div>
            </div>
          </div>
        </aside>

        {/* ── Main area ─────────────────────────────────────────────── */}
        <div className="flex min-w-0 flex-1 flex-col xl:h-screen">

          {/* Header */}
          <header className="z-50 shrink-0 border-b border-line bg-surface">
            <div className="flex min-h-[60px] items-center gap-3 px-4 py-3 md:px-6">

              {/* Mobile brand */}
              <div className="flex min-w-0 items-center gap-3 xl:hidden">
                <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-accent text-white">
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                  </svg>
                </div>
                <p className="text-sm font-bold text-primary">Sentient Trader</p>
              </div>

              {/* Search */}
              <div className="hidden min-w-[260px] max-w-sm flex-1 items-center gap-2.5 rounded-xl border border-line bg-surface-2 px-3.5 py-2 md:flex">
                <svg className="h-4 w-4 shrink-0 text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35M10.5 18a7.5 7.5 0 1 1 0-15 7.5 7.5 0 0 1 0 15z" />
                </svg>
                <input
                  aria-label="Search signals"
                  className="min-w-0 flex-1 bg-transparent text-sm text-secondary outline-none placeholder:text-muted"
                  placeholder="Search signals, tickers…"
                />
              </div>

              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={() => setIsSimulatorOpen(true)}
                  aria-label="Open simulator"
                  className="flex h-9 items-center gap-2 rounded-xl bg-accent px-4 text-[13px] font-semibold text-white shadow-sm transition-all duration-150 hover:opacity-90 active:scale-95"
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

          {/* Page content */}
          <main className="mx-auto flex w-full max-w-[1540px] flex-1 flex-col gap-4 overflow-y-auto px-4 py-5 md:px-6 xl:min-h-0 xl:overflow-hidden">

            {/* Page header strip */}
            <section className="flex shrink-0 flex-wrap items-center justify-between gap-3">
              <div>
                <h1 className="text-xl font-bold leading-tight text-primary">{activeView}</h1>
                <p className="mt-0.5 text-sm text-muted">
                  {viewSubtitle[activeView] ?? "View coming online."}
                </p>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-full border border-positive-border bg-positive-soft px-3 py-1 text-xs font-semibold text-positive">
                  <span className="h-1.5 w-1.5 rounded-full bg-positive" />
                  Autonomous online
                </span>
                <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-xs font-medium text-secondary">
                  Last signal {lastSignalTime}
                </span>
              </div>
            </section>

            {/* Dashboard view */}
            {activeView === "Dashboard" && (
              <>
                <div className="shrink-0">
                  <StatsBar trades={trades} />
                </div>
                <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
                  <LiveTicker
                    trades={trades}
                    newIds={newIds}
                    onTradeSelect={setSelectedTrade}
                    selectedId={selectedTrade?.id ?? null}
                    onLoadMore={loadMore}
                    isLoadingMore={isLoadingMore}
                    hasMore={hasMore}
                    previewLimit={5}
                  />
                  <div className="grid min-h-0 min-w-0 gap-4 xl:grid-rows-[minmax(0,1fr)_240px]">
                    <AgentMonologue trade={selectedTrade} />
                    <PnLChart />
                  </div>
                </div>
              </>
            )}

            {/* Signals view */}
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

            {activeView === "Orders" && <OrdersPage />}

            {activeView !== "Dashboard" && activeView !== "Signals" && activeView !== "Orders" && (
              <div className="glass-panel flex min-h-0 flex-1 items-center justify-center rounded-2xl p-10 text-center">
                <div>
                  <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-2 text-muted">
                    {NAV_ICONS[activeView]}
                  </div>
                  <p className="text-base font-semibold text-primary">{activeView}</p>
                  <p className="mt-1.5 text-sm text-muted">This workspace is coming soon.</p>
                </div>
              </div>
            )}
          </main>
        </div>
      </div>

      {/* ── Simulate modal ────────────────────────────────────────── */}
      {isSimulatorOpen && (
        <div
          className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center px-4 py-6 backdrop-blur-sm"
          role="presentation"
          onMouseDown={() => setIsSimulatorOpen(false)}
        >
          <div
            className="glass-panel w-full max-w-md rounded-2xl p-5"
            role="dialog"
            aria-modal="true"
            aria-label="Simulate signal"
            onMouseDown={e => e.stopPropagation()}
          >
            <div className="mb-5 flex items-center justify-between gap-3">
              <div>
                <p className="text-base font-bold text-primary">Simulate Signal</p>
                <p className="mt-0.5 text-sm text-muted">Manually test a market headline</p>
              </div>
              <button
                onClick={() => setIsSimulatorOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-xl border border-line bg-surface-2 text-muted transition-all duration-150 hover:border-accent-border hover:text-accent"
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
