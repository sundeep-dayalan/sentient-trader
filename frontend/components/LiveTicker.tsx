import { useEffect, useMemo, useRef, useState } from 'react';
import { safeArticleUrl, unescapeHtml } from '@/lib/news';
import { Trade } from '@/lib/types';

const ACTION_STYLE: Record<string, string> = {
  BUY: 'bg-positive-soft text-positive border-positive-border',
  SELL: 'bg-negative-soft text-negative border-negative-border',
  HOLD: 'bg-surface-2 text-muted border-line',
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

function decisionPathLabel(path?: string | null): string | null {
  if (path === 'pre_screen') return 'PRE-SCREEN';
  if (path === 'full_debate') return 'FULL DEBATE';
  if (path === 'expired') return 'EXPIRED';
  return null;
}

type SignalFilter = 'ALL' | 'BUY' | 'SELL' | 'HOLD' | 'SIM';

const FILTERS: Array<{ key: SignalFilter; label: string }> = [
  { key: 'ALL', label: 'All' },
  { key: 'BUY', label: 'Buy' },
  { key: 'SELL', label: 'Sell' },
  { key: 'HOLD', label: 'Hold' },
  { key: 'SIM', label: 'Sim' },
];

function localTimestamp(value: string): number {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? -1 : date.getTime();
}

function dateTimeValueToMs(value: string): number | null {
  if (!value) return null;
  const time = new Date(value).getTime();
  return Number.isNaN(time) ? null : time;
}

function formatDateTimeLabel(value: string): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function rangeLabel(start: string, end: string): string {
  if (start && end) return `${formatDateTimeLabel(start)} - ${formatDateTimeLabel(end)}`;
  if (start) return `${formatDateTimeLabel(start)} ->`;
  if (end) return `-> ${formatDateTimeLabel(end)}`;
  return 'Date range';
}

interface LiveTickerProps {
  trades: Trade[];
  newIds: Set<string>;
  onTradeSelect: (trade: Trade) => void;
  selectedId: string | null;
  onLoadMore: () => void;
  isLoadingMore: boolean;
  hasMore: boolean;
  previewLimit?: number;
  totalCount?: number;
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
  totalCount,
}: LiveTickerProps) {
  const sentinelRef = useRef<HTMLDivElement>(null);
  const isPreview = Boolean(previewLimit);
  const [filter, setFilter] = useState<SignalFilter>('ALL');
  const [rangeStart, setRangeStart] = useState('');
  const [rangeEnd, setRangeEnd] = useState('');
  const [isRangeOpen, setIsRangeOpen] = useState(false);
  const onLoadMoreRef = useRef(onLoadMore);
  const rangePickerRef = useRef<HTMLDivElement>(null);
  const hasRangeFilter = rangeStart !== '' || rangeEnd !== '';

  const filterCounts = useMemo(
    () =>
      trades.reduce<Record<SignalFilter, number>>(
        (counts, trade) => {
          counts.ALL += 1;
          counts[recommendationFor(trade)] += 1;
          if (trade.is_simulated) counts.SIM += 1;
          return counts;
        },
        { ALL: 0, BUY: 0, SELL: 0, HOLD: 0, SIM: 0 },
      ),
    [trades],
  );

  const filteredTrades = useMemo(() => {
    if (isPreview) return trades;
    const fromMs = dateTimeValueToMs(rangeStart);
    const toMs = dateTimeValueToMs(rangeEnd);

    return trades.filter((trade) => {
      if (filter === 'SIM' && !trade.is_simulated) return false;
      if (filter !== 'ALL' && filter !== 'SIM' && recommendationFor(trade) !== filter) return false;

      const tradeTime = localTimestamp(trade.created_at);
      if (fromMs !== null && tradeTime < fromMs) return false;
      if (toMs !== null && tradeTime > toMs) return false;

      return true;
    });
  }, [filter, isPreview, rangeEnd, rangeStart, trades]);

  const visibleTrades = previewLimit ? filteredTrades.slice(0, previewLimit) : filteredTrades;

  function clearFilters() {
    setFilter('ALL');
    setRangeStart('');
    setRangeEnd('');
  }

  useEffect(() => {
    onLoadMoreRef.current = onLoadMore;
  }, [onLoadMore]);

  useEffect(() => {
    if (!isRangeOpen) return;

    function closeOnOutsideClick(event: MouseEvent) {
      const target = event.target;
      if (target instanceof Node && !rangePickerRef.current?.contains(target)) {
        setIsRangeOpen(false);
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setIsRangeOpen(false);
    }

    document.addEventListener('mousedown', closeOnOutsideClick);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [isRangeOpen]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) onLoadMoreRef.current();
      },
      { rootMargin: '0px 0px 120px 0px', threshold: 0 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="glass-panel flex h-full min-h-[360px] flex-col overflow-hidden rounded-2xl xl:min-h-0">
      {/* Header */}
      <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="pulse-dot h-2.5 w-2.5 rounded-full bg-positive" />
            <div>
              <p className="text-sm font-bold text-[var(--dashboard-text)]">Signal Feed</p>
              <p className="mt-0.5 text-xs text-[var(--dashboard-subtle)]">
                Headlines analyzed by the agent
              </p>
            </div>
          </div>
          <span className="rounded-full bg-[var(--dashboard-control)] px-3 py-1 text-[11px] font-semibold text-[var(--dashboard-subtle)]">
            {isPreview
              ? `${visibleTrades.length} latest`
              : totalCount !== undefined
                ? `${visibleTrades.length}/${totalCount} events`
                : `${visibleTrades.length}/${trades.length} events`}
          </span>
        </div>
        {!isPreview && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
              {FILTERS.map((item) => {
                const active = filter === item.key;
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() => setFilter(item.key)}
                    className={[
                      'inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[11px] font-semibold transition',
                      active
                        ? 'border-accent-border bg-accent-soft text-accent'
                        : 'border-line bg-surface text-muted hover:border-accent-border hover:text-accent',
                    ].join(' ')}
                  >
                    {item.label}
                    <span className="font-mono text-[10px] opacity-70">{filterCounts[item.key]}</span>
                  </button>
                );
              })}
            </div>
            <div ref={rangePickerRef} className="relative ml-auto shrink-0">
              <button
                type="button"
                onClick={() => setIsRangeOpen((open) => !open)}
                className={[
                  'inline-flex h-8 max-w-[230px] items-center gap-2 rounded-lg border bg-surface px-3 text-[11px] font-semibold transition hover:border-accent-border hover:text-accent',
                  hasRangeFilter
                    ? 'border-accent-border text-accent'
                    : 'border-line text-secondary',
                ].join(' ')}
                aria-expanded={isRangeOpen}
              >
                <CalendarIcon />
                <span className="truncate">{rangeLabel(rangeStart, rangeEnd)}</span>
              </button>
              {isRangeOpen && (
                <div className="absolute right-0 top-full z-30 mt-2 w-80 rounded-xl border border-line bg-surface p-3 shadow-[var(--dashboard-shadow)]">
                  <div className="grid gap-2">
                    <label>
                      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
                        From
                      </span>
                      <input
                        type="datetime-local"
                        value={rangeStart}
                        onChange={(event) => setRangeStart(event.target.value)}
                        className="h-9 w-full rounded-lg border border-line bg-surface-2 px-2.5 text-xs text-primary outline-none transition focus:border-accent-border"
                      />
                    </label>
                    <label>
                      <span className="mb-1 block text-[10px] font-semibold uppercase tracking-wide text-muted">
                        To
                      </span>
                      <input
                        type="datetime-local"
                        value={rangeEnd}
                        onChange={(event) => setRangeEnd(event.target.value)}
                        className="h-9 w-full rounded-lg border border-line bg-surface-2 px-2.5 text-xs text-primary outline-none transition focus:border-accent-border"
                      />
                    </label>
                  </div>
                  <div className="mt-3 flex items-center justify-between gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        setRangeStart('');
                        setRangeEnd('');
                      }}
                      disabled={!hasRangeFilter}
                      className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-secondary transition hover:border-accent-border hover:text-accent disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      Clear range
                    </button>
                    <button
                      type="button"
                      onClick={() => setIsRangeOpen(false)}
                      className="rounded-lg border border-accent-border bg-accent-soft px-3 py-1.5 text-xs font-semibold text-accent transition hover:brightness-110"
                    >
                      Apply
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Trade rows */}
      <div className="modern-scroll flex-1 overflow-y-auto p-3 pr-2">
        {trades.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-20 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface-2 text-muted">
              <svg
                className="h-5 w-5"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1.5}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zm9.75-4.5a1.125 1.125 0 00-1.125 1.125v10.125a1.125 1.125 0 002.25 0V9.75A1.125 1.125 0 0012.75 8.625zm4.875 2.25a1.125 1.125 0 00-1.125 1.125v7.875a1.125 1.125 0 002.25 0v-7.875a1.125 1.125 0 00-1.125-1.125z"
                />
              </svg>
            </div>
            <div>
              <p className="text-sm font-semibold text-primary">No signals yet</p>
              <p className="mt-1 text-xs text-muted">Waiting for market events…</p>
            </div>
          </div>
        )}

        {trades.length > 0 && visibleTrades.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-20 text-center">
            <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-surface-2 text-muted">
              <FilterIcon />
            </div>
            <div>
              <p className="text-sm font-semibold text-primary">No matching signals</p>
              <p className="mt-1 text-xs text-muted">Adjust the action or date-time range.</p>
            </div>
            <button
              type="button"
              onClick={clearFilters}
              className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-semibold text-secondary transition hover:border-accent-border hover:text-accent"
            >
              Clear filters
            </button>
          </div>
        )}

        {visibleTrades.map((trade) => {
          const articleUrl = safeArticleUrl(trade.article_url);
          const pathLabel = decisionPathLabel(trade.decision_path);

          return (
            <div
              key={trade.id}
              role="button"
              tabIndex={0}
              onClick={() => onTradeSelect(trade)}
              onKeyDown={(event) => {
                if (event.currentTarget !== event.target) return;
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  onTradeSelect(trade);
                }
              }}
              className={[
                'mb-2 w-full cursor-pointer rounded-xl border p-3.5 text-left transition-all duration-150',
                selectedId === trade.id
                  ? 'border-accent-border bg-selected shadow-sm'
                  : 'border-[var(--dashboard-border)] bg-[var(--dashboard-row)] hover:border-accent-border hover:bg-hover',
                newIds.has(trade.id) ? 'slide-in' : '',
              ].join(' ')}
            >
              <div className="mb-2.5 flex items-center gap-2">
                <span className="font-mono text-[13px] font-bold text-accent">{trade.ticker}</span>
                <span
                  className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${ACTION_STYLE[recommendationFor(trade)]}`}
                >
                  {executionBadge(trade)}
                </span>
                {pathLabel && (
                  <span className="rounded-full border border-line bg-surface px-2 py-0.5 text-[10px] font-semibold text-muted">
                    {pathLabel}
                  </span>
                )}
                {trade.is_simulated && (
                  <span className="rounded-full border border-warning-border bg-warning-soft px-2 py-0.5 text-[10px] font-semibold text-warning">
                    SIM
                  </span>
                )}
                <div className="ml-auto flex shrink-0 items-center gap-2">
                  {articleUrl && (
                    <a
                      href={articleUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      onClick={(event) => event.stopPropagation()}
                      aria-label={`Open source article for ${trade.ticker}`}
                      title="Open source article"
                      className="flex h-7 w-7 items-center justify-center rounded-lg border border-line bg-surface text-muted transition hover:border-accent-border hover:text-accent"
                    >
                      <ExternalLinkIcon />
                    </a>
                  )}
                  <span className="text-[11px] text-muted">
                    {new Date(trade.created_at).toLocaleString([], {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                </div>
              </div>

              <p className="mb-3 line-clamp-2 text-xs leading-relaxed text-secondary">
                {unescapeHtml(trade.headline)}
              </p>

              <div className="flex items-center gap-2">
                <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11px]">
                  <span className="text-muted">sentiment</span>
                  <span
                    className={`font-mono font-semibold ${trade.sentiment_score >= 0 ? 'text-positive' : 'text-negative'}`}
                  >
                    {trade.sentiment_score >= 0 ? '+' : ''}
                    {trade.sentiment_score.toFixed(2)}
                  </span>
                </div>
                <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11px]">
                  <span className="text-muted">conf</span>
                  <span className="font-mono font-semibold text-accent">
                    {(displayConfidence(trade) * 100).toFixed(0)}%
                  </span>
                </div>
              </div>
            </div>
          );
        })}

        {/* Infinite scroll sentinel */}
        {!isPreview && (
          <div ref={sentinelRef} className="flex items-center justify-center px-4 py-4">
            {isLoadingMore && (
              <div className="flex items-center gap-2 text-xs text-muted">
                <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-line border-t-muted" />
                Loading more signals…
              </div>
            )}
            {!isLoadingMore && !hasMore && trades.length > 0 && (
              <span className="text-[11px] text-muted opacity-50">
                You've reached the beginning
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function ExternalLinkIcon() {
  return (
    <svg
      className="h-3.5 w-3.5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M14 3h7v7" />
      <path d="M10 14 21 3" />
      <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
    </svg>
  );
}

function CalendarIcon() {
  return (
    <svg
      className="h-3.5 w-3.5 shrink-0"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M8 2v4" />
      <path d="M16 2v4" />
      <rect x="3" y="4" width="18" height="18" rx="2" />
      <path d="M3 10h18" />
    </svg>
  );
}

function FilterIcon() {
  return (
    <svg
      className="h-5 w-5"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 5h18" />
      <path d="M6 12h12" />
      <path d="M10 19h4" />
    </svg>
  );
}
