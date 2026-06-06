import PnLChart from '@/components/PnLChart';
import AppErrorNotice from '@/components/AppErrorNotice';
import { AppErrorCopy } from '@/lib/errors';

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

interface PortfolioPageProps {
  account: AlpacaAccount | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
  loading: boolean;
  error?: AppErrorCopy;
  fetchedAt?: string;
}

function numberValue(value?: string | number | null) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function money(value?: string | number | null, maximumFractionDigits = 2) {
  const number = numberValue(value);
  if (number === null) return '-';
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits,
  });
}

function compactMoney(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '-';
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  });
}

function percentFromRatio(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '-';
  const pct = number * 100;
  return `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%`;
}

function signedMoney(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '-';
  return `${number >= 0 ? '+' : '-'}${money(Math.abs(number))}`;
}

function shareQuantity(value?: string | number | null) {
  const number = numberValue(value);
  if (number === null) return '-';
  return number.toLocaleString('en-US', { maximumFractionDigits: 4 });
}

function dateTime(value?: string | null) {
  if (!value) return '-';
  return new Date(value).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
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

function MetricCard({
  label,
  value,
  sub,
  tone = 'text-[var(--dashboard-text)]',
}: {
  label: string;
  value: string | number;
  sub: string;
  tone?: string;
}) {
  return (
    <div className="rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] px-4 py-3 shadow-[var(--dashboard-shadow)]">
      <p className="truncate text-[11px] font-semibold uppercase tracking-wide text-[var(--dashboard-subtle)]">
        {label}
      </p>
      <p className={`mt-2 truncate font-mono text-xl font-semibold leading-none ${tone}`}>
        {value}
      </p>
      <p className="mt-2 truncate text-[11px] text-[var(--dashboard-muted)]">{sub}</p>
    </div>
  );
}

function FlagPill({ active, label }: { active: boolean; label: string }) {
  return (
    <span
      className={[
        'rounded-full border px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide',
        active
          ? 'border-warning-border bg-warning-soft text-warning'
          : 'border-line bg-surface-2 text-muted',
      ].join(' ')}
    >
      {label}
    </span>
  );
}

export default function PortfolioPage({
  account,
  positions,
  orders,
  loading,
  error,
  fetchedAt,
}: PortfolioPageProps) {
  const accountValue = account?.portfolio_value ?? account?.equity;
  const equity = numberValue(accountValue) ?? 0;
  const lastEquity = numberValue(account?.last_equity);
  const dayChange = lastEquity !== null ? equity - lastEquity : 0;
  const dayChangePct = lastEquity && lastEquity > 0 ? (dayChange / lastEquity) * 100 : 0;
  const totalUnrealized = positions.reduce(
    (sum, position) => sum + (numberValue(position.unrealized_pl) ?? 0),
    0,
  );
  const totalIntraday = positions.reduce(
    (sum, position) => sum + (numberValue(position.unrealized_intraday_pl) ?? 0),
    0,
  );
  const invested = positions.reduce(
    (sum, position) => sum + Math.abs(numberValue(position.market_value) ?? 0),
    0,
  );
  // Count open orders across bracket parents AND their nested legs: with
  // nested=true the working take-profit / stop legs sit under a filled parent,
  // so a top-level-only count reports 0 while orders are still live.
  const workingOrders = orders.flatMap((order) => [order, ...(order.legs ?? [])]);
  const openOrders = workingOrders.filter((order) => orderBucket(order.status) === 'open').length;
  const filledOrders = orders.filter((order) => orderBucket(order.status) === 'filled').length;
  const sortedPositions = [...positions].sort(
    (a, b) =>
      Math.abs(numberValue(b.market_value) ?? 0) - Math.abs(numberValue(a.market_value) ?? 0),
  );
  const exposurePct = equity > 0 ? (invested / equity) * 100 : 0;

  return (
    <div className="grid min-h-[720px] flex-1 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_380px] 2xl:grid-cols-[minmax(0,1fr)_420px]">
      <div className="min-w-0 space-y-4">
        <PnLChart />

        <section className="glass-panel flex min-h-[420px] flex-col overflow-hidden rounded-2xl">
          <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div>
                <p className="text-base font-semibold text-[var(--dashboard-text)]">
                  Live Holdings
                </p>
                <p className="mt-1 text-[11px] text-[var(--dashboard-subtle)]">
                  {loading ? 'Refreshing Alpaca positions...' : `Synced ${dateTime(fetchedAt)}`}
                </p>
              </div>
              <span className="inline-flex items-center gap-2 rounded-full border border-positive-border bg-positive-soft px-3 py-1 text-[11px] font-semibold uppercase tracking-wide text-positive">
                <span className="pulse-dot h-1.5 w-1.5 rounded-full bg-positive" />
                {positions.length} open
              </span>
            </div>
            {error && (
              <AppErrorNotice error={error} compact className="mt-3" />
            )}
          </div>

          <div className="modern-scroll min-h-0 flex-1 overflow-y-auto">
            {sortedPositions.length === 0 && (
              <div className="flex min-h-[280px] items-center justify-center px-6 text-center text-sm text-muted">
                {loading ? 'Loading Alpaca positions...' : 'No open positions from Alpaca.'}
              </div>
            )}

            {/* Mobile / tablet: stacked position cards (the dense table needs
                ~900px, which doesn't fit a phone, so we reflow it below lg). */}
            {sortedPositions.length > 0 && (
              <div className="space-y-2.5 px-4 py-4 lg:hidden">
                {sortedPositions.map((position) => {
                  const marketValue = numberValue(position.market_value) ?? 0;
                  const positionShare =
                    equity > 0 ? Math.min((Math.abs(marketValue) / equity) * 100, 100) : 0;
                  const pl = numberValue(position.unrealized_pl) ?? 0;
                  const intraday = numberValue(position.unrealized_intraday_pl) ?? 0;
                  const isUp = pl >= 0;

                  return (
                    <div
                      key={position.symbol ?? `${position.asset_class}-${marketValue}`}
                      className="rounded-xl border border-[var(--dashboard-divider)] bg-[var(--dashboard-row)] p-3.5"
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="truncate font-mono text-sm font-bold text-[var(--dashboard-text)]">
                            {position.symbol ?? '-'}
                          </p>
                          <p className="mt-0.5 truncate text-[10px] uppercase text-[var(--dashboard-muted)]">
                            {position.asset_class ?? 'asset'} · {position.exchange ?? 'alpaca'}
                          </p>
                        </div>
                        <div className="text-right">
                          <p className="font-mono text-sm font-semibold text-[var(--dashboard-text)]">
                            {money(position.market_value)}
                          </p>
                          <p
                            className={`mt-0.5 font-mono text-[11px] font-semibold ${isUp ? 'text-positive' : 'text-negative'}`}
                          >
                            {signedMoney(position.unrealized_pl)} ·{' '}
                            {percentFromRatio(position.unrealized_plpc)}
                          </p>
                        </div>
                      </div>

                      <div className="mt-3 grid grid-cols-3 gap-2">
                        <div>
                          <span className="block text-[10px] text-[var(--dashboard-muted)]">Qty</span>
                          <span className="mt-0.5 block font-mono text-xs text-[var(--dashboard-text)]">
                            {shareQuantity(position.qty)}
                          </span>
                        </div>
                        <div>
                          <span className="block text-[10px] text-[var(--dashboard-muted)]">
                            Current
                          </span>
                          <span className="mt-0.5 block font-mono text-xs text-[var(--dashboard-text)]">
                            {money(position.current_price)}
                          </span>
                        </div>
                        <div>
                          <span className="block text-[10px] text-[var(--dashboard-muted)]">
                            Avg entry
                          </span>
                          <span className="mt-0.5 block font-mono text-xs text-[var(--dashboard-text)]">
                            {money(position.avg_entry_price)}
                          </span>
                        </div>
                      </div>

                      <div className="mt-3">
                        <div className="flex items-center justify-between gap-2 text-[10px]">
                          <span
                            className={`font-mono ${intraday >= 0 ? 'text-positive' : 'text-negative'}`}
                          >
                            Today {signedMoney(position.unrealized_intraday_pl)} ·{' '}
                            {percentFromRatio(position.unrealized_intraday_plpc)}
                          </span>
                          <span className="font-mono text-[var(--dashboard-muted)]">
                            {positionShare.toFixed(1)}% exposure
                          </span>
                        </div>
                        <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-[var(--dashboard-control)]">
                          <div
                            className={isUp ? 'h-full rounded-full bg-positive' : 'h-full rounded-full bg-negative'}
                            style={{ width: `${Math.max(positionShare, marketValue ? 2 : 0)}%` }}
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {/* Desktop: dense table, kept scroll-contained so it can never
                push the page sideways. */}
            {sortedPositions.length > 0 && (
              <div className="hidden overflow-x-auto lg:block">
              <div className="min-w-[900px]">
                <div className="grid grid-cols-[110px_90px_110px_110px_120px_120px_120px_minmax(0,1fr)] border-b border-[var(--dashboard-divider)] px-5 py-3 text-[10px] font-semibold uppercase tracking-wide text-[var(--dashboard-muted)]">
                  <span>Symbol</span>
                  <span>Qty</span>
                  <span>Market</span>
                  <span>Current</span>
                  <span>Avg Entry</span>
                  <span>Today</span>
                  <span>Total P/L</span>
                  <span>Exposure</span>
                </div>

                {sortedPositions.map((position) => {
                  const marketValue = numberValue(position.market_value) ?? 0;
                  const positionShare =
                    equity > 0 ? Math.min((Math.abs(marketValue) / equity) * 100, 100) : 0;
                  const pl = numberValue(position.unrealized_pl) ?? 0;
                  const intraday = numberValue(position.unrealized_intraday_pl) ?? 0;
                  const isUp = pl >= 0;

                  return (
                    <div
                      key={position.symbol ?? `${position.asset_class}-${marketValue}`}
                      className="grid grid-cols-[110px_90px_110px_110px_120px_120px_120px_minmax(0,1fr)] items-center border-b border-[var(--dashboard-divider)] px-5 py-3 text-sm last:border-b-0"
                    >
                      <div className="min-w-0">
                        <p className="truncate font-mono font-bold text-[var(--dashboard-text)]">
                          {position.symbol ?? '-'}
                        </p>
                        <p className="mt-1 truncate text-[10px] uppercase text-[var(--dashboard-muted)]">
                          {position.asset_class ?? 'asset'} · {position.exchange ?? 'alpaca'}
                        </p>
                      </div>
                      <p className="font-mono text-xs text-[var(--dashboard-text)]">
                        {shareQuantity(position.qty)}
                      </p>
                      <p className="font-mono text-xs font-semibold text-[var(--dashboard-text)]">
                        {money(position.market_value)}
                      </p>
                      <p className="font-mono text-xs text-[var(--dashboard-text)]">
                        {money(position.current_price)}
                      </p>
                      <p className="font-mono text-xs text-[var(--dashboard-text)]">
                        {money(position.avg_entry_price)}
                      </p>
                      <div>
                        <p
                          className={`font-mono text-xs font-semibold ${intraday >= 0 ? 'text-positive' : 'text-negative'}`}
                        >
                          {signedMoney(position.unrealized_intraday_pl)}
                        </p>
                        <p
                          className={`mt-1 font-mono text-[10px] ${intraday >= 0 ? 'text-positive' : 'text-negative'}`}
                        >
                          {percentFromRatio(position.unrealized_intraday_plpc)}
                        </p>
                      </div>
                      <div>
                        <p
                          className={`font-mono text-xs font-semibold ${isUp ? 'text-positive' : 'text-negative'}`}
                        >
                          {signedMoney(position.unrealized_pl)}
                        </p>
                        <p
                          className={`mt-1 font-mono text-[10px] ${isUp ? 'text-positive' : 'text-negative'}`}
                        >
                          {percentFromRatio(position.unrealized_plpc)}
                        </p>
                      </div>
                      <div className="min-w-0">
                        <div className="h-1.5 overflow-hidden rounded-full bg-[var(--dashboard-control)]">
                          <div
                            className={
                              isUp
                                ? 'h-full rounded-full bg-positive'
                                : 'h-full rounded-full bg-negative'
                            }
                            style={{ width: `${Math.max(positionShare, marketValue ? 2 : 0)}%` }}
                          />
                        </div>
                        <p className="mt-1 text-right font-mono text-[10px] text-[var(--dashboard-muted)]">
                          {positionShare.toFixed(1)}%
                        </p>
                      </div>
                    </div>
                  );
                })}
              </div>
              </div>
            )}
          </div>
        </section>
      </div>

      <aside className="space-y-4">
        <div className="grid grid-cols-2 gap-3">
          <MetricCard
            label="Equity"
            value={money(accountValue)}
            sub={`${dayChange >= 0 ? '+' : '-'}${money(Math.abs(dayChange))} ${dayChangePct >= 0 ? '+' : ''}${dayChangePct.toFixed(2)}%`}
            tone="text-primary"
          />
          <MetricCard
            label="Invested"
            value={compactMoney(invested)}
            sub={`${exposurePct.toFixed(1)}% allocated`}
            tone="text-accent"
          />
          <MetricCard
            label="Unrealized"
            value={signedMoney(totalUnrealized)}
            sub="all open positions"
            tone={totalUnrealized >= 0 ? 'text-positive' : 'text-negative'}
          />
          <MetricCard
            label="Intraday P/L"
            value={signedMoney(totalIntraday)}
            sub="position day move"
            tone={totalIntraday >= 0 ? 'text-positive' : 'text-negative'}
          />
        </div>

        <section className="rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-5 shadow-[var(--dashboard-shadow)]">
          <div className="mb-4 flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-[var(--dashboard-text)]">Alpaca Account</h2>
            <span className="rounded-full bg-[var(--dashboard-control)] px-3 py-1 text-[11px] font-semibold uppercase text-[var(--dashboard-subtle)]">
              {account?.status ?? 'paper'}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-2.5">
            {[
              {
                label: 'Buying power',
                value: money(account?.buying_power),
                sub: account?.multiplier ? `${account.multiplier}x margin` : 'available',
              },
              { label: 'Cash', value: money(account?.cash), sub: account?.currency ?? 'USD' },
              {
                label: 'Day trade BP',
                value: money(account?.daytrading_buying_power),
                sub: `${account?.daytrade_count ?? 0} day trades`,
              },
              { label: 'RegT BP', value: money(account?.regt_buying_power), sub: 'regulation T' },
              { label: 'Long value', value: money(account?.long_market_value), sub: 'gross long' },
              {
                label: 'Short value',
                value: money(account?.short_market_value),
                sub: 'gross short',
              },
              {
                label: 'Maintenance',
                value: money(account?.maintenance_margin),
                sub: 'margin requirement',
              },
              {
                label: 'Open orders',
                value: String(openOrders),
                sub: `${filledOrders} filled recent`,
              },
            ].map((item) => (
              <div key={item.label} className="rounded-xl bg-[var(--dashboard-row)] px-3 py-3">
                <p className="truncate text-[11px] text-[var(--dashboard-subtle)]">{item.label}</p>
                <p className="mt-1 truncate font-mono text-sm font-semibold text-[var(--dashboard-text)]">
                  {loading && !account ? '-' : item.value}
                </p>
                <p className="mt-1 truncate text-[10px] text-[var(--dashboard-muted)]">
                  {loading && !account ? '-' : item.sub}
                </p>
              </div>
            ))}
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <FlagPill
              active={Boolean(account?.pattern_day_trader)}
              label={account?.pattern_day_trader ? 'PDT flagged' : 'No PDT flag'}
            />
            <FlagPill
              active={Boolean(
                account?.trading_blocked ||
                account?.account_blocked ||
                account?.trade_suspended_by_user,
              )}
              label={
                account?.trading_blocked ||
                account?.account_blocked ||
                account?.trade_suspended_by_user
                  ? 'Trading blocked'
                  : 'Trading enabled'
              }
            />
            <FlagPill
              active={Boolean(account?.transfers_blocked)}
              label={account?.transfers_blocked ? 'Transfers blocked' : 'Transfers clear'}
            />
            <FlagPill
              active={!account?.shorting_enabled}
              label={account?.shorting_enabled ? 'Shorting enabled' : 'Shorting off'}
            />
          </div>
        </section>

        <section className="rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card-muted)] p-5 shadow-[var(--dashboard-shadow)]">
          <h2 className="text-lg font-semibold text-[var(--dashboard-text)]">
            Recent Alpaca Activity
          </h2>
          <div className="mt-4 space-y-2">
            {orders.slice(0, 5).map((order) => (
              <div
                key={order.id}
                className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-lg bg-[var(--dashboard-row)] px-3 py-2.5"
              >
                <div className="min-w-0">
                  <p className="truncate font-mono text-sm font-semibold text-[var(--dashboard-text)]">
                    {order.symbol ?? '-'}
                  </p>
                  <p className="mt-1 truncate text-[11px] capitalize text-[var(--dashboard-subtle)]">
                    {order.side ?? 'order'} · {order.type ?? order.order_type ?? 'market'} ·{' '}
                    {order.status ?? 'unknown'}
                  </p>
                </div>
                <div className="text-right">
                  <p className="font-mono text-xs font-semibold text-[var(--dashboard-text)]">
                    {order.notional ? money(order.notional) : `${shareQuantity(order.qty)} sh`}
                  </p>
                  <p className="mt-1 text-[10px] text-[var(--dashboard-muted)]">
                    {dateTime(order.submitted_at ?? order.created_at)}
                  </p>
                </div>
              </div>
            ))}
            {!loading && orders.length === 0 && (
              <div className="flex min-h-[120px] items-center justify-center text-sm text-[var(--dashboard-muted)]">
                No recent Alpaca orders.
              </div>
            )}
          </div>
        </section>
      </aside>
    </div>
  );
}
