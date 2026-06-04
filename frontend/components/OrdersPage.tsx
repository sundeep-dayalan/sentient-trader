import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import AppErrorNotice from '@/components/AppErrorNotice';
import { AppErrorCopy, formatAlpacaError } from '@/lib/errors';

type OrderBucket = 'all' | 'open' | 'filled' | 'canceled' | 'rejected' | 'other';
type ActionNotice = { message: string; tone: 'success' | 'error' };

interface AlpacaAccount {
  status?: string;
  currency?: string;
  equity?: string;
  portfolio_value?: string;
  buying_power?: string;
  cash?: string;
  [key: string]: unknown;
}

interface AlpacaPosition {
  symbol?: string;
  qty?: string;
  market_value?: string;
  unrealized_pl?: string;
  [key: string]: unknown;
}

interface AlpacaOrder {
  id: string;
  client_order_id?: string;
  symbol?: string;
  side?: string;
  type?: string;
  order_type?: string;
  time_in_force?: string;
  qty?: string;
  notional?: string;
  filled_qty?: string;
  filled_avg_price?: string;
  limit_price?: string | null;
  stop_price?: string | null;
  status?: string;
  created_at?: string;
  submitted_at?: string;
  filled_at?: string | null;
  canceled_at?: string | null;
  expired_at?: string | null;
  failed_at?: string | null;
  updated_at?: string;
  legs?: AlpacaOrder[];
  [key: string]: unknown;
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

const BUCKETS: { label: string; value: OrderBucket }[] = [
  { label: 'All', value: 'all' },
  { label: 'Open', value: 'open' },
  { label: 'Filled', value: 'filled' },
  { label: 'Canceled', value: 'canceled' },
  { label: 'Rejected', value: 'rejected' },
  { label: 'Other', value: 'other' },
];

const OPEN_STATUSES = new Set([
  'new',
  'accepted',
  'accepted_for_bidding',
  'pending_new',
  'partially_filled',
  'pending_cancel',
  'pending_replace',
  'stopped',
]);

const CANCELABLE_STATUSES = new Set([
  'new',
  'accepted',
  'accepted_for_bidding',
  'pending_new',
  'partially_filled',
  'stopped',
]);

function money(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  });
}

function compactMoney(value?: string | number | null) {
  if (value === null || value === undefined || value === '') return '-';
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  });
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

function bucketFor(status?: string): OrderBucket {
  const normalized = status?.toLowerCase() ?? 'other';
  if (OPEN_STATUSES.has(normalized)) return 'open';
  if (normalized === 'filled') return 'filled';
  if (normalized === 'canceled' || normalized === 'expired' || normalized === 'done_for_day')
    return 'canceled';
  if (normalized === 'rejected' || normalized === 'failed' || normalized === 'suspended')
    return 'rejected';
  return 'other';
}

function canCancel(status?: string) {
  return CANCELABLE_STATUSES.has(status?.toLowerCase() ?? '');
}

function statusClasses(status?: string) {
  const bucket = bucketFor(status);
  return {
    open: 'border-cyan-border bg-cyan-soft text-cyan',
    filled: 'border-positive-border bg-positive-soft text-positive',
    canceled: 'border-warning-border bg-warning-soft text-warning',
    rejected: 'border-negative-border bg-negative-soft text-negative',
    all: 'border-line bg-surface-2 text-muted',
    other: 'border-line bg-surface-2 text-muted',
  }[bucket];
}

function sideClasses(side?: string) {
  return side?.toLowerCase() === 'buy'
    ? 'border-positive-border bg-positive-soft text-positive'
    : 'border-negative-border bg-negative-soft text-negative';
}

function orderQuantity(order: AlpacaOrder) {
  if (order.notional) return money(order.notional);
  if (order.qty) return `${Number(order.qty).toLocaleString('en-US')} sh`;
  return '-';
}

function orderPrice(order: AlpacaOrder) {
  if (order.filled_avg_price) return money(order.filled_avg_price);
  if (order.limit_price) return `LMT ${money(order.limit_price)}`;
  if (order.stop_price) return `STP ${money(order.stop_price)}`;
  return '-';
}

function DetailRow({ label, value }: { label: string; value?: string | number | null }) {
  return (
    <div className="rounded-lg border border-line bg-surface-2 px-3 py-2">
      <p className="text-[10px] font-semibold uppercase text-muted">{label}</p>
      <p className="mt-1 break-words font-mono text-xs text-secondary">{value ?? '-'}</p>
    </div>
  );
}

export default function OrdersPage() {
  const [data, setData] = useState<OrdersResponse>({
    account: null,
    positions: [],
    orders: [],
    fetchedAt: new Date().toISOString(),
  });
  const [loading, setLoading] = useState(true);
  const [bucket, setBucket] = useState<OrderBucket>('all');
  const [query, setQuery] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedCancelIds, setSelectedCancelIds] = useState<Set<string>>(new Set());
  const [confirmCancelOpen, setConfirmCancelOpen] = useState(false);
  const [canceling, setCanceling] = useState(false);
  const [cancelNotice, setCancelNotice] = useState<ActionNotice | null>(null);

  const loadOrders = useCallback(async () => {
    try {
      const json = await apiFetch<OrdersApiResponse>('/orders?status=all&limit=100');
      setData({
        ...json,
        error: json.error ? formatAlpacaError(json.error, 'alpaca-orders') : undefined,
      });
      setSelectedId((current) => current ?? json.orders[0]?.id ?? null);
      setSelectedCancelIds((current) => {
        const liveCancelableIds = new Set(
          json.orders.filter((order) => canCancel(order.status)).map((order) => order.id),
        );
        return new Set([...current].filter((id) => liveCancelableIds.has(id)));
      });
    } catch (error) {
      if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
        setLoading(false);
        return;
      }
      setData((current) => ({ ...current, error: formatAlpacaError(error, 'alpaca-orders') }));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadOrders();
    const interval = setInterval(loadOrders, 30_000);
    return () => clearInterval(interval);
  }, [loadOrders]);

  useEffect(() => {
    if (!selectedId && data.orders.length > 0) {
      setSelectedId(data.orders[0].id);
    }
  }, [data.orders, selectedId]);

  const filteredOrders = useMemo(() => {
    const search = query.trim().toLowerCase();
    return data.orders.filter((order) => {
      const matchesBucket = bucket === 'all' || bucketFor(order.status) === bucket;
      const matchesSearch =
        !search ||
        [order.symbol, order.side, order.status, order.type, order.client_order_id, order.id].some(
          (value) =>
            String(value ?? '')
              .toLowerCase()
              .includes(search),
        );
      return matchesBucket && matchesSearch;
    });
  }, [bucket, data.orders, query]);

  const selectedOrder =
    data.orders.find((order) => order.id === selectedId) ?? filteredOrders[0] ?? null;
  const selectedCancelOrders = data.orders.filter(
    (order) => selectedCancelIds.has(order.id) && canCancel(order.status),
  );
  const openOrders = data.orders.filter((order) => bucketFor(order.status) === 'open').length;
  const filledOrders = data.orders.filter((order) => bucketFor(order.status) === 'filled').length;
  const rejectedOrders = data.orders.filter(
    (order) => bucketFor(order.status) === 'rejected',
  ).length;

  const accountValue = data.account?.portfolio_value ?? data.account?.equity;

  function toggleCancelSelection(order: AlpacaOrder) {
    if (!canCancel(order.status)) return;
    setSelectedCancelIds((current) => {
      const next = new Set(current);
      if (next.has(order.id)) next.delete(order.id);
      else next.add(order.id);
      return next;
    });
  }

  function openCancelConfirm(order?: AlpacaOrder) {
    setCancelNotice(null);
    if (order) {
      setSelectedCancelIds(new Set([order.id]));
    }
    setConfirmCancelOpen(true);
  }

  async function cancelSelectedOrders() {
    if (selectedCancelOrders.length === 0) return;

    setCanceling(true);
    setCancelNotice(null);

    try {
      const result = await apiFetch<{
        canceled?: number;
        failed?: number;
        error?: string;
      }>('/orders/cancel', {
        method: 'POST',
        body: JSON.stringify({ orderIds: selectedCancelOrders.map((order) => order.id) }),
      });

      if (result.error) {
        throw new Error(result.error);
      }

      setCancelNotice({
        message: `${result.canceled ?? 0} cancel request(s) accepted${result.failed ? `, ${result.failed} failed` : ''}.`,
        tone: result.failed ? 'error' : 'success',
      });
      setSelectedCancelIds(new Set());
      setConfirmCancelOpen(false);
      await loadOrders();
    } catch (error) {
      setCancelNotice({
        message: formatAlpacaError(error, 'cancel').message,
        tone: 'error',
      });
    } finally {
      setCanceling(false);
    }
  }

  return (
    <>
      <div className="grid min-h-0 flex-1 grid-rows-[auto_minmax(0,1fr)] gap-4">
        <div className="grid shrink-0 grid-cols-2 gap-3 xl:grid-cols-5">
          {[
            {
              label: 'Account Value',
              value: compactMoney(accountValue),
              tone: 'text-primary',
              sub: data.account?.status ?? 'alpaca',
            },
            {
              label: 'Buying Power',
              value: compactMoney(data.account?.buying_power),
              tone: 'text-cyan',
              sub: data.account?.currency ?? 'USD',
            },
            { label: 'Open Orders', value: openOrders, tone: 'text-cyan', sub: 'working' },
            { label: 'Filled', value: filledOrders, tone: 'text-positive', sub: 'executed' },
            {
              label: 'Positions',
              value: data.positions.length,
              tone: rejectedOrders > 0 ? 'text-negative' : 'text-accent',
              sub: rejectedOrders > 0 ? `${rejectedOrders} rejected` : 'open',
            },
          ].map((card) => (
            <div key={card.label} className="metric-card glass-panel-subtle rounded-lg px-4 py-3">
              <p className="truncate text-[10px] font-semibold uppercase text-muted">
                {card.label}
              </p>
              <p className={`mt-2 font-mono text-xl font-semibold leading-none ${card.tone}`}>
                {card.value}
              </p>
              <p className="mt-2 text-[10px] text-muted">{card.sub}</p>
            </div>
          ))}
        </div>

        <div className="grid min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
          <section className="glass-panel flex min-h-[520px] flex-col overflow-hidden rounded-2xl xl:min-h-0">
            <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
              <div className="flex flex-wrap items-center gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-[var(--dashboard-text)]">
                    Alpaca Orders
                  </p>
                  <p className="mt-0.5 text-[11px] text-[var(--dashboard-subtle)]">
                    {loading ? 'Loading orders...' : `Fetched ${dateTime(data.fetchedAt)}`}
                  </p>
                </div>
                {cancelNotice && (
                  <span
                    className={[
                      'rounded-md border px-2.5 py-1 text-[11px] font-semibold',
                      cancelNotice.tone === 'success'
                        ? 'border-cyan-border bg-cyan-soft text-cyan'
                        : 'border-negative-border bg-negative-soft text-negative',
                    ].join(' ')}
                  >
                    {cancelNotice.message}
                  </span>
                )}
                <button
                  onClick={() => openCancelConfirm()}
                  disabled={selectedCancelOrders.length === 0 || canceling}
                  className={[
                    'rounded-lg border px-3 py-2 text-[12px] font-semibold transition-all duration-200',
                    selectedCancelOrders.length === 0 || canceling
                      ? 'cursor-not-allowed border-line bg-surface-2 text-muted'
                      : 'border-negative-border bg-negative-soft text-negative hover:border-negative',
                  ].join(' ')}
                >
                  Cancel selected{' '}
                  {selectedCancelOrders.length > 0 ? `(${selectedCancelOrders.length})` : ''}
                </button>
              </div>

              {data.error && <AppErrorNotice error={data.error} compact className="mt-3" />}

              <div className="mt-3 flex flex-col gap-2 lg:flex-row lg:items-center">
                <div className="flex flex-wrap gap-1.5">
                  {BUCKETS.map((option) => (
                    <button
                      key={option.value}
                      onClick={() => setBucket(option.value)}
                      className={[
                        'rounded-md border px-2.5 py-1 text-[11px] font-semibold transition-all duration-200',
                        bucket === option.value
                          ? 'border-accent-border bg-accent-soft text-accent'
                          : 'border-line bg-surface-2 text-muted hover:border-cyan-border hover:text-secondary',
                      ].join(' ')}
                    >
                      {option.label}
                    </button>
                  ))}
                </div>
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  className="min-w-0 rounded-lg border border-line bg-surface-2 px-3 py-2 text-sm text-primary outline-none transition-colors placeholder:text-muted focus:border-accent-border lg:ml-auto lg:w-72"
                  placeholder="Search symbol, status, order id"
                />
              </div>
            </div>

            <div className="modern-scroll min-h-0 flex-1 overflow-y-auto p-3 pr-2">
              {filteredOrders.length === 0 && (
                <div className="flex h-full min-h-[240px] items-center justify-center text-sm text-muted">
                  {loading ? 'Loading Alpaca orders...' : 'No orders match this view.'}
                </div>
              )}

              {filteredOrders.map((order) => {
                const selected = selectedOrder?.id === order.id;
                return (
                  <div
                    key={order.id}
                    onClick={() => setSelectedId(order.id)}
                    className={[
                      'mb-2 grid w-full min-w-0 cursor-pointer gap-3 overflow-hidden rounded-xl border px-3.5 py-3 text-left transition-all duration-200 lg:grid-cols-[28px_96px_72px_108px_minmax(0,1fr)_108px]',
                      selected
                        ? 'border-accent-border bg-selected shadow-glow'
                        : 'border-[var(--dashboard-border)] bg-[var(--dashboard-row)] hover:border-cyan-border hover:bg-hover',
                    ].join(' ')}
                  >
                    <div className="flex items-start pt-0.5">
                      <input
                        type="checkbox"
                        aria-label={`Select ${order.symbol ?? 'order'} for cancel`}
                        checked={selectedCancelIds.has(order.id)}
                        disabled={!canCancel(order.status)}
                        onChange={() => toggleCancelSelection(order)}
                        onClick={(event) => event.stopPropagation()}
                        className="h-4 w-4 rounded border-line bg-surface-2 accent-[var(--negative)] disabled:opacity-30"
                      />
                    </div>
                    <div className="min-w-0">
                      <p className="font-mono text-sm font-bold text-accent">
                        {order.symbol ?? '-'}
                      </p>
                      <p className="mt-1 text-[10px] text-muted">
                        {dateTime(order.submitted_at ?? order.created_at)}
                      </p>
                    </div>
                    <div className="flex min-w-0 items-start gap-2">
                      <span
                        className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase ${sideClasses(order.side)}`}
                      >
                        {order.side ?? '-'}
                      </span>
                    </div>
                    <div className="min-w-0">
                      <p className="font-mono text-xs text-secondary">{orderQuantity(order)}</p>
                      <p className="mt-1 text-[10px] text-muted">
                        {order.type ?? order.order_type ?? 'order'}
                      </p>
                    </div>
                    <div className="min-w-0">
                      <p className="font-mono text-xs text-secondary">{orderPrice(order)}</p>
                      <p className="mt-1 truncate text-[10px] text-muted">
                        {order.client_order_id ?? order.id}
                      </p>
                    </div>
                    <div className="min-w-0 lg:text-right">
                      <span
                        className={`inline-flex max-w-full truncate rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase ${statusClasses(order.status)}`}
                      >
                        {order.status ?? 'unknown'}
                      </span>
                      <p className="mt-1 font-mono text-[10px] text-muted">
                        filled {Number(order.filled_qty ?? 0).toLocaleString('en-US')}
                      </p>
                    </div>
                  </div>
                );
              })}
            </div>
          </section>

          <aside className="glass-panel flex min-h-[520px] flex-col overflow-hidden rounded-2xl xl:min-h-0">
            <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
              <p className="text-sm font-semibold text-[var(--dashboard-text)]">Order Detail</p>
              <p className="mt-0.5 text-[11px] text-[var(--dashboard-subtle)]">
                Full Alpaca order payload for the selected row.
              </p>
            </div>

            {selectedOrder ? (
              <div className="modern-scroll min-h-0 flex-1 space-y-4 overflow-y-auto p-4 pr-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-2xl font-semibold text-accent">
                      {selectedOrder.symbol ?? '-'}
                    </p>
                    <p className="mt-1 break-all font-mono text-[10px] text-muted">
                      {selectedOrder.id}
                    </p>
                  </div>
                  <span
                    className={`rounded-md border px-2.5 py-1 text-[11px] font-semibold uppercase ${statusClasses(selectedOrder.status)}`}
                  >
                    {selectedOrder.status ?? 'unknown'}
                  </span>
                </div>

                {canCancel(selectedOrder.status) && (
                  <button
                    onClick={() => openCancelConfirm(selectedOrder)}
                    disabled={canceling}
                    className="w-full rounded-lg border border-negative-border bg-negative-soft px-3 py-2 text-[12px] font-semibold text-negative transition-all duration-200 hover:border-negative disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    Cancel this order
                  </button>
                )}

                <div className="grid grid-cols-2 gap-2">
                  <DetailRow label="Side" value={selectedOrder.side?.toUpperCase()} />
                  <DetailRow label="Type" value={selectedOrder.type ?? selectedOrder.order_type} />
                  <DetailRow
                    label="Time In Force"
                    value={selectedOrder.time_in_force?.toUpperCase()}
                  />
                  <DetailRow label="Qty" value={selectedOrder.qty} />
                  <DetailRow label="Filled Qty" value={selectedOrder.filled_qty} />
                  <DetailRow label="Avg Fill" value={money(selectedOrder.filled_avg_price)} />
                  <DetailRow label="Limit" value={money(selectedOrder.limit_price)} />
                  <DetailRow label="Stop" value={money(selectedOrder.stop_price)} />
                  <DetailRow label="Submitted" value={dateTime(selectedOrder.submitted_at)} />
                  <DetailRow label="Filled" value={dateTime(selectedOrder.filled_at)} />
                </div>

                {selectedOrder.legs && selectedOrder.legs.length > 0 && (
                  <div className="rounded-lg border border-line bg-surface-2 p-3">
                    <p className="text-[10px] font-semibold uppercase text-muted">Nested Legs</p>
                    <p className="mt-2 text-sm text-secondary">
                      {selectedOrder.legs.length} child order(s)
                    </p>
                  </div>
                )}

                <div className="rounded-lg border border-line bg-surface-2 p-3">
                  <p className="mb-2 text-[10px] font-semibold uppercase text-muted">
                    Raw Alpaca Payload
                  </p>
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap break-words font-mono text-[10px] leading-relaxed text-secondary">
                    {JSON.stringify(selectedOrder, null, 2)}
                  </pre>
                </div>
              </div>
            ) : (
              <div className="flex min-h-0 flex-1 items-center justify-center p-6 text-center text-sm text-muted">
                Select an order to inspect its Alpaca payload.
              </div>
            )}
          </aside>
        </div>
      </div>
      {confirmCancelOpen && (
        <div
          className="modal-backdrop fixed inset-0 z-[100] flex items-center justify-center px-4 py-6 backdrop-blur-md"
          role="presentation"
          onMouseDown={() => !canceling && setConfirmCancelOpen(false)}
        >
          <div
            className="glass-panel w-full max-w-lg rounded-lg p-4 shadow-card-md"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm order cancellation"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="flex items-start justify-between gap-3 border-b border-line pb-3">
              <div>
                <p className="text-sm font-semibold">Cancel selected orders?</p>
                <p className="mt-1 text-xs leading-5 text-muted">
                  This sends cancel requests to Alpaca for open paper orders. Orders already filled
                  or no longer cancelable may be rejected.
                </p>
              </div>
              <button
                onClick={() => setConfirmCancelOpen(false)}
                disabled={canceling}
                className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface-2 text-muted transition-all duration-200 hover:border-accent-border hover:text-primary disabled:opacity-50"
                aria-label="Close cancellation dialog"
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

            <div className="modern-scroll mt-4 max-h-56 space-y-2 overflow-y-auto pr-2">
              {selectedCancelOrders.map((order) => (
                <div
                  key={order.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-line bg-surface-2 px-3 py-2"
                >
                  <div>
                    <p className="font-mono text-sm font-semibold text-accent">
                      {order.symbol ?? '-'}
                    </p>
                    <p className="mt-0.5 text-[10px] text-muted">
                      {orderQuantity(order)} | {order.type ?? order.order_type}
                    </p>
                  </div>
                  <span
                    className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase ${statusClasses(order.status)}`}
                  >
                    {order.status}
                  </span>
                </div>
              ))}
            </div>

            {cancelNotice && (
              <p
                className={[
                  'mt-3 rounded-lg border px-3 py-2 text-xs',
                  cancelNotice.tone === 'success'
                    ? 'border-cyan-border bg-cyan-soft text-cyan'
                    : 'border-negative-border bg-negative-soft text-negative',
                ].join(' ')}
              >
                {cancelNotice.message}
              </p>
            )}

            <div className="mt-4 flex flex-col gap-2 sm:flex-row sm:justify-end">
              <button
                onClick={() => setConfirmCancelOpen(false)}
                disabled={canceling}
                className="rounded-lg border border-line bg-surface-2 px-4 py-2 text-[12px] font-semibold text-secondary transition-all duration-200 hover:border-accent-border hover:text-primary disabled:opacity-50"
              >
                Keep orders
              </button>
              <button
                onClick={cancelSelectedOrders}
                disabled={canceling || selectedCancelOrders.length === 0}
                className="rounded-lg border border-negative bg-negative px-4 py-2 text-[12px] font-semibold text-white transition-all duration-200 hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {canceling
                  ? 'Cancelling...'
                  : `Cancel ${selectedCancelOrders.length} order${selectedCancelOrders.length === 1 ? '' : 's'}`}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
