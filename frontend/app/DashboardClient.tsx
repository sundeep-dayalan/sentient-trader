"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import AgentMonologue  from "@/components/AgentMonologue";
import CustomNewsForm  from "@/components/CustomNewsForm";
import LiveTicker      from "@/components/LiveTicker";
import OrdersPage      from "@/components/OrdersPage";
import PnLChart        from "@/components/PnLChart";
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

interface AlpacaAccount {
  status?: string;
  currency?: string;
  equity?: string;
  last_equity?: string;
  portfolio_value?: string;
  buying_power?: string;
  cash?: string;
  long_market_value?: string;
  short_market_value?: string;
}

interface AlpacaPosition {
  symbol?: string;
  qty?: string;
  market_value?: string;
}

interface AlpacaOrder {
  id: string;
  symbol?: string;
  side?: string;
  type?: string;
  order_type?: string;
  qty?: string;
  notional?: string;
  filled_qty?: string;
  filled_avg_price?: string;
  limit_price?: string | null;
  status?: string;
  created_at?: string;
  submitted_at?: string;
  filled_at?: string | null;
  updated_at?: string;
}

interface OrdersResponse {
  account: AlpacaAccount | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  fetchedAt: string;
  error?: string;
}

const EMPTY_ALPACA_DATA: OrdersResponse = {
  account: null,
  positions: [],
  orders: [],
  fetchedAt: new Date().toISOString(),
};

const SIGNAL_STYLE: Record<Trade["trade_action"], string> = {
  BUY:  "border-positive-border bg-positive-soft text-positive",
  SELL: "border-negative-border bg-negative-soft text-negative",
  HOLD: "border-line bg-surface-2 text-muted",
};

const ORDER_STATUS_STYLE: Record<string, string> = {
  open:     "border-cyan-border bg-cyan-soft text-cyan",
  filled:   "border-positive-border bg-positive-soft text-positive",
  canceled: "border-warning-border bg-warning-soft text-warning",
  rejected: "border-negative-border bg-negative-soft text-negative",
  other:    "border-line bg-surface-2 text-muted",
};

const ORDER_DOT_STYLE: Record<string, string> = {
  buy:  "border-positive-border bg-positive-soft text-positive",
  sell: "border-negative-border bg-negative-soft text-negative",
};

function numberValue(value?: string | number | null) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function money(value?: string | number | null, maximumFractionDigits = 2) {
  const number = numberValue(value);
  if (number === null) return "—";
  return number.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits,
  });
}

function percent(value: number | null) {
  if (value === null || !Number.isFinite(value)) return "0.00%";
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`;
}

function timeLabel(value?: string | null) {
  if (!value) return "—";
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function orderBucket(status?: string) {
  const normalized = status?.toLowerCase() ?? "other";
  if (["new", "accepted", "accepted_for_bidding", "pending_new", "partially_filled", "pending_cancel", "pending_replace", "stopped"].includes(normalized)) return "open";
  if (normalized === "filled") return "filled";
  if (["canceled", "expired", "done_for_day"].includes(normalized)) return "canceled";
  if (["rejected", "failed", "suspended"].includes(normalized)) return "rejected";
  return "other";
}

function orderQuantity(order: AlpacaOrder) {
  if (order.notional) return money(order.notional);
  if (order.qty) return `${Number(order.qty).toLocaleString("en-US")} sh`;
  return "—";
}

function orderPrice(order: AlpacaOrder) {
  if (order.filled_avg_price) return money(order.filled_avg_price);
  if (order.limit_price) return `LMT ${money(order.limit_price)}`;
  return "Market";
}

function ChangeArrow({ up }: { up: boolean }) {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path d={up ? "M10 3.5 16.5 15h-13L10 3.5z" : "M10 16.5 3.5 5h13L10 16.5z"} />
    </svg>
  );
}

function EmptyDots() {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center gap-1.5">
      {[0, 1, 2].map(i => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
          style={{ animationDelay: `${i * 120}ms` }}
        />
      ))}
    </div>
  );
}

function RecentSignalsCard({
  trades,
  onSeeMore,
}: {
  trades: Trade[];
  onSeeMore: () => void;
}) {
  const visibleTrades = trades.slice(0, 3);

  return (
    <section className="rounded-[28px] border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-4 shadow-[var(--dashboard-shadow)] 2xl:p-6">
      <div className="mb-4 flex items-center justify-between gap-3 2xl:mb-5">
        <h2 className="text-2xl font-bold leading-none text-[var(--dashboard-text)] 2xl:text-[28px]">Recent Signals</h2>
        <button
          onClick={onSeeMore}
          className="inline-flex shrink-0 items-center gap-2 rounded-full px-1 py-1 text-sm font-medium text-[var(--dashboard-link)] transition hover:text-accent"
        >
          <span className="hidden min-[1440px]:inline">See more</span>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--dashboard-control)] text-lg leading-none">›</span>
        </button>
      </div>

      <div className="space-y-1">
        {visibleTrades.length === 0 && (
          <div className="flex min-h-[190px] items-center justify-center text-sm text-[var(--dashboard-muted)]">
            Waiting for market signals.
          </div>
        )}

        {visibleTrades.map((trade, index) => (
          <div
            key={trade.id}
            className={[
              "grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 py-3.5 2xl:grid-cols-[28px_minmax(0,1fr)_auto] 2xl:gap-3 2xl:py-4",
              index > 0 ? "border-t border-[var(--dashboard-divider)]" : "",
            ].join(" ")}
          >
            <div className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-border bg-cyan-soft text-cyan 2xl:h-7 2xl:w-7">
              <svg className="h-3 w-3 2xl:h-3.5 2xl:w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <p className="truncate text-sm font-semibold text-[var(--dashboard-text)] 2xl:text-base">{trade.ticker}</p>
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${SIGNAL_STYLE[trade.trade_action]}`}>
                  {trade.trade_action}
                </span>
              </div>
              <p className="mt-1 line-clamp-1 text-xs text-[var(--dashboard-subtle)]">{trade.headline}</p>
            </div>
            <div className="text-right">
              <p className="font-mono text-sm font-semibold text-positive">
                {(trade.confidence_score * 100).toFixed(0)}%
              </p>
              <p className="mt-1 text-[11px] text-[var(--dashboard-muted)]">{timeLabel(trade.created_at)}</p>
            </div>
          </div>
        ))}
      </div>

    </section>
  );
}

function RecentOrdersCard({
  orders,
  loading,
  error,
  onSeeMore,
}: {
  orders: AlpacaOrder[];
  loading: boolean;
  error?: string;
  onSeeMore: () => void;
}) {
  const visibleOrders = orders.slice(0, 3);

  return (
    <section className="rounded-[28px] border border-[var(--dashboard-border)] bg-[var(--dashboard-card-muted)] p-4 shadow-[var(--dashboard-shadow)] 2xl:p-6">
      <div className="mb-4 flex items-center justify-between gap-3 2xl:mb-5">
        <h2 className="text-2xl font-bold leading-none text-[var(--dashboard-text)] 2xl:text-[28px]">Transactions</h2>
        <button
          onClick={onSeeMore}
          className="inline-flex shrink-0 items-center gap-2 rounded-full px-1 py-1 text-sm font-medium text-[var(--dashboard-link)] transition hover:text-accent"
        >
          <span className="hidden min-[1440px]:inline">See more</span>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--dashboard-control)] text-lg leading-none">›</span>
        </button>
      </div>

      <div className="space-y-1">
        {loading && visibleOrders.length === 0 && <EmptyDots />}
        {!loading && error && (
          <div className="flex min-h-[210px] items-center justify-center rounded-2xl border border-negative-border bg-negative-soft px-4 text-center text-sm text-negative">
            {error}
          </div>
        )}
        {!loading && !error && visibleOrders.length === 0 && (
          <div className="flex min-h-[210px] items-center justify-center text-sm text-[var(--dashboard-muted)]">
            No Alpaca orders yet.
          </div>
        )}

        {visibleOrders.map((order, index) => {
          const side = order.side?.toLowerCase() === "sell" ? "sell" : "buy";
          const bucket = orderBucket(order.status);
          return (
            <div
              key={order.id}
              className={[
                "grid grid-cols-[34px_minmax(0,1fr)_auto] items-center gap-3 py-3 2xl:grid-cols-[42px_minmax(0,1fr)_auto] 2xl:gap-4 2xl:py-3.5",
                index > 0 ? "border-t border-[var(--dashboard-divider)]" : "",
              ].join(" ")}
            >
              <div className={`flex h-9 w-9 items-center justify-center rounded-full border 2xl:h-11 2xl:w-11 ${ORDER_DOT_STYLE[side]}`}>
                <span className="font-mono text-xs font-bold 2xl:text-sm">{order.symbol?.slice(0, 1) ?? "?"}</span>
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-semibold text-[var(--dashboard-text)] 2xl:text-base">{order.symbol ?? "Unknown"}</p>
                  <span className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${ORDER_STATUS_STYLE[bucket]}`}>
                    {order.status ?? "unknown"}
                  </span>
                </div>
                <p className="mt-1 text-xs capitalize text-[var(--dashboard-subtle)] 2xl:text-sm">
                  {order.side ?? "order"} · {order.type ?? order.order_type ?? "market"}
                </p>
              </div>
              <div className="text-right">
                <p className={side === "buy" ? "font-mono text-sm font-semibold text-positive" : "font-mono text-sm font-semibold text-negative"}>
                  {side === "buy" ? "+" : "-"}{orderQuantity(order)}
                </p>
                <p className="mt-1 text-[11px] text-[var(--dashboard-muted)]">
                  {orderPrice(order)} · {timeLabel(order.submitted_at ?? order.created_at)}
                </p>
              </div>
            </div>
          );
        })}
      </div>

    </section>
  );
}

function AlpacaBalanceCard({
  account,
  positions,
  orders,
  loading,
  error,
}: {
  account: AlpacaAccount | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  loading: boolean;
  error?: string;
}) {
  const accountValue = account?.portfolio_value ?? account?.equity;
  const equity = numberValue(accountValue);
  const lastEquity = numberValue(account?.last_equity);
  const dailyChange = equity !== null && lastEquity !== null ? equity - lastEquity : 0;
  const dailyChangePct = lastEquity ? dailyChange / lastEquity * 100 : 0;
  const isUp = dailyChange >= 0;
  const openOrders = orders.filter(order => orderBucket(order.status) === "open").length;
  const filledOrders = orders.filter(order => orderBucket(order.status) === "filled").length;

  return (
    <section className="rounded-[30px] border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-6 shadow-[var(--dashboard-shadow)]">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-[var(--dashboard-subtle)]">Alpaca Balance</p>
          <p className="mt-4 font-sans text-[30px] font-bold leading-none tracking-tight text-[var(--dashboard-text)]">
            {loading && !account ? "—" : money(accountValue)}
          </p>
        </div>
        <span className="flex h-8 w-8 items-center justify-center rounded-full bg-[var(--dashboard-control)] text-[var(--dashboard-subtle)]">
          <svg className="h-4 w-4" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <circle cx="5" cy="12" r="1.8" />
            <circle cx="12" cy="12" r="1.8" />
            <circle cx="19" cy="12" r="1.8" />
          </svg>
        </span>
      </div>

      <div className={`mt-4 inline-flex items-center gap-2 font-mono text-sm font-semibold ${isUp ? "text-positive" : "text-negative"}`}>
        <ChangeArrow up={isUp} />
        {money(Math.abs(dailyChange))} {percent(dailyChangePct)}
      </div>

      <div className="mt-6 border-t border-[var(--dashboard-divider)] pt-5">
        <div className="mb-4 flex items-center justify-between gap-3">
          <h3 className="text-xl font-semibold text-[var(--dashboard-text)]">Alpaca Insights</h3>
          <span className="rounded-full bg-[var(--dashboard-control)] px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-[var(--dashboard-subtle)]">
            {account?.status ?? "paper"}
          </span>
        </div>

        {error && (
          <p className="mb-4 rounded-2xl border border-negative-border bg-negative-soft px-3 py-2 text-xs text-negative">
            {error}
          </p>
        )}

        <div className="grid grid-cols-2 gap-2.5">
          {[
            { label: "Buying power", value: money(account?.buying_power) },
            { label: "Cash", value: money(account?.cash) },
            { label: "Open positions", value: String(positions.length) },
            { label: "Open orders", value: String(openOrders) },
            { label: "Filled orders", value: String(filledOrders) },
          ].map(item => (
            <div
              key={item.label}
              className="rounded-2xl bg-[var(--dashboard-row)] px-4 py-3"
            >
              <span className="block text-xs font-medium text-[var(--dashboard-subtle)]">{item.label}</span>
              <span className="mt-1 block truncate font-mono text-[15px] font-semibold text-[var(--dashboard-text)]">
                {loading && !account ? "—" : item.value}
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function EmptyAiTipsCard() {
  return (
    <section>
      <h2 className="mb-5 text-[30px] font-bold leading-none text-[var(--dashboard-text)]">AI Tips</h2>
      <div className="min-h-[190px] rounded-[28px] border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] shadow-[var(--dashboard-shadow)]" />
    </section>
  );
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
  const [alpacaData,    setAlpacaData]    = useState<OrdersResponse>(EMPTY_ALPACA_DATA);
  const [alpacaLoading, setAlpacaLoading] = useState(true);

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

  const loadAlpacaSummary = useCallback(async () => {
    try {
      const response = await fetch(`${BASE_PATH}/api/orders?status=all&limit=8`);
      const json = await response.json() as OrdersResponse;
      setAlpacaData(json);
    } catch {
      setAlpacaData(current => ({
        ...current,
        error: "Could not load Alpaca summary",
      }));
    } finally {
      setAlpacaLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAlpacaSummary();
    const interval = setInterval(loadAlpacaSummary, 30_000);
    return () => clearInterval(interval);
  }, [loadAlpacaSummary]);

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
          <main
            className={[
              "mx-auto flex w-full max-w-[1660px] flex-1 flex-col overflow-y-auto bg-[var(--dashboard-bg)] px-4 py-5 md:px-6 xl:min-h-0",
              activeView === "Dashboard"
                ? "xl:overflow-y-auto"
                : "gap-4 xl:overflow-y-auto",
            ].join(" ")}
          >

            {/* Page header strip */}
            {activeView !== "Dashboard" && (
            <section className="glass-panel flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-[28px] px-5 py-4">
              <div>
                <h1 className="text-2xl font-bold leading-tight text-[var(--dashboard-text)]">{activeView}</h1>
                <p className="mt-1 text-sm text-[var(--dashboard-subtle)]">
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
            )}

            {/* Dashboard view */}
            {activeView === "Dashboard" && (
              <div className="w-full space-y-6">
                <StatsBar trades={trades} />
                <div className="grid gap-6 xl:grid-cols-[minmax(0,1fr)_420px] 2xl:grid-cols-[minmax(0,1fr)_440px]">
                  <div className="min-w-0 space-y-6">
                    <PnLChart />
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:gap-6">
                      <RecentSignalsCard
                        trades={trades}
                        onSeeMore={() => setActiveView("Signals")}
                      />
                      <RecentOrdersCard
                        orders={alpacaData.orders}
                        loading={alpacaLoading}
                        error={alpacaData.error}
                        onSeeMore={() => setActiveView("Orders")}
                      />
                    </div>
                  </div>
                  <aside className="space-y-7">
                    <AlpacaBalanceCard
                      account={alpacaData.account}
                      positions={alpacaData.positions}
                      orders={alpacaData.orders}
                      loading={alpacaLoading}
                      error={alpacaData.error}
                    />
                    <EmptyAiTipsCard />
                  </aside>
                </div>
              </div>
            )}

            {/* Signals view */}
            {activeView === "Signals" && (
              <div className="grid min-h-[640px] flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(420px,0.9fr)_minmax(0,1.1fr)]">
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
              <div className="glass-panel flex min-h-[520px] flex-1 items-center justify-center rounded-[28px] p-10 text-center">
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
