/**
 * PipelinePage — the /pipeline view.
 *
 * The same React Flow graph is used for both routes:
 *   - /pipeline shows the live aggregate processing pipeline.
 *   - /pipeline/:id hydrates that same graph with one signal's trace data.
 */
import React, { useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Panel,
  Edge,
  Node,
  NodeProps,
  Handle,
  Position,
  MarkerType,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  DashboardStats,
  Trade,
  DecisionTrace,
  PersonaOpinion,
  ArticleQuality,
  RiskGateTrace,
} from '@/lib/types';

// =============================================================================
// Shared presentational helpers
// =============================================================================

type Tone = 'neutral' | 'positive' | 'negative' | 'accent' | 'warn';
type PipelineMode = 'aggregate' | 'signal';

function toneClasses(tone: Tone): string {
  switch (tone) {
    case 'positive':
      return 'border-positive-border bg-positive-soft text-positive';
    case 'negative':
      return 'border-negative-border bg-negative-soft text-negative';
    case 'warn':
      return 'border-amber-500/30 bg-amber-500/10 text-amber-500';
    case 'accent':
      return 'border-accent-border bg-accent-soft text-accent';
    default:
      return 'border-line bg-surface-2 text-secondary';
  }
}

function toneRing(tone: Tone): string {
  switch (tone) {
    case 'positive':
      return 'border-positive/60';
    case 'negative':
      return 'border-negative/60';
    case 'warn':
      return 'border-amber-500/60';
    case 'accent':
      return 'border-accent/60';
    default:
      return 'border-[var(--dashboard-border)]';
  }
}

function Pill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: Tone }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${toneClasses(
        tone,
      )}`}
    >
      {children}
    </span>
  );
}

function KV({ label, value }: { label: string; value: React.ReactNode }) {
  if (value === undefined || value === null || value === '') return null;
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-[11px] text-muted">{label}</span>
      <span className="min-w-0 break-words text-right font-mono text-xs font-semibold text-primary">
        {value}
      </span>
    </div>
  );
}

function ConvictionBar({ value, tone }: { value: number; tone: Tone }) {
  const barTone =
    tone === 'positive' ? 'bg-positive' : tone === 'negative' ? 'bg-negative' : 'bg-amber-500';
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className={`h-full rounded-full ${barTone}`}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

function actionTone(action?: string | null): Tone {
  if (action === 'BUY' || action === 'BULLISH') return 'positive';
  if (action === 'SELL' || action === 'BEARISH') return 'negative';
  return 'warn';
}

function pct(n?: number | null, digits = 0): string | undefined {
  if (n === undefined || n === null || Number.isNaN(n)) return undefined;
  return `${(n * 100).toFixed(digits)}%`;
}

function money(n?: number | null): string | undefined {
  if (n === undefined || n === null || Number.isNaN(n)) return undefined;
  return `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function dateTime(value?: string | null): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return undefined;
  return date.toLocaleString();
}

const ICON = (paths: React.ReactNode) => (
  <svg
    width="20"
    height="20"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
  >
    {paths}
  </svg>
);

const PipelineIcons = {
  ingestion: ICON(
    <>
      <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8l-4 4v14a2 2 0 0 0 2 2z" />
      <path d="M14 2v4a2 2 0 0 0 2 2h4" />
    </>,
  ),
  stream: ICON(<polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />),
  prescreen: ICON(<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />),
  committee: ICON(
    <>
      <circle cx="9" cy="7" r="4" />
      <path d="M3 21v-2a4 4 0 0 1 4-4h4a4 4 0 0 1 4 4v2" />
      <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      <path d="M21 21v-2a4 4 0 0 0-3-3.87" />
    </>,
  ),
  risk: ICON(<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />),
  trader: ICON(
    <>
      <line x1="12" y1="2" x2="12" y2="22" />
      <line x1="17" y1="5" x2="7" y2="5" />
      <line x1="17" y1="19" x2="7" y2="19" />
      <polyline points="15 9 9 12 15 15" />
    </>,
  ),
  database: ICON(
    <>
      <ellipse cx="12" cy="5" rx="9" ry="3" />
      <path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
      <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
    </>,
  ),
};

// =============================================================================
// Trace normalization
// =============================================================================

interface NewsTrace {
  source?: string;
  summary?: string;
  headline?: string;
  article_url?: string;
  published_at?: string;
  is_simulated?: boolean;
}

interface MarketContext {
  price?: number;
  day_change_pct?: number;
  position?: { qty?: number; side?: string };
  account?: { buying_power?: number };
  technical_indicators_unavailable_reason?: string;
}

interface ExecutionTrace {
  action?: string;
  status?: string;
  order_id?: string;
  quantity?: number;
  limit_price?: number;
  filled_avg_price?: number;
  fill_status?: string;
  bracket_orders?: { entry_price?: number; stop_loss_price?: number; take_profit_price?: number };
  price_move_gate?: { blocked?: boolean; move_pct?: number };
  execution_plan?: { sizing_scale?: number; sizing_method?: string };
}

interface EnhancedFeatures {
  activations?: Array<{ feature?: string; activated?: boolean }>;
  total_features_activated?: number;
}

function normalizeTrace(trade: Trade): DecisionTrace {
  const dt = trade.decision_trace;
  if (Array.isArray(dt)) return { committee_debate: dt };
  return (dt ?? {}) as DecisionTrace;
}

// =============================================================================
// Shared graph
// =============================================================================

type PipelineNodeData = {
  label: string;
  icon: React.ReactNode;
  value?: string | number;
  subValue?: string;
  headline?: string;
  timestamp?: string;
  statusLabel?: string;
  statusTone?: Tone;
  tone: Tone;
  isActive?: boolean;
  isProcessing?: boolean;
  isSelected?: boolean;
  detailTitle: string;
  detailSubtitle?: string;
  detailBody: React.ReactNode;
};

function PipelineNode({ data, isConnectable }: NodeProps<Node<PipelineNodeData>>) {
  return (
    <div
      className={`relative min-h-[98px] w-[255px] rounded-xl border p-3.5 shadow-[var(--dashboard-shadow)] backdrop-blur-md transition-all duration-300 ${toneRing(
        data.tone,
      )} ${
        data.isActive
          ? 'bg-accent/10 shadow-[0_0_20px_rgba(var(--accent-rgb),0.2)]'
          : 'bg-[var(--dashboard-card)]'
      } ${data.isSelected ? 'ring-2 ring-accent ring-offset-2 ring-offset-[var(--dashboard-bg)]' : ''}`}
    >
      <Handle
        type="target"
        position={Position.Top}
        isConnectable={isConnectable}
        className="h-2 w-2 border-none bg-muted"
      />

      <div className="flex items-start gap-3">
        <div
          className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-xl border ${
            data.isActive
              ? 'border-accent-border bg-accent text-white'
              : toneClasses(data.tone)
          } transition-colors duration-300`}
        >
          {data.icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex min-w-0 items-start justify-between gap-2">
            <p className="min-w-0 break-words text-sm font-bold leading-tight text-[var(--dashboard-text)]">
              {data.label}
            </p>
            {data.statusLabel && (
              <span className="shrink-0">
                <Pill tone={data.statusTone}>{data.statusLabel}</Pill>
              </span>
            )}
          </div>

          {data.value !== undefined ? (
            <p className="mt-1 font-mono text-xs font-semibold text-accent">
              {data.value}{' '}
              <span className="text-[10px] font-normal text-[var(--dashboard-subtle)]">
                {data.subValue}
              </span>
            </p>
          ) : data.subValue ? (
            <p className="mt-1 text-[11px] leading-snug text-[var(--dashboard-subtle)]">
              {data.subValue}
            </p>
          ) : null}

          {data.headline && (
            <p
              className="mt-2 text-xs font-medium leading-snug text-primary"
              style={{
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
              }}
            >
              {data.headline}
            </p>
          )}
          {data.timestamp && (
            <p className="mt-1 truncate font-mono text-[10px] text-[var(--dashboard-subtle)]">
              {data.timestamp}
            </p>
          )}
        </div>
      </div>

      {data.isProcessing && (
        <div className="absolute -bottom-1 -right-1 flex h-4 w-4 items-center justify-center">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-accent" />
        </div>
      )}

      <Handle
        type="source"
        position={Position.Bottom}
        isConnectable={isConnectable}
        className="h-2 w-2 border-none bg-muted"
      />
    </div>
  );
}

const pipelineNodeTypes = { custom: PipelineNode };

const nodePositions: Record<string, { x: number; y: number }> = {
  ingestion: { x: 360, y: 0 },
  stream: { x: 360, y: 140 },
  prescreen: { x: 360, y: 280 },
  committee: { x: 360, y: 420 },
  risk: { x: 360, y: 560 },
  trader: { x: 90, y: 710 },
  database: { x: 630, y: 710 },
};

const edgeLabel = {
  labelBgStyle: { fill: 'var(--dashboard-bg)' },
  labelStyle: { fill: 'var(--dashboard-text)', fontSize: 10, fontWeight: 600 },
};

const baseEdges: Edge[] = [
  { id: 'e-ingestion-stream', source: 'ingestion', target: 'stream', animated: true },
  { id: 'e-stream-prescreen', source: 'stream', target: 'prescreen', animated: true },
  {
    id: 'e-prescreen-committee',
    source: 'prescreen',
    target: 'committee',
    animated: true,
    label: 'tradeable',
    style: { stroke: 'var(--accent)' },
    ...edgeLabel,
  },
  {
    id: 'e-prescreen-database',
    source: 'prescreen',
    target: 'database',
    animated: true,
    label: 'filtered',
    style: { stroke: 'var(--muted)' },
    ...edgeLabel,
  },
  { id: 'e-committee-risk', source: 'committee', target: 'risk', animated: true },
  {
    id: 'e-risk-trader',
    source: 'risk',
    target: 'trader',
    animated: true,
    label: 'BUY / SELL',
    style: { stroke: 'var(--positive)' },
    ...edgeLabel,
  },
  {
    id: 'e-risk-database',
    source: 'risk',
    target: 'database',
    animated: true,
    label: 'HOLD',
    style: { stroke: 'var(--muted)' },
    ...edgeLabel,
  },
  { id: 'e-trader-database', source: 'trader', target: 'database', animated: true },
];

function node(
  id: keyof typeof nodePositions,
  data: PipelineNodeData,
  selectedNodeId: string | null,
): Node<PipelineNodeData> {
  return {
    id,
    type: 'custom',
    position: nodePositions[id],
    data: { ...data, isSelected: selectedNodeId === id },
    draggable: false,
  };
}

function mutedDetail(message: string) {
  return <p className="text-xs leading-relaxed text-muted">{message}</p>;
}

function aggregateNodes({
  stats,
  trades,
  isPulsing,
  lastTradeType,
  selectedNodeId,
}: {
  stats: DashboardStats | null;
  trades: Trade[];
  isPulsing: boolean;
  lastTradeType: 'BUY' | 'SELL' | 'HOLD' | null;
  selectedNodeId: string | null;
}): Node<PipelineNodeData>[] {
  const base = [
    node(
      'ingestion',
      {
        label: 'News Ingestion',
        subValue: 'Alpaca news + backfill',
        icon: PipelineIcons.ingestion,
        tone: 'accent',
        detailTitle: 'News Ingestion',
        detailSubtitle: 'Live aggregate',
        detailBody: mutedDetail('Incoming articles enter the processing stream before any signal-specific context is attached to this graph.'),
      },
      selectedNodeId,
    ),
    node(
      'stream',
      {
        label: 'Event Stream',
        subValue: 'Valkey / Redis',
        icon: PipelineIcons.stream,
        tone: 'neutral',
        detailTitle: 'Event Stream',
        detailSubtitle: 'Live aggregate',
        detailBody: mutedDetail('The stream coordinates async pipeline work before deterministic filtering and AI analysis.'),
      },
      selectedNodeId,
    ),
    node(
      'prescreen',
      {
        label: 'Pre-Screen',
        value: stats?.preScreened ?? 0,
        subValue: 'filtered',
        icon: PipelineIcons.prescreen,
        tone: 'neutral',
        detailTitle: 'Pre-Screen',
        detailSubtitle: 'Live aggregate',
        detailBody: (
          <div className="space-y-2">
            <KV label="Pre-screened" value={stats?.preScreened ?? 0} />
            <KV label="Full debates" value={stats?.fullDebates ?? 0} />
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'committee',
      {
        label: 'AI Committee',
        value: stats?.fullDebates ?? 0,
        subValue: 'full debates',
        icon: PipelineIcons.committee,
        tone: 'accent',
        detailTitle: 'AI Committee',
        detailSubtitle: 'Live aggregate',
        detailBody: (
          <div className="space-y-2">
            <KV label="Full debates" value={stats?.fullDebates ?? 0} />
            <KV label="Average sentiment" value={stats ? stats.avgSentiment.toFixed(2) : undefined} />
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'risk',
      {
        label: 'Risk Gate',
        value: stats?.riskGated ?? 0,
        subValue: 'held / blocked',
        icon: PipelineIcons.risk,
        tone: 'warn',
        detailTitle: 'Risk Gate',
        detailSubtitle: 'Live aggregate',
        detailBody: (
          <div className="space-y-2">
            <KV label="Risk gated" value={stats?.riskGated ?? 0} />
            <KV label="Analyzed" value={stats?.analyzed ?? trades.length} />
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'trader',
      {
        label: 'Execution',
        value: stats?.executed ?? 0,
        subValue: 'orders filled',
        icon: PipelineIcons.trader,
        tone: 'positive',
        detailTitle: 'Execution',
        detailSubtitle: 'Live aggregate',
        detailBody: (
          <div className="space-y-2">
            <KV label="Executed" value={stats?.executed ?? 0} />
            <KV label="Buy orders" value={stats?.buyOrders ?? 0} />
            <KV label="Sell orders" value={stats?.sellOrders ?? 0} />
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'database',
      {
        label: 'Supabase Log',
        value: trades.length,
        subValue: 'signals loaded',
        icon: PipelineIcons.database,
        tone: 'neutral',
        detailTitle: 'Supabase Log',
        detailSubtitle: 'Live aggregate',
        detailBody: (
          <div className="space-y-2">
            <KV label="Loaded signals" value={trades.length} />
            <KV label="Total analyzed" value={stats?.analyzed} />
          </div>
        ),
      },
      selectedNodeId,
    ),
  ];

  return base.map((n) => {
    const isTraderHold = n.id === 'trader' && lastTradeType === 'HOLD';
    return {
      ...n,
      data: {
        ...n.data,
        isActive: isPulsing && !isTraderHold,
        isProcessing: isPulsing && !isTraderHold,
      },
    };
  });
}

function aggregateEdges({
  isPulsing,
  lastTradeType,
}: {
  isPulsing: boolean;
  lastTradeType: 'BUY' | 'SELL' | 'HOLD' | null;
}): Edge[] {
  return baseEdges.map((edge) => {
    let active = isPulsing;
    if (edge.id === 'e-risk-trader' && lastTradeType === 'HOLD') active = false;
    if (edge.id === 'e-trader-database' && lastTradeType === 'HOLD') active = false;
    if (edge.id === 'e-risk-database' && lastTradeType !== 'HOLD') active = false;
    return {
      ...edge,
      animated: true,
      style: {
        ...edge.style,
        strokeWidth: active ? 3 : 1.5,
        opacity: active ? 1 : 0.45,
      },
    };
  });
}

function signalPlaceholderNodes(selectedNodeId: string | null): Node<PipelineNodeData>[] {
  const placeholder = (id: keyof typeof nodePositions, label: string, icon: React.ReactNode, tone: Tone) =>
    node(
      id,
      {
        label,
        icon,
        tone,
        detailTitle: label,
        detailSubtitle: 'Signal trace',
        detailBody: mutedDetail('Signal trace data is loading.'),
      },
      selectedNodeId,
    );

  return [
    placeholder('ingestion', 'News Ingestion', PipelineIcons.ingestion, 'accent'),
    placeholder('stream', 'Event Stream', PipelineIcons.stream, 'neutral'),
    placeholder('prescreen', 'Pre-Screen', PipelineIcons.prescreen, 'neutral'),
    placeholder('committee', 'AI Committee', PipelineIcons.committee, 'neutral'),
    placeholder('risk', 'Risk Gate', PipelineIcons.risk, 'neutral'),
    placeholder('trader', 'Execution', PipelineIcons.trader, 'neutral'),
    placeholder('database', 'Supabase Log', PipelineIcons.database, 'neutral'),
  ];
}

function signalNodes({
  trade,
  selectedNodeId,
}: {
  trade: Trade;
  selectedNodeId: string | null;
}): Node<PipelineNodeData>[] {
  const trace = normalizeTrace(trade);
  const news = trace.news as NewsTrace | undefined;
  const market = trace.market_context as MarketContext | undefined;
  const quality = trace.article_quality as ArticleQuality | undefined;
  const committee =
    (Array.isArray(trace.committee_debate) ? trace.committee_debate : undefined) ??
    trade.committee_debate ??
    undefined;
  const pm = trace.portfolio_manager_decision ?? undefined;
  const risk = (trace.risk_gate ?? undefined) as RiskGateTrace | undefined;
  const execution = trace.execution as ExecutionTrace | undefined;
  const features = trace.enhanced_features as EnhancedFeatures | undefined;

  const isPreScreen = trade.decision_path === 'pre_screen' || !committee?.length;
  const finalAction = (trade.executed_action ?? trade.pm_recommendation ?? trade.trade_action) as string;
  const executed = Boolean(trade.executed_action && trade.order_id);
  const newsHeadline = news?.headline ?? trade.headline;
  const newsTimestamp = dateTime(news?.published_at ?? trade.created_at);
  const publishedAt = dateTime(news?.published_at);
  const recordedAt = dateTime(trace.recorded_at);
  const processingStartedAt = dateTime(trade.processing_started_at);
  const processingFinishedAt = dateTime(trade.processing_finished_at);
  const qualityGrade = (quality?.grade ?? '').toUpperCase();
  const qualityTone: Tone =
    qualityGrade === 'HIGH' ? 'positive' : qualityGrade === 'LOW' ? 'negative' : quality ? 'warn' : 'neutral';
  const riskCleared = risk?.should_trade === true || executed;
  const executionTone = executed ? actionTone(trade.executed_action) : 'warn';

  return [
    node(
      'ingestion',
      {
        label: 'News Ingestion',
        icon: PipelineIcons.ingestion,
        tone: 'accent',
        statusLabel: news?.is_simulated || trade.is_simulated ? 'sim' : undefined,
        statusTone: 'warn',
        headline: newsHeadline,
        timestamp: newsTimestamp,
        detailTitle: 'News Ingestion',
        detailSubtitle: `${trade.ticker} signal`,
        detailBody: (
          <div className="space-y-3">
            <p className="text-sm font-medium leading-relaxed text-primary">{newsHeadline}</p>
            {news?.summary && <p className="text-xs leading-relaxed text-muted">{news.summary}</p>}
            <div className="space-y-1.5">
              <KV label="Ticker" value={trade.ticker} />
              <KV label="Source" value={news?.source ?? trade.article_source} />
              <KV label="Published" value={publishedAt} />
              <KV label="Signal time" value={dateTime(trade.created_at)} />
              <KV label="Article ID" value={trade.article_id} />
            </div>
            {(news?.article_url ?? trade.article_url) && (
              <a
                href={(news?.article_url ?? trade.article_url) as string}
                target="_blank"
                rel="noreferrer"
                className="inline-flex text-xs font-semibold text-accent hover:underline"
              >
                Open source
              </a>
            )}
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'stream',
      {
        label: 'Event Stream',
        icon: PipelineIcons.stream,
        tone: 'neutral',
        statusLabel: trace.pipeline ? 'trace' : undefined,
        statusTone: 'accent',
        detailTitle: 'Event Stream',
        detailSubtitle: 'Processing context',
        detailBody: (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <KV label="Pipeline" value={trace.pipeline} />
              <KV label="Recorded" value={recordedAt} />
              <KV label="Started" value={processingStartedAt} />
              <KV label="Finished" value={processingFinishedAt} />
            </div>
            {market ? (
              <div className="space-y-1.5 rounded-lg bg-surface-2 px-3 py-2">
                <KV label="Price" value={money(market.price)} />
                <KV label="Day change" value={market.day_change_pct !== undefined ? `${market.day_change_pct.toFixed(2)}%` : undefined} />
                <KV label="Position" value={market.position ? `${market.position.side ?? 'flat'} · ${market.position.qty ?? 0}` : undefined} />
                <KV label="Buying power" value={money(market.account?.buying_power)} />
                {market.technical_indicators_unavailable_reason && (
                  <Pill tone="warn">technical indicators unavailable</Pill>
                )}
              </div>
            ) : (
              mutedDetail('No market context was stored for this signal.')
            )}
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'prescreen',
      {
        label: 'Pre-Screen',
        icon: PipelineIcons.prescreen,
        tone: qualityTone,
        statusLabel: qualityGrade ? (isPreScreen ? `${qualityGrade} stop` : qualityGrade) : isPreScreen ? 'stop' : 'passed',
        statusTone: isPreScreen ? 'warn' : qualityTone,
        detailTitle: 'Pre-Screen',
        detailSubtitle: isPreScreen ? 'Filtered before debate' : 'Passed to committee',
        detailBody: quality ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Quality score" value={pct(quality.score, 0)} />
              <KV label="Category" value={quality.category} />
              <KV label="Grade" value={quality.grade} />
              <KV label="Has summary" value={quality.has_summary === undefined ? undefined : quality.has_summary ? 'yes' : 'no'} />
            </div>
            {quality.reasons?.length ? (
              <ul className="space-y-1">
                {quality.reasons.map((reason, i) => (
                  <li key={i} className="flex gap-2 text-xs leading-relaxed text-muted">
                    <span className="text-accent">•</span>
                    <span>{reason}</span>
                  </li>
                ))}
              </ul>
            ) : null}
            {quality.flags?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {quality.flags.map((flag, i) => (
                  <Pill key={i} tone="warn">
                    {flag}
                  </Pill>
                ))}
              </div>
            ) : null}
          </div>
        ) : (
          mutedDetail('No article quality payload was stored for this signal.')
        ),
      },
      selectedNodeId,
    ),
    node(
      'committee',
      {
        label: 'AI Committee',
        icon: PipelineIcons.committee,
        tone: committee?.length ? 'accent' : 'neutral',
        statusLabel: committee?.length ? `${committee.length}` : 'skipped',
        statusTone: committee?.length ? 'accent' : 'neutral',
        detailTitle: 'AI Committee',
        detailSubtitle: pm?.action ? `Portfolio manager: ${pm.action}` : undefined,
        detailBody: (
          <div className="space-y-3">
            {committee?.length ? (
              committee.map((persona: PersonaOpinion, i: number) => {
                const tone = actionTone(persona.stance);
                return (
                  <div key={i} className="space-y-1.5 rounded-lg bg-surface-2 px-3 py-2">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-bold text-primary">{persona.name}</span>
                      <div className="flex items-center gap-1.5">
                        <Pill tone={tone}>{persona.stance}</Pill>
                        <span className="font-mono text-[10px] text-muted">
                          {pct(persona.conviction, 0)}
                        </span>
                      </div>
                    </div>
                    <ConvictionBar value={persona.conviction ?? 0} tone={tone} />
                    <p className="text-xs leading-relaxed text-muted">
                      {persona.view || persona.reasoning}
                    </p>
                    {persona.reasoning && persona.reasoning !== persona.view && (
                      <p className="text-[11px] leading-relaxed text-muted">{persona.reasoning}</p>
                    )}
                    {persona.model && <p className="text-[10px] text-muted">model · {persona.model}</p>}
                  </div>
                );
              })
            ) : (
              mutedDetail('This signal stopped before the LLM committee.')
            )}
            {pm && (
              <div className="space-y-1.5 rounded-lg border border-line px-3 py-2">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs font-bold text-primary">Portfolio manager</span>
                  <Pill tone={actionTone(pm.action)}>{pm.action ?? 'HOLD'}</Pill>
                </div>
                <div className="grid grid-cols-2 gap-x-6">
                  <KV label="Sentiment" value={pm.sentiment?.toFixed(2)} />
                  <KV label="Confidence" value={pct(pm.confidence, 0)} />
                </div>
                {pm.reasoning && <p className="text-xs leading-relaxed text-muted">{pm.reasoning}</p>}
                {pm.model && <p className="text-[10px] text-muted">model · {pm.model}</p>}
              </div>
            )}
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'risk',
      {
        label: 'Risk Gate',
        icon: PipelineIcons.risk,
        tone: riskCleared ? 'positive' : 'warn',
        statusLabel: riskCleared ? 'cleared' : 'held',
        statusTone: riskCleared ? 'positive' : 'warn',
        detailTitle: 'Risk Gate',
        detailSubtitle: riskCleared ? 'Order allowed' : 'Order held',
        detailBody: (
          <div className="space-y-3">
            {(risk?.reason || trade.gate_reason) && (
              <p className="text-xs leading-relaxed text-muted">{risk?.reason ?? trade.gate_reason}</p>
            )}
            {risk?.checks && (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(risk.checks).map(([key, value]) => (
                  <Pill key={key} tone={value ? 'positive' : 'negative'}>
                    {value ? 'pass' : 'fail'} {key.replace(/_/g, ' ')}
                  </Pill>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Action" value={risk?.inputs?.action ?? finalAction} />
              <KV label="Confidence" value={pct(risk?.inputs?.confidence ?? trade.confidence_score, 0)} />
              <KV label="Calibrated" value={pct(risk?.committee_metrics?.calibrated_confidence ?? trade.calibrated_confidence, 0)} />
              <KV label="Confidence cap" value={pct(risk?.committee_metrics?.confidence_cap ?? trade.confidence_cap, 0)} />
              <KV label="Agreement" value={pct(risk?.committee_metrics?.agreement, 0)} />
              <KV label="Risk level" value={risk?.committee_metrics?.risk_level} />
            </div>
            {risk?.committee_metrics?.cap_reasons?.length ? (
              <p className="text-[11px] leading-relaxed text-muted">
                caps · {risk.committee_metrics.cap_reasons.join(' · ')}
              </p>
            ) : null}
            {risk?.blockers?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {risk.blockers.map((blocker, i) => (
                  <Pill key={i} tone="negative">
                    {blocker}
                  </Pill>
                ))}
              </div>
            ) : null}
          </div>
        ),
      },
      selectedNodeId,
    ),
    node(
      'trader',
      {
        label: 'Execution',
        icon: PipelineIcons.trader,
        tone: executionTone,
        statusLabel: executed ? execution?.fill_status ?? execution?.status ?? 'sent' : 'hold',
        statusTone: executionTone,
        detailTitle: 'Execution',
        detailSubtitle: executed ? 'Order submitted' : 'No order placed',
        detailBody: executed && execution ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Action" value={execution.action ?? trade.executed_action} />
              <KV label="Quantity" value={execution.quantity ?? trade.quantity} />
              <KV label="Limit" value={money(execution.limit_price)} />
              <KV label="Filled @" value={money(execution.filled_avg_price)} />
              <KV label="Order" value={(execution.order_id ?? trade.order_id)?.slice(0, 8)} />
              <KV label="Status" value={execution.fill_status ?? execution.status ?? trade.order_status} />
              <KV
                label="Sizing"
                value={
                  execution.execution_plan?.sizing_scale !== undefined
                    ? `${pct(execution.execution_plan.sizing_scale, 0)} · ${execution.execution_plan.sizing_method ?? ''}`
                    : undefined
                }
              />
            </div>
            {execution.bracket_orders && (
              <div className="flex flex-wrap gap-1.5">
                {execution.bracket_orders.entry_price && (
                  <Pill tone="accent">entry {money(execution.bracket_orders.entry_price)}</Pill>
                )}
                {execution.bracket_orders.take_profit_price && (
                  <Pill tone="positive">TP {money(execution.bracket_orders.take_profit_price)}</Pill>
                )}
                {execution.bracket_orders.stop_loss_price && (
                  <Pill tone="negative">SL {money(execution.bracket_orders.stop_loss_price)}</Pill>
                )}
              </div>
            )}
            {execution.price_move_gate && (
              <p className="text-[11px] text-muted">
                price-move gate · {execution.price_move_gate.blocked ? 'blocked' : 'passed'}
                {execution.price_move_gate.move_pct !== undefined
                  ? ` (${pct(execution.price_move_gate.move_pct, 2)})`
                  : ''}
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs leading-relaxed text-muted">
            {trade.gate_reason ??
              risk?.reason ??
              'The decision did not clear every gate, so the signal was logged without placing an order.'}
          </p>
        ),
      },
      selectedNodeId,
    ),
    node(
      'database',
      {
        label: 'Supabase Log',
        icon: PipelineIcons.database,
        tone: 'neutral',
        statusLabel: trade.decision_path ? trade.decision_path.replace(/_/g, ' ') : undefined,
        statusTone: 'neutral',
        detailTitle: 'Supabase Log',
        detailSubtitle: 'Stored signal record',
        detailBody: (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <KV label="Signal ID" value={trade.id} />
              <KV label="Decision path" value={trade.decision_path?.replace(/_/g, ' ')} />
              <KV label="Trade action" value={trade.trade_action} />
              <KV label="PM recommendation" value={trade.pm_recommendation} />
              <KV label="Executed action" value={trade.executed_action} />
              <KV label="Order ID" value={trade.order_id} />
            </div>
            {features?.activations?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {features.activations.map((feature, i) => (
                  <Pill key={i} tone={feature.activated ? 'positive' : 'neutral'}>
                    {feature.activated ? 'on' : 'off'} {(feature.feature ?? '').replace(/_/g, ' ')}
                  </Pill>
                ))}
              </div>
            ) : null}
          </div>
        ),
      },
      selectedNodeId,
    ),
  ];
}

function signalEdges(trade: Trade): Edge[] {
  const trace = normalizeTrace(trade);
  const committee =
    (Array.isArray(trace.committee_debate) ? trace.committee_debate : undefined) ??
    trade.committee_debate ??
    undefined;
  const isPreScreen = trade.decision_path === 'pre_screen' || !committee?.length;
  const executed = Boolean(trade.executed_action && trade.order_id);

  return baseEdges.map((edge) => {
    let active = true;
    if (edge.id === 'e-prescreen-committee') active = !isPreScreen;
    if (edge.id === 'e-committee-risk') active = !isPreScreen;
    if (edge.id === 'e-risk-trader') active = executed;
    if (edge.id === 'e-trader-database') active = executed;
    if (edge.id === 'e-prescreen-database') active = isPreScreen;
    if (edge.id === 'e-risk-database') active = !executed && !isPreScreen;

    return {
      ...edge,
      animated: active,
      style: {
        ...edge.style,
        strokeWidth: active ? 3 : 1.5,
        opacity: active ? 1 : 0.22,
      },
    };
  });
}

function inactiveSignalEdges(): Edge[] {
  return baseEdges.map((edge) => ({
    ...edge,
    animated: false,
    style: {
      ...edge.style,
      strokeWidth: 1.5,
      opacity: 0.22,
    },
  }));
}

function DetailPanel({
  selectedNode,
  mode,
  trade,
  loading,
  error,
  onClose,
  onBack,
}: {
  selectedNode: Node<PipelineNodeData> | null;
  mode: PipelineMode;
  trade: Trade | null;
  loading: boolean;
  error: string | null;
  onClose: () => void;
  onBack?: () => void;
}) {
  return (
    <Panel position="top-right" className="m-3 w-[360px] max-w-[calc(100vw-2rem)]">
      <div className="max-h-[calc(100vh-220px)] overflow-auto rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-4 shadow-[var(--dashboard-shadow)] backdrop-blur-md">
        <div className="mb-3 flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex min-w-0 items-center gap-2">
              <span className="text-accent">{selectedNode?.data.icon ?? PipelineIcons.database}</span>
              <h3 className="min-w-0 break-words text-sm font-bold text-[var(--dashboard-text)]">
                {selectedNode?.data.detailTitle ?? (mode === 'signal' ? 'Signal Pipeline' : 'Pipeline')}
              </h3>
            </div>
            {selectedNode?.data.detailSubtitle && (
              <p className="mt-0.5 text-[11px] text-muted">{selectedNode.data.detailSubtitle}</p>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg border border-line bg-surface text-muted transition hover:bg-hover hover:text-primary"
            aria-label="Close details"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </svg>
          </button>
        </div>

        {loading ? (
          <div className="flex items-center gap-3 py-6 text-sm text-muted">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            Loading signal trace
          </div>
        ) : error ? (
          <div className="space-y-3">
            <p className="text-sm font-semibold text-primary">Signal not found</p>
            <p className="text-xs leading-relaxed text-muted">{error}</p>
            {onBack && (
              <button
                type="button"
                onClick={onBack}
                className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary transition hover:bg-hover"
              >
                Back to signals
              </button>
            )}
          </div>
        ) : selectedNode ? (
          selectedNode.data.detailBody
        ) : trade ? (
          <div className="space-y-2">
            <KV label="Ticker" value={trade.ticker} />
            <KV label="Signal time" value={dateTime(trade.created_at)} />
          </div>
        ) : (
          mutedDetail('Select a pipeline stage to inspect the latest available details.')
        )}
      </div>
    </Panel>
  );
}

function PipelineFlow({
  stats,
  trades,
  newIds,
  mode,
  trade,
  loading,
  error,
  onBack,
}: {
  stats: DashboardStats | null;
  trades: Trade[];
  newIds: Set<string>;
  mode: PipelineMode;
  trade: Trade | null;
  loading: boolean;
  error: string | null;
  onBack?: () => void;
}) {
  const [isPulsing, setIsPulsing] = useState(false);
  const [lastTradeType, setLastTradeType] = useState<'BUY' | 'SELL' | 'HOLD' | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(mode === 'signal' ? 'ingestion' : null);

  useEffect(() => {
    if (mode !== 'aggregate') return;
    if (newIds.size > 0) {
      setIsPulsing(true);
      const recentTrade = trades.find((t) => newIds.has(t.id));
      if (recentTrade) setLastTradeType(recentTrade.trade_action);
      const timer = setTimeout(() => setIsPulsing(false), 2000);
      return () => clearTimeout(timer);
    }
  }, [mode, newIds, trades]);

  useEffect(() => {
    setSelectedNodeId(mode === 'signal' ? 'ingestion' : null);
  }, [mode, trade?.id]);

  const graphNodes = useMemo(() => {
    if (mode === 'signal') {
      return trade
        ? signalNodes({ trade, selectedNodeId })
        : signalPlaceholderNodes(selectedNodeId);
    }
    return aggregateNodes({ stats, trades, isPulsing, lastTradeType, selectedNodeId });
  }, [mode, trade, selectedNodeId, stats, trades, isPulsing, lastTradeType]);

  const graphEdges = useMemo(() => {
    if (mode === 'signal') return trade ? signalEdges(trade) : inactiveSignalEdges();
    return aggregateEdges({ isPulsing, lastTradeType });
  }, [mode, trade, isPulsing, lastTradeType]);

  const selectedNode = useMemo(
    () => graphNodes.find((n) => n.id === selectedNodeId) ?? null,
    [graphNodes, selectedNodeId],
  );

  const showPanel = Boolean(selectedNodeId) || loading || Boolean(error);
  const signalTitle = trade ? `${trade.ticker} pipeline` : 'Signal pipeline';
  const signalSubtitle = trade ? trade.headline : 'Loading signal trace';

  return (
    <div className="w-full space-y-4">
      {mode === 'signal' && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] px-4 py-3 shadow-[var(--dashboard-shadow)]">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-lg font-extrabold tracking-tight text-[var(--dashboard-text)]">
                {signalTitle}
              </h2>
              {trade && <Pill tone={actionTone(trade.executed_action ?? trade.pm_recommendation ?? trade.trade_action)}>{trade.executed_action ?? trade.pm_recommendation ?? trade.trade_action}</Pill>}
              {trade?.is_simulated && <Pill tone="warn">simulated</Pill>}
            </div>
            <p className="mt-1 max-w-3xl truncate text-sm text-muted">{signalSubtitle}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {onBack && (
              <button
                type="button"
                onClick={onBack}
                className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary transition hover:bg-hover"
              >
                Signals
              </button>
            )}
            <button
              type="button"
              onClick={() => navigator.clipboard?.writeText(window.location.href)}
              className="flex h-8 w-8 items-center justify-center rounded-lg border border-line bg-surface text-secondary transition hover:bg-hover"
              aria-label="Copy pipeline link"
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
              </svg>
            </button>
          </div>
        </div>
      )}

      <div className="relative h-full min-h-[760px] w-full overflow-hidden rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-bg)]">
        <ReactFlow
          nodes={graphNodes}
          edges={graphEdges}
          onNodeClick={(_, clickedNode) => setSelectedNodeId(clickedNode.id)}
          nodeTypes={pipelineNodeTypes}
          fitView
          fitViewOptions={{ padding: 0.16, maxZoom: 1 }}
          className="bg-transparent"
          minZoom={0.35}
          maxZoom={1.5}
          nodesDraggable={false}
          nodesConnectable={false}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            type: 'smoothstep',
            markerEnd: {
              type: MarkerType.ArrowClosed,
              width: 15,
              height: 15,
              color: 'var(--dashboard-subtle)',
            },
          }}
        >
          <Background gap={16} size={1} color="var(--dashboard-border)" />
          <Controls className="border-line bg-surface-2 fill-primary text-primary" showInteractive={false} />
          {showPanel && (
            <DetailPanel
              selectedNode={selectedNode}
              mode={mode}
              trade={trade}
              loading={loading}
              error={error}
              onClose={() => setSelectedNodeId(null)}
              onBack={onBack}
            />
          )}
        </ReactFlow>
      </div>
    </div>
  );
}

// =============================================================================
// Entry point
// =============================================================================

export interface PipelinePageProps {
  stats: DashboardStats | null;
  trades: Trade[];
  newIds: Set<string>;
  signalMode?: boolean;
  signalTrade?: Trade | null;
  signalLoading?: boolean;
  signalError?: string | null;
  onBack?: () => void;
}

export default function PipelinePage(props: PipelinePageProps) {
  const mode: PipelineMode = props.signalMode ? 'signal' : 'aggregate';

  return (
    <PipelineFlow
      stats={props.stats}
      trades={props.trades}
      newIds={props.newIds}
      mode={mode}
      trade={props.signalTrade ?? null}
      loading={Boolean(props.signalLoading)}
      error={props.signalError ?? null}
      onBack={props.onBack}
    />
  );
}
