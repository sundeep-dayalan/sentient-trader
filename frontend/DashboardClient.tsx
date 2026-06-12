import { useCallback, useEffect, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import AgentMonologue from '@/components/AgentMonologue';
import AppErrorNotice from '@/components/AppErrorNotice';
import CalibrationPanel from '@/components/CalibrationPanel';
import AuthGate from '@/components/AuthGate';
import CustomNewsForm from '@/components/CustomNewsForm';
import LiveTicker from '@/components/LiveTicker';
import OrdersPage from '@/components/OrdersPage';
import PnLChart from '@/components/PnLChart';
import PortfolioPage from '@/components/PortfolioPage';
import StatsBar from '@/components/StatsBar';
import SystemStatus from '@/components/SystemStatus';
import ThemeToggle from '@/components/ThemeToggle';
import PipelinePage from '@/components/PipelinePage';
import SettingsPage from '@/components/SettingsPage';
import { Sidebar, SidebarBody, SidebarLink, useSidebar } from '@/components/ui/sidebar';
import { cn } from '@/lib/utils';
import { motion } from 'motion/react';
import { useAuth } from '@/components/AuthProvider';
import { ApiError, apiFetch } from '@/lib/api';
import { DashboardStats, SignalCounts, Trade } from '@/lib/types';
import { unescapeHtml } from '@/lib/news';
import { AppErrorCopy, formatAlpacaError } from '@/lib/errors';

const PAGE_SIZE = 20;
const NAV_ITEMS = [
  'Dashboard',
  'Signals',
  'Risk Gate',
  'Orders',
  'Portfolio',
  'Pipeline',
  'Settings',
] as const;
type ViewName = (typeof NAV_ITEMS)[number];

// The four views surfaced directly in the mobile bottom tab bar (thumb reach).
// Everything else lives behind the "More" sheet / hamburger drawer.
const BOTTOM_NAV_ITEMS: ViewName[] = ['Dashboard', 'Signals', 'Orders', 'Portfolio'];
// Shorter captions so four tabs + "More" fit a narrow phone without truncating.
const BOTTOM_NAV_LABELS: Partial<Record<ViewName, string>> = { Dashboard: 'Home' };

const VIEW_PATH_MAP: Record<ViewName, string> = {
  Dashboard: '/',
  Signals: '/signals',
  'Risk Gate': '/risk-gate',
  Orders: '/orders',
  Portfolio: '/portfolio',
  Pipeline: '/pipeline',
  Settings: '/settings',
};

const PATH_VIEW_MAP: Record<string, ViewName> = {
  '/': 'Dashboard',
  '/signals': 'Signals',
  '/risk-gate': 'Risk Gate',
  '/orders': 'Orders',
  '/portfolio': 'Portfolio',
  '/pipeline': 'Pipeline',
  '/settings': 'Settings',
};

interface DashboardClientProps {
  initialTrades: Trade[];
  initialStats: DashboardStats | null;
}

interface AlpacaAccount {
  status?: string;
  currency?: string;
  equity?: string;
  last_equity?: string;
  portfolio_value?: string;
  buying_power?: string;
  daytrading_buying_power?: string;
  regt_buying_power?: string;
  non_marginable_buying_power?: string;
  cash?: string;
  long_market_value?: string;
  short_market_value?: string;
  maintenance_margin?: string;
  last_maintenance_margin?: string;
  multiplier?: string;
  daytrade_count?: number;
  pattern_day_trader?: boolean;
  trading_blocked?: boolean;
  transfers_blocked?: boolean;
  account_blocked?: boolean;
  trade_suspended_by_user?: boolean;
  shorting_enabled?: boolean;
}

interface AlpacaPosition {
  symbol?: string;
  qty?: string;
  qty_available?: string;
  side?: string;
  asset_class?: string;
  exchange?: string;
  market_value?: string;
  cost_basis?: string;
  avg_entry_price?: string;
  current_price?: string;
  lastday_price?: string;
  change_today?: string;
  unrealized_pl?: string;
  unrealized_plpc?: string;
  unrealized_intraday_pl?: string;
  unrealized_intraday_plpc?: string;
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
  // Bracket/OCO child legs, nested under the parent when fetched with
  // nested=true. These keep working after the parent order fills.
  legs?: AlpacaOrder[] | null;
}

interface OrdersResponse {
  account: AlpacaAccount | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  fetchedAt: string;
  error?: AppErrorCopy;
}

interface OrdersApiResponse extends Omit<OrdersResponse, 'error'> {
  error?: string;
}

const EMPTY_ALPACA_DATA: OrdersResponse = {
  account: null,
  positions: [],
  orders: [],
  fetchedAt: new Date().toISOString(),
};

const SIGNAL_STYLE: Record<Trade['trade_action'], string> = {
  BUY: 'border-positive-border bg-positive-soft text-positive',
  SELL: 'border-negative-border bg-negative-soft text-negative',
  HOLD: 'border-line bg-surface-2 text-muted',
};

function recommendationFor(trade: Trade): Trade['trade_action'] {
  return trade.pm_recommendation ?? trade.trade_action;
}

function executionBadge(trade: Trade): string {
  if (trade.executed_action) return `${trade.executed_action} ORDER`;
  return `${recommendationFor(trade)} REC`;
}

function displayConfidence(trade: Trade): number {
  return trade.calibrated_confidence ?? trade.confidence_score;
}

const ORDER_STATUS_STYLE: Record<string, string> = {
  open: 'border-cyan-border bg-cyan-soft text-cyan',
  filled: 'border-positive-border bg-positive-soft text-positive',
  canceled: 'border-warning-border bg-warning-soft text-warning',
  rejected: 'border-negative-border bg-negative-soft text-negative',
  other: 'border-line bg-surface-2 text-muted',
};

const ORDER_DOT_STYLE: Record<string, string> = {
  buy: 'border-positive-border bg-positive-soft text-positive',
  sell: 'border-negative-border bg-negative-soft text-negative',
};

function numberValue(value?: string | number | null) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function money(value?: string | number | null, maximumFractionDigits = 2) {
  const number = numberValue(value);
  if (number === null) return '—';
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits,
  });
}

function percent(value: number | null) {
  if (value === null || !Number.isFinite(value)) return '0.00%';
  return `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`;
}

function ratioPercent(value?: string | number | null) {
  const number = numberValue(value);
  return number === null ? '—' : percent(number * 100);
}

function compactMoney(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '—';
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  });
}

function shareQuantity(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '—';
  return `${number.toLocaleString('en-US', { maximumFractionDigits: 4 })} sh`;
}

function timeLabel(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function orderBucket(status?: string) {
  const normalized = status?.toLowerCase() ?? 'other';
  if (
    [
      'new',
      'accepted',
      'accepted_for_bidding',
      'pending_new',
      'partially_filled',
      'pending_cancel',
      'pending_replace',
      'stopped',
    ].includes(normalized)
  )
    return 'open';
  if (normalized === 'filled') return 'filled';
  if (['canceled', 'expired', 'done_for_day'].includes(normalized)) return 'canceled';
  if (['rejected', 'failed', 'suspended'].includes(normalized)) return 'rejected';
  return 'other';
}

function orderQuantity(order: AlpacaOrder) {
  if (order.notional) return money(order.notional);
  if (order.qty) return `${Number(order.qty).toLocaleString('en-US')} sh`;
  return '—';
}

function orderPrice(order: AlpacaOrder) {
  if (order.filled_avg_price) return money(order.filled_avg_price);
  if (order.limit_price) return `LMT ${money(order.limit_price)}`;
  return 'Market';
}

function hasTracePayload(trade: Trade): boolean {
  return Object.prototype.hasOwnProperty.call(trade, 'decision_trace');
}

function ChangeArrow({ up }: { up: boolean }) {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 20 20" fill="currentColor" aria-hidden="true">
      <path d={up ? 'M10 3.5 16.5 15h-13L10 3.5z' : 'M10 16.5 3.5 5h13L10 16.5z'} />
    </svg>
  );
}

function EmptyDots() {
  return (
    <div className="flex h-full min-h-[120px] items-center justify-center gap-1.5">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="h-1.5 w-1.5 animate-bounce rounded-full bg-muted"
          style={{ animationDelay: `${i * 120}ms` }}
        />
      ))}
    </div>
  );
}

function RecentSignalsCard({ trades, onSeeMore }: { trades: Trade[]; onSeeMore: () => void }) {
  const visibleTrades = trades.slice(0, 3);

  return (
    <section className="rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-4 shadow-[var(--dashboard-shadow)] 2xl:p-6">
      <div className="mb-4 flex items-center justify-between gap-3 2xl:mb-5">
        <h2 className="text-2xl font-bold leading-none text-[var(--dashboard-text)] 2xl:text-[28px]">
          Recent Signals
        </h2>
        <button
          onClick={onSeeMore}
          className="inline-flex shrink-0 items-center gap-2 rounded-full px-1 py-1 text-sm font-medium text-[var(--dashboard-link)] transition hover:text-accent"
        >
          <span className="hidden min-[1440px]:inline">See more</span>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--dashboard-control)] text-lg leading-none">
            ›
          </span>
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
              'grid grid-cols-[24px_minmax(0,1fr)_auto] items-center gap-2.5 py-3.5 2xl:grid-cols-[28px_minmax(0,1fr)_auto] 2xl:gap-3 2xl:py-4',
              index > 0 ? 'border-t border-[var(--dashboard-divider)]' : '',
            ].join(' ')}
          >
            <div className="flex h-6 w-6 items-center justify-center rounded-lg border border-cyan-border bg-cyan-soft text-cyan 2xl:h-7 2xl:w-7">
              <svg
                className="h-3 w-3 2xl:h-3.5 2xl:w-3.5"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
              </svg>
            </div>
            <div className="min-w-0">
              <div className="flex min-w-0 items-center gap-2">
                <p className="truncate text-sm font-semibold text-[var(--dashboard-text)] 2xl:text-base">
                  {trade.ticker}
                </p>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-bold ${SIGNAL_STYLE[recommendationFor(trade)]}`}
                >
                  {executionBadge(trade)}
                </span>
              </div>
              <p className="mt-1 line-clamp-1 text-xs text-[var(--dashboard-subtle)]">
                {unescapeHtml(trade.headline)}
              </p>
            </div>
            <div className="text-right">
              <p className="font-mono text-sm font-semibold text-positive">
                {(displayConfidence(trade) * 100).toFixed(0)}%
              </p>
              <p className="mt-1 text-[11px] text-[var(--dashboard-muted)]">
                {timeLabel(trade.created_at)}
              </p>
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
  error?: AppErrorCopy;
  onSeeMore: () => void;
}) {
  const visibleOrders = orders.slice(0, 3);

  return (
    <section className="rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card-muted)] p-4 shadow-[var(--dashboard-shadow)] 2xl:p-6">
      <div className="mb-4 flex items-center justify-between gap-3 2xl:mb-5">
        <h2 className="text-2xl font-bold leading-none text-[var(--dashboard-text)] 2xl:text-[28px]">
          Transactions
        </h2>
        <button
          onClick={onSeeMore}
          className="inline-flex shrink-0 items-center gap-2 rounded-full px-1 py-1 text-sm font-medium text-[var(--dashboard-link)] transition hover:text-accent"
        >
          <span className="hidden min-[1440px]:inline">See more</span>
          <span className="flex h-7 w-7 items-center justify-center rounded-full bg-[var(--dashboard-control)] text-lg leading-none">
            ›
          </span>
        </button>
      </div>

      <div className="space-y-1">
        {loading && visibleOrders.length === 0 && <EmptyDots />}
        {error && <AppErrorNotice error={error} compact className="mb-3" />}
        {!loading && error && visibleOrders.length === 0 && (
          <div className="flex min-h-[172px] items-center justify-center rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-row)] px-4 text-center text-sm text-[var(--dashboard-muted)]">
            Order activity will reappear after the next successful refresh.
          </div>
        )}
        {!loading && !error && visibleOrders.length === 0 && (
          <div className="flex min-h-[210px] items-center justify-center text-sm text-[var(--dashboard-muted)]">
            No Alpaca orders yet.
          </div>
        )}

        {visibleOrders.map((order, index) => {
          const side = order.side?.toLowerCase() === 'sell' ? 'sell' : 'buy';
          const bucket = orderBucket(order.status);
          return (
            <div
              key={order.id}
              className={[
                'grid grid-cols-[34px_minmax(0,1fr)_auto] items-center gap-3 py-3 2xl:grid-cols-[42px_minmax(0,1fr)_auto] 2xl:gap-4 2xl:py-3.5',
                index > 0 ? 'border-t border-[var(--dashboard-divider)]' : '',
              ].join(' ')}
            >
              <div
                className={`flex h-9 w-9 items-center justify-center rounded-full border 2xl:h-11 2xl:w-11 ${ORDER_DOT_STYLE[side]}`}
              >
                <span className="font-mono text-xs font-bold 2xl:text-sm">
                  {order.symbol?.slice(0, 1) ?? '?'}
                </span>
              </div>
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <p className="truncate text-sm font-semibold text-[var(--dashboard-text)] 2xl:text-base">
                    {order.symbol ?? 'Unknown'}
                  </p>
                  <span
                    className={`rounded-full border px-2 py-0.5 text-[10px] font-bold uppercase ${ORDER_STATUS_STYLE[bucket]}`}
                  >
                    {order.status ?? 'unknown'}
                  </span>
                </div>
                <p className="mt-1 text-xs capitalize text-[var(--dashboard-subtle)] 2xl:text-sm">
                  {order.side ?? 'order'} · {order.type ?? order.order_type ?? 'market'}
                </p>
              </div>
              <div className="text-right">
                <p
                  className={
                    side === 'buy'
                      ? 'font-mono text-sm font-semibold text-positive'
                      : 'font-mono text-sm font-semibold text-negative'
                  }
                >
                  {side === 'buy' ? '+' : '-'}
                  {orderQuantity(order)}
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
  fetchedAt,
}: {
  account: AlpacaAccount | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  loading: boolean;
  error?: AppErrorCopy;
  fetchedAt?: string;
}) {
  const accountValue = account?.portfolio_value ?? account?.equity;
  const equity = numberValue(accountValue);
  const lastEquity = numberValue(account?.last_equity);
  const dailyChange = equity !== null && lastEquity !== null ? equity - lastEquity : 0;
  const dailyChangePct = lastEquity ? (dailyChange / lastEquity) * 100 : 0;
  const isUp = dailyChange >= 0;
  // Bracket orders nest their take-profit / stop legs under the (filled) parent
  // when fetched with nested=true. Those legs keep working after the parent
  // fills, so open orders must be counted across parents AND their legs —
  // otherwise a live TP/SL is invisible and the count shows 0.
  const workingOrders = orders.flatMap((order) => [order, ...(order.legs ?? [])]);
  const openOrders = workingOrders.filter((order) => orderBucket(order.status) === 'open').length;
  // "Filled recent" counts placed (parent) orders so a closed bracket's exit
  // leg is not double-counted as a separate fill.
  const filledOrders = orders.filter((order) => orderBucket(order.status) === 'filled').length;
  const topPositions = [...positions]
    .sort(
      (a, b) =>
        Math.abs(numberValue(b.market_value) ?? 0) - Math.abs(numberValue(a.market_value) ?? 0),
    )
    .slice(0, 3);
  const investedValue = positions.reduce(
    (sum, position) => sum + Math.abs(numberValue(position.market_value) ?? 0),
    0,
  );
  const allocationPct = equity && equity > 0 ? (investedValue / equity) * 100 : null;
  const tradingBlocked = Boolean(
    account?.trading_blocked || account?.account_blocked || account?.trade_suspended_by_user,
  );

  const metrics = [
    {
      label: 'Buying power',
      value: money(account?.buying_power),
      sub: account?.multiplier ? `${account.multiplier}x margin` : 'available',
    },
    { label: 'Cash', value: money(account?.cash), sub: account?.currency ?? 'USD' },
    {
      label: 'Invested',
      value: compactMoney(investedValue),
      sub: allocationPct === null ? 'allocation' : `${allocationPct.toFixed(1)}% of equity`,
    },
    { label: 'Open orders', value: String(openOrders), sub: `${filledOrders} filled recent` },
  ];

  const statusPills = [
    {
      label: account?.status ?? 'paper',
      className:
        account?.status?.toLowerCase() === 'active'
          ? 'border-positive-border bg-positive-soft text-positive'
          : 'border-line bg-surface-2 text-muted',
    },
    {
      label: tradingBlocked ? 'Trading blocked' : 'Trading enabled',
      className: tradingBlocked
        ? 'border-negative-border bg-negative-soft text-negative'
        : 'border-positive-border bg-positive-soft text-positive',
    },
  ];

  return (
    <section className="rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-5 shadow-[var(--dashboard-shadow)] 2xl:p-6">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm font-medium text-[var(--dashboard-subtle)]">Alpaca Live Equity</p>
          <p className="mt-3 font-sans text-[30px] font-bold leading-none tracking-tight text-[var(--dashboard-text)]">
            {loading && !account ? '—' : money(accountValue)}
          </p>
          <p className="mt-2 text-[11px] font-medium text-[var(--dashboard-muted)]">
            Synced {fetchedAt ? timeLabel(fetchedAt) : '—'}
          </p>
        </div>
        <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-positive-border bg-positive-soft px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-positive">
          <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-positive" />
          Live
        </span>
      </div>

      <div
        className={`mt-4 inline-flex items-center gap-2 font-mono text-sm font-semibold ${isUp ? 'text-positive' : 'text-negative'}`}
      >
        <ChangeArrow up={isUp} />
        {money(Math.abs(dailyChange))} {percent(dailyChangePct)}
      </div>

      <div className="mt-5 flex flex-wrap gap-2">
        {statusPills.map((item) => (
          <span
            key={item.label}
            className={`rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide ${item.className}`}
          >
            {item.label}
          </span>
        ))}
      </div>

      <div className="mt-5 border-t border-[var(--dashboard-divider)] pt-5">
        <div className="mb-3">
          <h3 className="text-base font-semibold text-[var(--dashboard-text)]">Account Snapshot</h3>
        </div>

        {error && (
          <AppErrorNotice error={error} compact className="mb-4" />
        )}

        <div className="grid grid-cols-2 gap-2.5">
          {metrics.map((item) => (
            <div key={item.label} className="rounded-xl bg-[var(--dashboard-row)] px-3 py-2.5">
              <span className="block text-xs font-medium text-[var(--dashboard-subtle)]">
                {item.label}
              </span>
              <span className="mt-1 block truncate font-mono text-[15px] font-semibold text-[var(--dashboard-text)]">
                {loading && !account ? '—' : item.value}
              </span>
              <span className="mt-1 block truncate text-[10px] text-[var(--dashboard-muted)]">
                {loading && !account ? '—' : item.sub}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="mt-4 border-t border-[var(--dashboard-divider)] pt-4">
        <div className="mb-2.5 flex items-center justify-between gap-3">
          <h3 className="text-base font-semibold text-[var(--dashboard-text)]">Top Holdings</h3>
          <span className="rounded-full bg-[var(--dashboard-control)] px-3 py-1 text-[11px] font-semibold text-[var(--dashboard-subtle)]">
            Top 3
          </span>
        </div>

        {loading && positions.length === 0 && (
          <div className="flex min-h-[96px] items-center justify-center">
            <EmptyDots />
          </div>
        )}

        {!loading && topPositions.length === 0 && (
          <div className="flex min-h-[96px] items-center justify-center rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-row)] px-4 text-center text-sm text-[var(--dashboard-muted)]">
            No open Alpaca positions yet.
          </div>
        )}

        <div className="space-y-0">
          {topPositions.map((position) => {
            const marketValue = numberValue(position.market_value) ?? 0;
            const pl = numberValue(position.unrealized_pl);
            const isPositionUp = (pl ?? 0) >= 0;

            return (
              <div
                key={position.symbol ?? position.asset_class ?? marketValue}
                className="grid grid-cols-[34px_minmax(0,1fr)_auto] items-center gap-3 border-t border-[var(--dashboard-divider)] py-3 first:border-t-0"
              >
                <div
                  className={`flex h-9 w-9 items-center justify-center rounded-full border ${isPositionUp ? 'border-positive-border bg-positive-soft text-positive' : 'border-negative-border bg-negative-soft text-negative'}`}
                >
                  <span className="font-mono text-xs font-bold">
                    {position.symbol?.slice(0, 1) ?? '?'}
                  </span>
                </div>
                <div className="min-w-0">
                  <div className="flex min-w-0 items-center gap-2">
                    <p className="truncate text-sm font-semibold text-[var(--dashboard-text)]">
                      {position.symbol ?? 'Unknown'}
                    </p>
                    <span className="rounded-full border border-line bg-surface-2 px-2 py-0.5 text-[10px] font-bold uppercase text-muted">
                      {position.side ?? 'long'}
                    </span>
                  </div>
                  <p className="mt-1 truncate text-xs text-[var(--dashboard-subtle)]">
                    {shareQuantity(position.qty)} · {money(position.current_price)}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-sm font-semibold text-[var(--dashboard-text)]">
                    {compactMoney(position.market_value)}
                  </p>
                  <p
                    className={`mt-1 font-mono text-[11px] font-semibold ${isPositionUp ? 'text-positive' : 'text-negative'}`}
                  >
                    {ratioPercent(position.unrealized_plpc)}
                  </p>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}

const NAV_ICONS: Record<string, React.ReactNode> = {
  Dashboard: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="3" width="7" height="7" rx="1.5" />
      <rect x="14" y="3" width="7" height="7" rx="1.5" />
      <rect x="3" y="14" width="7" height="7" rx="1.5" />
      <rect x="14" y="14" width="7" height="7" rx="1.5" />
    </svg>
  ),
  Signals: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
    </svg>
  ),
  'Risk Gate': (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
    </svg>
  ),
  Orders: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="8" y1="6" x2="21" y2="6" />
      <line x1="8" y1="12" x2="21" y2="12" />
      <line x1="8" y1="18" x2="21" y2="18" />
      <line x1="3" y1="6" x2="3.01" y2="6" />
      <line x1="3" y1="12" x2="3.01" y2="12" />
      <line x1="3" y1="18" x2="3.01" y2="18" />
    </svg>
  ),
  Portfolio: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <line x1="18" y1="20" x2="18" y2="10" />
      <line x1="12" y1="20" x2="12" y2="4" />
      <line x1="6" y1="20" x2="6" y2="14" />
    </svg>
  ),
  Pipeline: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <circle cx="18" cy="18" r="3" />
      <circle cx="6" cy="6" r="3" />
      <path d="M6 21V9a9 9 0 0 0 9 9" />
    </svg>
  ),
  Settings: (
    <svg
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
};

// Label that fades/collapses in lockstep with the hover-expand rail. Lives inside
// the Sidebar provider so it can read `open` via useSidebar.
function CollapsingLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  const { open, animate } = useSidebar();
  const expanded = animate ? open : true;
  return (
    <motion.span
      animate={{ display: expanded ? 'inline-block' : 'none', opacity: expanded ? 1 : 0 }}
      className={className}
    >
      {children}
    </motion.span>
  );
}

// Brand lockup for the sidebar: the logo tile always shows; the wordmark
// collapses away when the rail is in its narrow (icon-only) state.
function SidebarBrand() {
  return (
    <div className="flex items-center gap-3 px-1">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white shadow-sm">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      </div>
      <CollapsingLabel className="min-w-0 whitespace-pre">
        <span className="block text-sm font-bold leading-none text-primary">Sentient Trader</span>
        <span className="mt-0.5 block text-[11px] font-medium text-muted">
          AI Market Intelligence
        </span>
      </CollapsingLabel>
    </div>
  );
}

// Autonomous-status footer. Only shown when the rail is expanded — in the narrow
// collapsed state it's hidden entirely rather than shrinking to a lone dot.
function SidebarStatus({ lastSignalTime }: { lastSignalTime: string }) {
  const { open, animate } = useSidebar();
  const expanded = animate ? open : true;
  if (!expanded) return null;
  return (
    <div className="mt-auto pt-4">
      <div className="flex items-center gap-2 rounded-xl border border-positive-border bg-positive-soft px-3 py-2.5">
        <span className="pulse-dot h-2 w-2 shrink-0 rounded-full bg-positive" />
        <div className="min-w-0 whitespace-pre">
          <span className="block text-xs font-semibold text-positive">Autonomous</span>
          <span className="mt-0.5 block text-[10px] text-positive opacity-70">
            Last signal {lastSignalTime}
          </span>
        </div>
      </div>
    </div>
  );
}

export default function DashboardClient({ initialTrades, initialStats }: DashboardClientProps) {
  const [trades, setTrades] = useState<Trade[]>(initialTrades);
  const [dashboardStats, setDashboardStats] = useState<DashboardStats | null>(initialStats);
  const [newIds, setNewIds] = useState<Set<string>>(new Set());
  const [selectedTrade, setSelectedTrade] = useState<Trade | null>(initialTrades[0] ?? null);
  const [traceLoadingId, setTraceLoadingId] = useState<string | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const [routeTrade, setRouteTrade] = useState<Trade | null>(null);
  const [routeTradeLoading, setRouteTradeLoading] = useState(false);
  const [routeTradeError, setRouteTradeError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(initialTrades.length === PAGE_SIZE);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<'rate_limited' | 'error' | null>(null);
  const [signalCounts, setSignalCounts] = useState<SignalCounts | null>(null);
  const [isSimulatorOpen, setIsSimulatorOpen] = useState(false);
  const [authGateOpen, setAuthGateOpen] = useState(false);
  const [authGateReason, setAuthGateReason] = useState<'auth_required' | 'limit_reached'>(
    'auth_required',
  );
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [navDrawerOpen, setNavDrawerOpen] = useState(false);
  // On mobile the Decision Core can't sit beside the feed, so a tap opens it
  // in a slide-up bottom sheet instead of silently updating an off-screen panel.
  const [mobileTraceOpen, setMobileTraceOpen] = useState(false);
  const [paperBannerDismissed, setPaperBannerDismissed] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('st-paper-banner-dismissed') === 'true';
    }
    return false;
  });

  const location = useLocation();
  const navigate = useNavigate();
  const pathname = location.pathname;
  const pipelineSignalId = pathname.match(/^\/pipeline\/(.+)$/)?.[1] ?? null;
  const activeView: ViewName = pipelineSignalId
    ? 'Pipeline'
    : PATH_VIEW_MAP[pathname] ?? 'Dashboard';

  const setActiveView = useCallback(
    (view: ViewName) => {
      navigate(VIEW_PATH_MAP[view]);
    },
    [navigate],
  );

  // ── Auth state ────────────────────────────────────────────────
  const { user, isAnonymous, isSuperUser, isLoading: authLoading, signOut } = useAuth();
  const [alpacaData, setAlpacaData] = useState<OrdersResponse>(EMPTY_ALPACA_DATA);
  const [alpacaLoading, setAlpacaLoading] = useState(true);

  const tailRef = useRef<string | null>(
    initialTrades.length > 0 ? initialTrades[initialTrades.length - 1].created_at : null,
  );
  const latestSeenRef = useRef<string | null>(initialTrades[0]?.created_at ?? null);
  const knownTradeIdsRef = useRef<Set<string>>(new Set(initialTrades.map((trade) => trade.id)));
  const tradeDetailCacheRef = useRef<Map<string, Trade>>(new Map());
  const pollingLatestRef = useRef(false);
  const hasMoreRef = useRef(hasMore);
  const loadingMoreRef = useRef(false);

  useEffect(() => {
    hasMoreRef.current = hasMore;
  }, [hasMore]);

  useEffect(() => {
    if (!isSimulatorOpen) return;
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setIsSimulatorOpen(false);
    };
    window.addEventListener('keydown', closeOnEscape);
    return () => window.removeEventListener('keydown', closeOnEscape);
  }, [isSimulatorOpen]);

  // A nav tap inside the drawer navigates and then this dismisses the overlay,
  // so a route change always closes the mobile nav.
  useEffect(() => {
    setNavDrawerOpen(false);
  }, [pathname]);

  // While the drawer is open, lock the page behind it and wire Escape to close.
  useEffect(() => {
    if (!navDrawerOpen) return;
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setNavDrawerOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
    };
  }, [navDrawerOpen]);

  // Leaving the Signals view (e.g. a bottom-nav tap) dismisses the detail sheet.
  useEffect(() => {
    setMobileTraceOpen(false);
  }, [activeView]);

  // Mirror the drawer: lock scroll + Escape-to-close while the sheet is open,
  // and auto-dismiss once the viewport grows to the desktop side-panel layout.
  useEffect(() => {
    if (!mobileTraceOpen) return;
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setMobileTraceOpen(false);
    };
    const desktop = window.matchMedia('(min-width: 1024px)');
    const closeOnDesktop = (e: MediaQueryListEvent) => {
      if (e.matches) setMobileTraceOpen(false);
    };
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    window.addEventListener('keydown', closeOnEscape);
    desktop.addEventListener('change', closeOnDesktop);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener('keydown', closeOnEscape);
      desktop.removeEventListener('change', closeOnDesktop);
    };
  }, [mobileTraceOpen]);

  const loadAlpacaSummary = useCallback(async () => {
    try {
      const json = await apiFetch<OrdersApiResponse>('/orders?status=all&limit=25');
      setAlpacaData({
        ...json,
        error: json.error ? formatAlpacaError(json.error, 'alpaca-summary') : undefined,
      });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setAlpacaData(EMPTY_ALPACA_DATA);
        setAlpacaLoading(false);
        return;
      }
      setAlpacaData((current) => ({
        ...current,
        error: formatAlpacaError(error, 'alpaca-summary'),
      }));
    } finally {
      setAlpacaLoading(false);
    }
  }, []);

  const loadDashboardStats = useCallback(async () => {
    try {
      const json = await apiFetch<{ stats: DashboardStats | null }>('/stats');
      if (json.stats) {
        setDashboardStats(json.stats);
      }
    } catch {
      // Keep the server-rendered stats or fall back to loaded rows.
    }
  }, []);

  useEffect(() => {
    loadAlpacaSummary();
    const interval = setInterval(loadAlpacaSummary, 15_000);
    return () => clearInterval(interval);
  }, [loadAlpacaSummary]);

  useEffect(() => {
    loadDashboardStats();
    const interval = setInterval(loadDashboardStats, 15_000);
    return () => clearInterval(interval);
  }, [loadDashboardStats]);

  const latestTrade = trades[0] ?? null;
  const lastSignalTime = latestTrade
    ? new Date(latestTrade.created_at).toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      })
    : 'Awaiting';

  const viewSubtitle: Partial<Record<ViewName, string>> = {
    Dashboard: 'Live AI trading activity overview',
    Signals: 'Real-time signal history and decision trace',
    Orders: 'Alpaca order execution and account context',
    Portfolio: 'Live equity, holdings, buying power, and Alpaca position detail',
    Settings: 'Agent configuration and tunable parameters',
    Pipeline: 'How every signal flows through the agent, end to end',
  };

  const ingestFreshTrades = useCallback((freshTrades: Trade[]) => {
    const uniqueTrades = freshTrades.filter((trade) => !knownTradeIdsRef.current.has(trade.id));
    if (uniqueTrades.length === 0) return;

    uniqueTrades.forEach((trade) => knownTradeIdsRef.current.add(trade.id));
    latestSeenRef.current = uniqueTrades[0].created_at;
    if (!tailRef.current) {
      tailRef.current = uniqueTrades[uniqueTrades.length - 1].created_at;
    }

    setTrades((prev) => [...uniqueTrades, ...prev]);
    setSelectedTrade((current) => current ?? uniqueTrades[0]);
    // Dashboard counters come exclusively from the /stats aggregate (the single
    // source of truth, re-polled below). Deriving them from the trade feed here
    // double-counted rows already included in the snapshot and made the cards
    // jitter on every refresh.
    setNewIds((prev) => {
      const next = new Set(prev);
      uniqueTrades.forEach((trade) => next.add(trade.id));
      return next;
    });

    const ids = uniqueTrades.map((trade) => trade.id);
    setTimeout(() => {
      setNewIds((prev) => {
        const next = new Set(prev);
        ids.forEach((id) => next.delete(id));
        return next;
      });
    }, 900);
  }, []);

  // ── Lightweight trade reconciliation ──────────────────────────
  const loadNewTrades = useCallback(async () => {
    if (pollingLatestRef.current) return;
    pollingLatestRef.current = true;

    try {
      const after = latestSeenRef.current;
      const url = after ? `/trades?after=${encodeURIComponent(after)}` : '/trades';
      const { trades: fresh, hasMore: nextHasMore } = await apiFetch<{
        trades: Trade[];
        hasMore?: boolean;
      }>(url);
      if (fresh.length > 0) {
        ingestFreshTrades(fresh);
        if (!after && nextHasMore !== undefined) {
          setHasMore(nextHasMore);
          hasMoreRef.current = nextHasMore;
        }
      }
    } finally {
      pollingLatestRef.current = false;
    }
  }, [ingestFreshTrades]);

  // ── Poll FastAPI for slim trade rows ──────────────────────────
  useEffect(() => {
    const refreshIfVisible = () => {
      if (document.visibilityState === 'visible') loadNewTrades();
    };

    loadNewTrades();
    const interval = setInterval(loadNewTrades, 10_000);
    window.addEventListener('focus', loadNewTrades);
    document.addEventListener('visibilitychange', refreshIfVisible);
    return () => {
      clearInterval(interval);
      window.removeEventListener('focus', loadNewTrades);
      document.removeEventListener('visibilitychange', refreshIfVisible);
    };
  }, [loadNewTrades]);

  const handleTradeSelect = useCallback((trade: Trade) => {
    setSelectedTrade(tradeDetailCacheRef.current.get(trade.id) ?? trade);
    // Below lg the detail lives in a bottom sheet rather than a side panel, so
    // a tap needs to surface it. On desktop the side panel is always visible.
    if (typeof window !== 'undefined' && window.matchMedia('(max-width: 1023px)').matches) {
      setMobileTraceOpen(true);
    }
  }, []);

  // Super-admin only: permanently delete a simulated signal.
  const handleDeleteTrade = useCallback(async (trade: Trade) => {
    if (!trade.is_simulated) return;
    await apiFetch(`/trades/${trade.id}`, { method: 'DELETE' });
    tradeDetailCacheRef.current.delete(trade.id);
    setTrades((prev) => prev.filter((t) => t.id !== trade.id));
    setSelectedTrade((current) => (current?.id === trade.id ? null : current));
  }, []);

  useEffect(() => {
    if (activeView !== 'Signals' || !selectedTrade) return;
    if (hasTracePayload(selectedTrade)) {
      setTraceLoadingId(null);
      setTraceError(null);
      return;
    }

    const cachedTrade = tradeDetailCacheRef.current.get(selectedTrade.id);
    if (cachedTrade && hasTracePayload(cachedTrade)) {
      setSelectedTrade(cachedTrade);
      return;
    }

    const tradeId = selectedTrade.id;
    const controller = new AbortController();
    setTraceLoadingId(tradeId);
    setTraceError(null);

    apiFetch<{ trade: Trade }>(`/trades/${tradeId}`, { signal: controller.signal })
      .then(({ trade }) => {
        tradeDetailCacheRef.current.set(trade.id, trade);
        setSelectedTrade((current) =>
          current?.id === trade.id ? { ...current, ...trade } : current,
        );
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setTraceError(
          error instanceof Error ? error.message : 'Could not load the full decision trace.',
        );
      })
      .finally(() => {
        setTraceLoadingId((current) => (current === tradeId ? null : current));
      });

    return () => controller.abort();
  }, [activeView, selectedTrade]);

  // ── Per-signal pipeline route (/pipeline/:id) ─────────────────
  useEffect(() => {
    if (!pipelineSignalId) return;
    const cached = tradeDetailCacheRef.current.get(pipelineSignalId);
    if (cached && hasTracePayload(cached)) {
      setRouteTrade(cached);
      setRouteTradeLoading(false);
      setRouteTradeError(null);
      return;
    }
    const controller = new AbortController();
    setRouteTrade(cached ?? null);
    setRouteTradeLoading(true);
    setRouteTradeError(null);

    apiFetch<{ trade: Trade }>(`/trades/${pipelineSignalId}`, { signal: controller.signal })
      .then(({ trade }) => {
        tradeDetailCacheRef.current.set(trade.id, trade);
        setRouteTrade(trade);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === 'AbortError') return;
        setRouteTradeError(
          error instanceof Error ? error.message : 'Could not load this signal.',
        );
      })
      .finally(() => setRouteTradeLoading(false));

    return () => controller.abort();
  }, [pipelineSignalId]);

  // ── Pagination ────────────────────────────────────────────────
  const loadMore = useCallback(async () => {
    if (loadingMoreRef.current || !hasMoreRef.current || !tailRef.current) return;
    loadingMoreRef.current = true;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const { trades: more, hasMore: next } = await apiFetch<{ trades: Trade[]; hasMore: boolean }>(
        `/trades?before=${encodeURIComponent(tailRef.current)}`,
      );
      if (more.length > 0) {
        const uniqueMore = more.filter((trade) => !knownTradeIdsRef.current.has(trade.id));
        uniqueMore.forEach((trade) => knownTradeIdsRef.current.add(trade.id));
        tailRef.current = (uniqueMore[uniqueMore.length - 1] ?? more[more.length - 1]).created_at;
        if (uniqueMore.length > 0) {
          setTrades((prev) => [...prev, ...uniqueMore]);
        }
      }
      setHasMore(next);
      hasMoreRef.current = next;
    } catch (error) {
      // Surface a graceful state instead of silently stopping. The infinite-scroll
      // observer is gated on this error (see LiveTicker), so we stop hammering the
      // rate limit; the user retries explicitly. 429 gets a distinct message.
      setLoadMoreError(error instanceof ApiError && error.status === 429 ? 'rate_limited' : 'error');
    } finally {
      loadingMoreRef.current = false;
      setIsLoadingMore(false);
    }
  }, []);

  const retryLoadMore = useCallback(() => {
    setLoadMoreError(null);
    void loadMore();
  }, [loadMore]);

  // Server-side filter-chip counts: all-time on load, date-range-scoped when the
  // feed's range picker changes — independent of the cursor-loaded rows.
  const fetchSignalCounts = useCallback(async (from: string, to: string) => {
    const params = new URLSearchParams();
    if (from) params.set('from', from);
    if (to) params.set('to', to);
    const qs = params.toString();
    try {
      const { counts } = await apiFetch<{ counts: SignalCounts }>(
        `/trades/summary${qs ? `?${qs}` : ''}`,
      );
      setSignalCounts(counts);
    } catch {
      // Non-fatal: the chips fall back to counting the loaded rows.
      setSignalCounts(null);
    }
  }, []);

  useEffect(() => {
    void fetchSignalCounts('', '');
  }, [fetchSignalCounts]);

  const handleSignalRangeChange = useCallback(
    (from: string, to: string) => {
      const toIso = (local: string) => (local ? new Date(local).toISOString() : '');
      void fetchSignalCounts(toIso(from), toIso(to));
    },
    [fetchSignalCounts],
  );

  return (
    <div className="min-h-screen bg-background text-primary lg:h-screen lg:overflow-hidden">
      <div className="flex min-h-screen lg:h-screen">
        {/* ── Sidebar (hover-to-expand rail, Aceternity UI) ─────────── */}
        <Sidebar>
          <SidebarBody className="justify-between gap-6 overflow-x-hidden">
            <div className="flex flex-1 flex-col overflow-hidden">
              <SidebarBrand />
              <nav className="mt-6 flex flex-col gap-0.5">
                {NAV_ITEMS.map((item) => (
                  <SidebarLink
                    key={item}
                    label={item}
                    icon={NAV_ICONS[item]}
                    active={activeView === item}
                    onClick={() => setActiveView(item)}
                  />
                ))}
              </nav>
            </div>
            <SidebarStatus lastSignalTime={lastSignalTime} />
          </SidebarBody>
        </Sidebar>

        {/* ── Main area ─────────────────────────────────────────────── */}
        <div className="flex min-w-0 flex-1 flex-col lg:h-screen">
          {/* Header */}
          <header className="z-50 shrink-0 border-b border-line bg-surface">
            <div className="flex min-h-[60px] items-center gap-3 px-4 py-3 md:px-6">
              {/* Mobile: hamburger + brand */}
              <div className="flex min-w-0 items-center gap-2 lg:hidden">
                <button
                  onClick={() => setNavDrawerOpen(true)}
                  aria-label="Open navigation menu"
                  aria-expanded={navDrawerOpen}
                  className="tap-target -ml-1 flex h-10 w-10 items-center justify-center rounded-xl text-secondary transition hover:bg-hover hover:text-primary active:scale-95"
                >
                  <svg
                    className="h-5 w-5"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <line x1="3" y1="6" x2="21" y2="6" />
                    <line x1="3" y1="12" x2="21" y2="12" />
                    <line x1="3" y1="18" x2="21" y2="18" />
                  </svg>
                </button>
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white">
                  <svg
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                  </svg>
                </div>
                <p className="hidden truncate text-sm font-bold text-primary sm:block">
                  Sentient Trader
                </p>
              </div>

              <div className="ml-auto flex items-center gap-2">
                <button
                  onClick={() => setIsSimulatorOpen(true)}
                  aria-label="Simulate a signal"
                  className="flex h-9 shrink-0 items-center gap-1.5 rounded-xl bg-accent px-3.5 text-[13px] font-semibold text-white shadow-sm transition-all duration-150 hover:opacity-90 active:scale-95"
                >
                  <svg
                    className="h-4 w-4"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .962 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.962 0z" />
                    <path d="M20 3v4" />
                    <path d="M22 5h-4" />
                    <path d="M4 17v2" />
                    <path d="M5 18H3" />
                  </svg>
                  <span className="whitespace-nowrap">Simulate</span>
                </button>
                <SystemStatus />

                {/* Auth: user menu or sign-in button */}
                {!authLoading && user && !isAnonymous && (
                  <div className="relative">
                    <button
                      onClick={() => setUserMenuOpen((prev) => !prev)}
                      className="flex items-center gap-2 rounded-xl border border-line bg-surface-2 px-2.5 py-1.5 transition hover:border-accent-border"
                    >
                      {/* Avatar: show profile picture if available, else initial */}
                      {user.user_metadata?.avatar_url ? (
                        <img
                          src={user.user_metadata.avatar_url}
                          alt=""
                          className="h-6 w-6 rounded-full"
                        />
                      ) : (
                        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-accent-soft text-[10px] font-bold text-accent">
                          {(user.email?.[0] ?? 'U').toUpperCase()}
                        </span>
                      )}
                      <span className="hidden text-xs font-medium text-secondary sm:inline">
                        {user.user_metadata?.full_name ?? user.email?.split('@')[0] ?? 'User'}
                      </span>
                      <svg
                        className={`h-3 w-3 text-muted transition-transform ${userMenuOpen ? 'rotate-180' : ''}`}
                        fill="none"
                        viewBox="0 0 24 24"
                        stroke="currentColor"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
                      </svg>
                    </button>

                    {/* Dropdown menu */}
                    {userMenuOpen && (
                      <>
                        {/* Invisible overlay to close on click outside */}
                        <div
                          className="fixed inset-0 z-[99]"
                          onClick={() => setUserMenuOpen(false)}
                        />
                        <div className="absolute right-0 top-full z-[100] mt-2 w-64 rounded-xl border border-line bg-surface shadow-card-md">
                          {/* User info */}
                          <div className="border-b border-line px-4 py-3">
                            <div className="flex items-center gap-3">
                              {user.user_metadata?.avatar_url ? (
                                <img
                                  src={user.user_metadata.avatar_url}
                                  alt=""
                                  className="h-9 w-9 rounded-full"
                                />
                              ) : (
                                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-accent-soft text-sm font-bold text-accent">
                                  {(user.email?.[0] ?? 'U').toUpperCase()}
                                </span>
                              )}
                              <div className="min-w-0 flex-1">
                                <p className="truncate text-sm font-semibold text-primary">
                                  {user.user_metadata?.full_name ??
                                    user.email?.split('@')[0] ??
                                    'User'}
                                </p>
                                <p className="truncate text-[11px] text-muted">{user.email}</p>
                              </div>
                            </div>
                            {/* Provider + super user badge */}
                            <div className="mt-2 flex items-center gap-1.5">
                              <span className="rounded-md border border-line bg-surface-2 px-1.5 py-0.5 text-[10px] font-semibold text-muted">
                                {(
                                  (user.app_metadata?.providers as string[] | undefined) ??
                                  [user.app_metadata?.provider ?? 'email']
                                ).join(' · ')}
                              </span>
                              {isSuperUser && (
                                <span className="rounded-md border border-accent-border bg-accent-soft px-1.5 py-0.5 text-[10px] font-bold text-accent">
                                  ADMIN
                                </span>
                              )}
                            </div>
                          </div>
                          {/* Theme + sign out */}
                          <div className="p-2">
                            <ThemeToggle variant="menu" />
                            <button
                              onClick={() => {
                                setUserMenuOpen(false);
                                signOut();
                              }}
                              className="flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-xs font-medium text-secondary transition hover:bg-hover hover:text-negative"
                            >
                              <svg
                                className="h-3.5 w-3.5"
                                fill="none"
                                viewBox="0 0 24 24"
                                stroke="currentColor"
                                strokeWidth={2}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M15.75 9V5.25A2.25 2.25 0 0 0 13.5 3h-6a2.25 2.25 0 0 0-2.25 2.25v13.5A2.25 2.25 0 0 0 7.5 21h6a2.25 2.25 0 0 0 2.25-2.25V15m3 0 3-3m0 0-3-3m3 3H9"
                                />
                              </svg>
                              Sign out
                            </button>
                          </div>
                        </div>
                      </>
                    )}
                  </div>
                )}
                {!authLoading && (isAnonymous || !user) && <ThemeToggle />}
                {!authLoading && (isAnonymous || !user) && (
                  <button
                    onClick={() => {
                      setAuthGateReason('auth_required');
                      setAuthGateOpen(true);
                    }}
                    className="flex h-9 items-center gap-1.5 rounded-xl border border-line bg-surface-2 px-3 text-xs font-semibold text-secondary transition hover:border-accent-border hover:text-accent"
                  >
                    <svg
                      className="h-3.5 w-3.5"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                      strokeWidth={2}
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0"
                      />
                    </svg>
                    Sign in
                  </button>
                )}
              </div>
            </div>
          </header>

          {/* Page content */}
          <main
            className={[
              'pb-bottom-nav mx-auto flex w-full max-w-[1660px] flex-1 flex-col overflow-y-auto bg-[var(--dashboard-bg)] px-4 py-5 md:px-6 lg:min-h-0',
              activeView === 'Dashboard' ? 'lg:overflow-y-auto' : 'gap-4 lg:overflow-y-auto',
            ].join(' ')}
          >
            {/* Paper trading disclaimer */}
            {!paperBannerDismissed && (
              <div className="mb-3 flex items-center gap-3 rounded-xl border border-warning-border bg-warning-soft px-4 py-2.5">
                <svg
                  className="h-4 w-4 shrink-0 text-warning"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M9.75 3.104v5.714a2.25 2.25 0 0 1-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 0 1 4.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0 1 12 15a9.065 9.065 0 0 0-6.23.693L5 14.5m14.8.8 1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0 1 12 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5"
                  />
                </svg>
                <p className="flex-1 text-xs leading-relaxed text-warning">
                  <strong>Paper Trading Mode</strong> — This dashboard uses Alpaca&apos;s paper
                  trading API with simulated funds. No real money is involved.
                </p>
                <button
                  onClick={() => {
                    localStorage.setItem('st-paper-banner-dismissed', 'true');
                    setPaperBannerDismissed(true);
                  }}
                  className="shrink-0 rounded-lg border border-warning/30 px-2 py-1 text-xs font-semibold text-warning transition hover:bg-warning/10 hover:border-warning/50 active:scale-95"
                >
                  Don&apos;t show again
                </button>
                <button
                  onClick={() => setPaperBannerDismissed(true)}
                  className="shrink-0 rounded-lg p-1 text-warning transition hover:bg-warning/10"
                  aria-label="Dismiss"
                >
                  <svg
                    className="h-3.5 w-3.5"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            )}

            {/* Page header strip */}
            {activeView !== 'Dashboard' && !pipelineSignalId && (
              <section className="glass-panel flex shrink-0 flex-wrap items-center justify-between gap-3 rounded-2xl px-5 py-3.5">
                <div className="flex flex-wrap items-center gap-3">
                  <h1 className="text-xl font-bold text-[var(--dashboard-text)]">{activeView}</h1>
                  <span className="hidden h-4 w-[1px] bg-[var(--dashboard-divider)] md:inline-block" />
                  <p className="text-xs text-[var(--dashboard-subtle)]">
                    {viewSubtitle[activeView] ?? 'View coming online.'}
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
            {activeView === 'Dashboard' && (
              <div className="w-full space-y-6">
                <StatsBar trades={trades} stats={dashboardStats} />
                <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_340px] xl:grid-cols-[minmax(0,1fr)_420px] 2xl:grid-cols-[minmax(0,1fr)_440px]">
                  <div className="min-w-0 space-y-6">
                    <PnLChart />
                    <CalibrationPanel />
                    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:gap-6">
                      <RecentSignalsCard
                        trades={trades}
                        onSeeMore={() => setActiveView('Signals')}
                      />
                      <RecentOrdersCard
                        orders={alpacaData.orders}
                        loading={alpacaLoading}
                        error={alpacaData.error}
                        onSeeMore={() => setActiveView('Orders')}
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
                      fetchedAt={alpacaData.fetchedAt}
                    />
                  </aside>
                </div>
              </div>
            )}

            {/* Signals view */}
            {activeView === 'Signals' && (
              <div className="grid min-h-[640px] flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)] xl:grid-cols-[minmax(420px,0.9fr)_minmax(0,1.1fr)]">
                <LiveTicker
                  trades={trades}
                  newIds={newIds}
                  onTradeSelect={handleTradeSelect}
                  selectedId={selectedTrade?.id ?? null}
                  onLoadMore={loadMore}
                  isLoadingMore={isLoadingMore}
                  hasMore={hasMore}
                  totalCount={dashboardStats?.analyzed}
                  serverCounts={signalCounts}
                  onRangeChange={handleSignalRangeChange}
                  loadError={loadMoreError}
                  onRetry={retryLoadMore}
                  onDeleteTrade={isSuperUser ? handleDeleteTrade : undefined}
                />
                <div className="hidden min-w-0 flex-col gap-3 lg:flex">
                  <AgentMonologue
                    trade={selectedTrade}
                    isLoadingTrace={Boolean(selectedTrade && traceLoadingId === selectedTrade.id)}
                    traceError={traceError}
                    onViewTrace={
                      selectedTrade ? () => navigate(`/pipeline/${selectedTrade.id}`) : undefined
                    }
                  />
                </div>
              </div>
            )}

            {activeView === 'Orders' && <OrdersPage />}

            {activeView === 'Portfolio' && (
              <PortfolioPage
                account={alpacaData.account}
                positions={alpacaData.positions}
                orders={alpacaData.orders}
                loading={alpacaLoading}
                error={alpacaData.error}
                fetchedAt={alpacaData.fetchedAt}
              />
            )}

            {activeView === 'Settings' && <SettingsPage />}

            {activeView === 'Pipeline' && (
              <PipelinePage
                stats={dashboardStats}
                trades={trades}
                newIds={newIds}
                signalMode={Boolean(pipelineSignalId)}
                signalTrade={routeTrade}
                signalLoading={routeTradeLoading}
                signalError={routeTradeError}
                onBack={() => navigate('/signals')}
              />
            )}

            {activeView !== 'Dashboard' &&
              activeView !== 'Signals' &&
              activeView !== 'Orders' &&
              activeView !== 'Portfolio' &&
              activeView !== 'Pipeline' &&
              activeView !== 'Settings' && (
                <div className="glass-panel flex min-h-[520px] flex-1 items-center justify-center rounded-2xl p-10 text-center">
                  <div>
                    <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-surface-2 text-muted">
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

      {/* ── Mobile bottom tab bar ──────────────────────────────────── */}
      <nav
        className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface shadow-[0_-4px_24px_var(--panel-shadow)] lg:hidden"
        aria-label="Primary"
      >
        <div className="mx-auto flex max-w-lg items-stretch justify-around">
          {BOTTOM_NAV_ITEMS.map((item) => {
            const active = activeView === item;
            return (
              <button
                key={item}
                onClick={() => setActiveView(item)}
                aria-current={active ? 'page' : undefined}
                className={[
                  'tap-target flex flex-1 flex-col items-center justify-center gap-1 px-1 py-2 text-[10px] font-semibold transition-colors',
                  active ? 'text-accent' : 'text-muted hover:text-secondary',
                ].join(' ')}
              >
                {NAV_ICONS[item]}
                <span className="leading-none">{BOTTOM_NAV_LABELS[item] ?? item}</span>
              </button>
            );
          })}
          {/* "More" surfaces the views that don't fit the bar and is highlighted
              whenever one of those views (Risk Gate, Pipeline, Settings) is active. */}
          <button
            onClick={() => setNavDrawerOpen(true)}
            aria-label="More views"
            aria-expanded={navDrawerOpen}
            className={[
              'tap-target flex flex-1 flex-col items-center justify-center gap-1 px-1 py-2 text-[10px] font-semibold transition-colors',
              navDrawerOpen || !BOTTOM_NAV_ITEMS.includes(activeView)
                ? 'text-accent'
                : 'text-muted hover:text-secondary',
            ].join(' ')}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <circle cx="5" cy="12" r="1" />
              <circle cx="12" cy="12" r="1" />
              <circle cx="19" cy="12" r="1" />
            </svg>
            <span className="leading-none">More</span>
          </button>
        </div>
      </nav>

      {/* ── Mobile nav drawer ──────────────────────────────────────── */}
      {navDrawerOpen && (
        <div
          className="fixed inset-0 z-[120] lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label="Navigation"
        >
          <div
            className="modal-backdrop backdrop-in absolute inset-0"
            onClick={() => setNavDrawerOpen(false)}
          />
          <aside className="drawer-in absolute inset-y-0 left-0 flex w-[82%] max-w-[300px] flex-col border-r border-line bg-surface px-5 py-6 shadow-card-lg">
            {/* Brand + close */}
            <div className="flex items-center justify-between gap-3 px-1">
              <div className="flex min-w-0 items-center gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-accent text-white shadow-sm">
                  <svg
                    width="16"
                    height="16"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2.5"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  >
                    <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
                  </svg>
                </div>
                <div className="min-w-0">
                  <p className="truncate text-sm font-bold leading-none text-primary">
                    Sentient Trader
                  </p>
                  <p className="mt-0.5 text-[11px] font-medium text-muted">AI Market Intelligence</p>
                </div>
              </div>
              <button
                onClick={() => setNavDrawerOpen(false)}
                aria-label="Close navigation"
                className="tap-target -mr-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-muted transition hover:bg-hover hover:text-primary active:scale-95"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Navigation */}
            <nav className="modern-scroll mt-6 min-h-0 flex-1 space-y-0.5 overflow-y-auto">
              <p className="mb-2 px-3 text-[10px] font-semibold uppercase tracking-widest text-muted">
                Navigation
              </p>
              {NAV_ITEMS.map((item) => (
                <button
                  key={item}
                  onClick={() => setActiveView(item)}
                  className={[
                    'tap-target flex w-full items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-medium transition-all duration-150',
                    activeView === item
                      ? 'bg-accent-soft text-accent'
                      : 'text-secondary hover:bg-hover hover:text-primary',
                  ].join(' ')}
                >
                  <span className={activeView === item ? 'text-accent' : 'text-muted'}>
                    {NAV_ICONS[item]}
                  </span>
                  {item}
                </button>
              ))}
            </nav>

            {/* Status */}
            <div className="mt-4 shrink-0">
              <div className="flex items-center gap-2 rounded-xl border border-positive-border bg-positive-soft px-3 py-2.5">
                <span className="pulse-dot h-2 w-2 shrink-0 rounded-full bg-positive" />
                <div className="min-w-0">
                  <p className="text-xs font-semibold text-positive">Autonomous</p>
                  <p className="mt-0.5 text-[10px] text-positive opacity-70">
                    Last signal {lastSignalTime}
                  </p>
                </div>
              </div>
            </div>
          </aside>
        </div>
      )}

      {/* ── Mobile Decision Core sheet (Signals) ───────────────────── */}
      {activeView === 'Signals' && mobileTraceOpen && selectedTrade && (
        <div
          className="fixed inset-0 z-[110] flex flex-col justify-end lg:hidden"
          role="dialog"
          aria-modal="true"
          aria-label={`Decision detail for ${selectedTrade.ticker}`}
        >
          <div
            className="modal-backdrop backdrop-in absolute inset-0"
            onClick={() => setMobileTraceOpen(false)}
          />
          <div className="sheet-up relative flex max-h-[88vh] w-full flex-col">
            {/* Grab handle + close, floating above the panel */}
            <div className="flex shrink-0 items-center justify-between px-4 pb-2 pt-1">
              <span className="h-8 w-8" aria-hidden="true" />
              <span className="h-1.5 w-10 rounded-full bg-white/70 shadow-sm" />
              <button
                onClick={() => setMobileTraceOpen(false)}
                aria-label="Close decision detail"
                className="tap-target flex h-8 w-8 items-center justify-center rounded-full border border-line bg-surface text-muted shadow-sm transition hover:text-primary active:scale-95"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              <AgentMonologue
                trade={selectedTrade}
                isLoadingTrace={Boolean(selectedTrade && traceLoadingId === selectedTrade.id)}
                traceError={traceError}
                onViewTrace={
                  selectedTrade ? () => navigate(`/pipeline/${selectedTrade.id}`) : undefined
                }
              />
            </div>
          </div>
        </div>
      )}

      {/* ── Auth Gate modal ────────────────────────────────────────── */}
      <AuthGate
        isOpen={authGateOpen}
        onClose={() => setAuthGateOpen(false)}
        reason={authGateReason}
      />

      {/* ── Simulate modal ────────────────────────────────────────── */}
      {isSimulatorOpen && (
        <div
          className="modal-backdrop fixed inset-0 z-[100] flex items-end justify-center backdrop-blur-sm sm:items-center sm:px-4 sm:py-6"
          role="presentation"
          onMouseDown={() => setIsSimulatorOpen(false)}
        >
          <div
            className="glass-panel sheet-up pb-safe modern-scroll max-h-[90vh] w-full max-w-2xl overflow-y-auto rounded-t-2xl p-5 sm:max-h-[calc(100vh-3rem)] sm:rounded-xl"
            role="dialog"
            aria-modal="true"
            aria-label="Simulate signal"
            onMouseDown={(e) => e.stopPropagation()}
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
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  viewBox="0 0 24 24"
                  stroke="currentColor"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
            <CustomNewsForm
              variant="modal"
              onAuthRequired={() => {
                setIsSimulatorOpen(false);
                setAuthGateReason('auth_required');
                setAuthGateOpen(true);
              }}
              onOpenSignals={() => {
                setIsSimulatorOpen(false);
                navigate('/signals');
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
