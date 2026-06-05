/**
 * SignalPipeline — per-signal pipeline replay, rendered in React Flow.
 *
 * Reuses the dashboard's pipeline node-graph language, but in single-signal
 * mode: every stage the signal actually passed through becomes a node, lit by
 * its real outcome (green = cleared/filled, amber = held, red = blocked, grey
 * = skipped). The path reflects exactly what ran — a pre-screened HOLD stops
 * early; a full-debate BUY runs the whole chain to execution. Click any node
 * to open its full detail (committee reasoning, risk checks, bracket orders…).
 *
 * Source of truth is the `decision_trace` JSONB stored in Supabase — permanent,
 * in-house, no external dependency.
 */
import { useMemo, useState } from 'react';
import {
  ReactFlow,
  Background,
  Controls,
  Panel,
  Handle,
  Position,
  MarkerType,
  Node,
  Edge,
  NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Trade,
  DecisionTrace,
  PersonaOpinion,
  ArticleQuality,
  RiskGateTrace,
} from '@/lib/types';

// ── Local shapes for trace sections typed as `unknown` in lib/types ──────────
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
  account?: { equity?: number; buying_power?: number };
  technical_indicators_unavailable_reason?: string;
}
interface BracketOrders {
  entry_price?: number;
  stop_loss_price?: number;
  take_profit_price?: number;
}
interface ExecutionTrace {
  action?: string;
  status?: string;
  order_id?: string;
  quantity?: number;
  limit_price?: number;
  filled_avg_price?: number;
  fill_status?: string;
  bracket_orders?: BracketOrders;
  price_move_gate?: { blocked?: boolean; move_pct?: number; threshold_pct?: number };
  execution_plan?: { sizing_scale?: number; sizing_method?: string; sizing_reasons?: string[] };
}
interface FeatureActivation {
  feature?: string;
  activated?: boolean;
}
interface EnhancedFeatures {
  activations?: FeatureActivation[];
  total_features_activated?: number;
}

type Tone = 'neutral' | 'positive' | 'negative' | 'accent' | 'warn';

// ── presentational helpers ───────────────────────────────────────────────────

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
      <span className="text-right font-mono text-xs font-semibold text-primary">{value}</span>
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
function normalizeTrace(trade: Trade): DecisionTrace {
  const dt = trade.decision_trace;
  if (Array.isArray(dt)) return { committee_debate: dt };
  return (dt ?? {}) as DecisionTrace;
}

// ── React Flow custom node ───────────────────────────────────────────────────

type StageNodeData = {
  icon: string;
  title: string;
  statusLabel?: string;
  statusTone: Tone;
  tone: Tone;
  isSelected: boolean;
};

function StageNode({ data }: NodeProps<Node<StageNodeData>>) {
  return (
    <div
      className={`relative min-w-[212px] cursor-pointer rounded-2xl border-2 bg-[var(--dashboard-card)] p-3.5 shadow-[var(--dashboard-shadow)] backdrop-blur-md transition-all duration-200 ${toneRing(
        data.tone,
      )} ${data.isSelected ? 'ring-2 ring-accent ring-offset-2 ring-offset-[var(--dashboard-bg)]' : ''}`}
    >
      <Handle type="target" position={Position.Top} className="h-2 w-2 border-none bg-muted" />
      <div className="flex items-center gap-3">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border text-base ${toneClasses(
            data.tone,
          )}`}
        >
          {data.icon}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-bold text-[var(--dashboard-text)]">{data.title}</p>
          {data.statusLabel && (
            <span className="mt-0.5 inline-block">
              <Pill tone={data.statusTone}>{data.statusLabel}</Pill>
            </span>
          )}
        </div>
      </div>
      <Handle type="source" position={Position.Bottom} className="h-2 w-2 border-none bg-muted" />
    </div>
  );
}

const nodeTypes = { stage: StageNode };

// ── stage model ──────────────────────────────────────────────────────────────

interface StageDef {
  icon: string;
  title: string;
  tone: Tone;
  status?: { label: string; tone: Tone };
  body: React.ReactNode;
}

export interface SignalPipelineProps {
  trade: Trade | null;
  loading: boolean;
  error: string | null;
  onBack: () => void;
}

export default function SignalPipeline({ trade, loading, error, onBack }: SignalPipelineProps) {
  const trace = useMemo<DecisionTrace>(() => (trade ? normalizeTrace(trade) : {}), [trade]);

  const stages = useMemo<StageDef[]>(() => {
    if (!trade) return [];
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
    const executed = Boolean(trade.executed_action && trade.order_id);
    const out: StageDef[] = [];

    // 1. News
    out.push({
      icon: '📰',
      title: 'News received',
      tone: 'accent',
      status:
        news?.is_simulated || trade.is_simulated ? { label: 'simulated', tone: 'warn' } : undefined,
      body: (
        <div className="space-y-2">
          <p className="text-sm font-medium text-primary">{news?.headline ?? trade.headline}</p>
          {news?.summary && <p className="text-xs leading-relaxed text-muted">{news.summary}</p>}
          <div className="flex flex-wrap items-center gap-1.5 pt-1">
            {(news?.source ?? trade.article_source) && (
              <Pill>{news?.source ?? trade.article_source}</Pill>
            )}
            {news?.published_at && <Pill>{new Date(news.published_at).toLocaleString()}</Pill>}
            {(news?.article_url ?? trade.article_url) && (
              <a
                href={(news?.article_url ?? trade.article_url) as string}
                target="_blank"
                rel="noreferrer"
                className="text-[10px] font-semibold uppercase tracking-wide text-accent hover:underline"
              >
                Source ↗
              </a>
            )}
          </div>
        </div>
      ),
    });

    // 2. Pre-screen
    if (quality) {
      const grade = (quality.grade ?? '').toUpperCase();
      const qTone: Tone = grade === 'HIGH' ? 'positive' : grade === 'LOW' ? 'negative' : 'warn';
      out.push({
        icon: '🔍',
        title: 'Pre-screen',
        tone: qTone,
        status: { label: isPreScreen ? `${grade || 'scored'} · stop` : grade || 'scored', tone: qTone },
        body: (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Quality score" value={pct(quality.score, 0)} />
              <KV label="Category" value={quality.category} />
            </div>
            {quality.reasons?.length ? (
              <ul className="space-y-1 pt-1">
                {quality.reasons.map((r, i) => (
                  <li key={i} className="flex gap-2 text-xs text-muted">
                    <span className="text-accent">•</span>
                    {r}
                  </li>
                ))}
              </ul>
            ) : null}
            {isPreScreen && (
              <p className="rounded-lg bg-surface-2 px-2.5 py-1.5 text-[11px] text-muted">
                Resolved deterministically — no LLM calls spent.
              </p>
            )}
          </div>
        ),
      });
    }

    // 3. Market context
    if (market) {
      out.push({
        icon: '📊',
        title: 'Market context',
        tone: 'neutral',
        body: (
          <div className="grid grid-cols-2 gap-x-6">
            <KV label="Price" value={money(market.price)} />
            <KV
              label="Day change"
              value={market.day_change_pct !== undefined ? `${market.day_change_pct.toFixed(2)}%` : undefined}
            />
            <KV
              label="Position"
              value={market.position ? `${market.position.side ?? 'flat'} · ${market.position.qty ?? 0}` : undefined}
            />
            <KV label="Buying power" value={money(market.account?.buying_power)} />
            {market.technical_indicators_unavailable_reason && (
              <div className="col-span-2 pt-1">
                <Pill tone="warn">technical indicators unavailable</Pill>
              </div>
            )}
          </div>
        ),
      });
    }

    // 4. Committee
    if (committee?.length) {
      out.push({
        icon: '🧠',
        title: 'AI committee',
        tone: 'accent',
        status: { label: `${committee.length} analysts`, tone: 'accent' },
        body: (
          <div className="space-y-3">
            {committee.map((p: PersonaOpinion, i: number) => {
              const t = actionTone(p.stance);
              return (
                <div key={i} className="space-y-1.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-bold text-primary">{p.name}</span>
                    <div className="flex items-center gap-1.5">
                      <Pill tone={t}>{p.stance}</Pill>
                      <span className="font-mono text-[10px] text-muted">{pct(p.conviction, 0)}</span>
                    </div>
                  </div>
                  <ConvictionBar value={p.conviction ?? 0} tone={t} />
                  <p className="text-xs leading-relaxed text-muted">{p.view}</p>
                </div>
              );
            })}
            {committee[0]?.model && (
              <p className="pt-1 text-[10px] text-muted">model · {committee[0].model}</p>
            )}
          </div>
        ),
      });
    }

    // 5. Portfolio manager
    if (pm) {
      out.push({
        icon: '🧑‍⚖️',
        title: 'Portfolio manager',
        tone: actionTone(pm.action),
        status: { label: pm.action ?? '—', tone: actionTone(pm.action) },
        body: (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Sentiment" value={pm.sentiment?.toFixed(2)} />
              <KV label="Confidence" value={pct(pm.confidence, 0)} />
            </div>
            {pm.reasoning && <p className="text-xs leading-relaxed text-muted">{pm.reasoning}</p>}
            {pm.model && <p className="text-[10px] text-muted">model · {pm.model}</p>}
          </div>
        ),
      });
    }

    // 6. Risk gate
    if (risk) {
      const should = risk.should_trade === true;
      const metrics = risk.committee_metrics;
      out.push({
        icon: '🛡️',
        title: 'Risk gate',
        tone: should ? 'positive' : 'warn',
        status: { label: should ? 'cleared' : 'held', tone: should ? 'positive' : 'warn' },
        body: (
          <div className="space-y-2">
            {risk.reason && <p className="text-xs leading-relaxed text-muted">{risk.reason}</p>}
            {risk.checks && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {Object.entries(risk.checks).map(([k, v]) => (
                  <Pill key={k} tone={v ? 'positive' : 'negative'}>
                    {v ? '✓' : '✕'} {k.replace(/_/g, ' ')}
                  </Pill>
                ))}
              </div>
            )}
            <div className="grid grid-cols-2 gap-x-6 pt-1">
              <KV label="Calibrated conf." value={pct(metrics?.calibrated_confidence, 0)} />
              <KV label="Confidence cap" value={pct(metrics?.confidence_cap, 0)} />
              <KV label="Agreement" value={pct(metrics?.agreement, 0)} />
              <KV label="Risk level" value={metrics?.risk_level} />
            </div>
            {metrics?.cap_reasons?.length ? (
              <p className="text-[11px] text-muted">caps · {metrics.cap_reasons.join(' · ')}</p>
            ) : null}
            {risk.blockers?.length ? (
              <div className="flex flex-wrap gap-1.5">
                {risk.blockers.map((b, i) => (
                  <Pill key={i} tone="negative">
                    {b}
                  </Pill>
                ))}
              </div>
            ) : null}
          </div>
        ),
      });
    }

    // 7. Execution OR HOLD terminal
    if (executed && execution) {
      const gate = execution.price_move_gate;
      const bracket = execution.bracket_orders;
      out.push({
        icon: '📈',
        title: 'Execution',
        tone: actionTone(execution.action),
        status: {
          label: execution.fill_status ?? execution.status ?? 'submitted',
          tone: execution.fill_status === 'filled' ? 'positive' : 'accent',
        },
        body: (
          <div className="space-y-2">
            <div className="grid grid-cols-2 gap-x-6">
              <KV label="Action" value={execution.action} />
              <KV label="Quantity" value={execution.quantity} />
              <KV label="Limit" value={money(execution.limit_price)} />
              <KV label="Filled @" value={money(execution.filled_avg_price)} />
              <KV
                label="Sizing"
                value={
                  execution.execution_plan?.sizing_scale !== undefined
                    ? `${pct(execution.execution_plan.sizing_scale, 0)} · ${execution.execution_plan.sizing_method ?? ''}`
                    : undefined
                }
              />
              <KV label="Order" value={execution.order_id?.slice(0, 8)} />
            </div>
            {bracket && (bracket.stop_loss_price || bracket.take_profit_price) && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {bracket.entry_price && <Pill tone="accent">entry {money(bracket.entry_price)}</Pill>}
                {bracket.take_profit_price && <Pill tone="positive">TP {money(bracket.take_profit_price)}</Pill>}
                {bracket.stop_loss_price && <Pill tone="negative">SL {money(bracket.stop_loss_price)}</Pill>}
              </div>
            )}
            {gate && (
              <p className="text-[11px] text-muted">
                price-move gate · {gate.blocked ? 'blocked' : 'passed'}
                {gate.move_pct !== undefined ? ` (${pct(gate.move_pct, 2)})` : ''}
              </p>
            )}
          </div>
        ),
      });
    } else {
      out.push({
        icon: '🟡',
        title: 'No order — HOLD',
        tone: 'warn',
        status: { label: 'hold', tone: 'warn' },
        body: (
          <p className="text-xs leading-relaxed text-muted">
            {trade.gate_reason ??
              risk?.reason ??
              'The decision did not clear every gate, so no order was placed. The signal is logged for audit and outcome labeling.'}
          </p>
        ),
      });
    }

    // 8. Enhanced features
    if (features?.activations?.length) {
      out.push({
        icon: '⚙️',
        title: 'Enhanced features',
        tone: 'neutral',
        status: {
          label: `${features.total_features_activated ?? features.activations.length} active`,
          tone: 'neutral',
        },
        body: (
          <div className="flex flex-wrap gap-1.5">
            {features.activations.map((f, i) => (
              <Pill key={i} tone={f.activated ? 'positive' : 'neutral'}>
                {f.activated ? '✓' : '·'} {(f.feature ?? '').replace(/_/g, ' ')}
              </Pill>
            ))}
          </div>
        ),
      });
    }

    return out;
  }, [trade, trace]);

  // Default-select the decision-defining stage (risk gate) or the last stage.
  const defaultIndex = useMemo(() => {
    const ri = stages.findIndex((s) => s.title === 'Risk gate');
    return ri >= 0 ? ri : Math.max(0, stages.length - 1);
  }, [stages]);
  const [selected, setSelected] = useState<number>(0);
  // keep selection valid when stages change
  const selectedIndex = selected < stages.length ? selected : defaultIndex;

  const rfNodes = useMemo<Node<StageNodeData>[]>(
    () =>
      stages.map((s, i) => ({
        id: String(i),
        type: 'stage',
        position: { x: 0, y: i * 116 },
        data: {
          icon: s.icon,
          title: s.title,
          statusLabel: s.status?.label,
          statusTone: s.status?.tone ?? 'neutral',
          tone: s.tone,
          isSelected: i === selectedIndex,
        },
        draggable: false,
      })),
    [stages, selectedIndex],
  );
  const rfEdges = useMemo<Edge[]>(
    () =>
      stages.slice(1).map((_, i) => ({
        id: `e-${i}`,
        source: String(i),
        target: String(i + 1),
        animated: true,
        style: { stroke: 'var(--accent)', opacity: 0.7 },
      })),
    [stages],
  );

  if (loading) {
    return (
      <div className="glass-panel flex min-h-[520px] flex-1 items-center justify-center rounded-2xl p-10">
        <div className="flex items-center gap-3 text-sm text-muted">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
          Reconstructing pipeline…
        </div>
      </div>
    );
  }
  if (error || !trade) {
    return (
      <div className="glass-panel flex min-h-[520px] flex-1 flex-col items-center justify-center gap-4 rounded-2xl p-10 text-center">
        <p className="text-base font-semibold text-primary">Signal not found</p>
        <p className="max-w-md text-sm text-muted">
          {error ?? 'This signal has no stored decision trace, or the link is invalid.'}
        </p>
        <button
          onClick={onBack}
          className="rounded-lg border border-line bg-surface px-4 py-2 text-sm font-medium text-secondary transition hover:bg-hover"
        >
          ← Back to signals
        </button>
      </div>
    );
  }

  const finalAction = (trade.executed_action ??
    trade.pm_recommendation ??
    trade.trade_action) as string;
  const isPreScreen = trade.decision_path === 'pre_screen';
  const executed = Boolean(trade.executed_action && trade.order_id);
  const sel = stages[selectedIndex];

  return (
    <div className="w-full space-y-4">
      {/* top bar */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <button
          onClick={onBack}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary transition hover:bg-hover"
        >
          ← Signals
        </button>
        <button
          onClick={() => navigator.clipboard?.writeText(window.location.href)}
          className="inline-flex items-center gap-2 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary transition hover:bg-hover"
        >
          🔗 Copy link
        </button>
      </div>

      {/* hero */}
      <section className="glass-panel rounded-2xl p-5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-2xl font-extrabold tracking-tight text-[var(--dashboard-text)]">
            {trade.ticker}
          </span>
          <Pill tone={actionTone(finalAction)}>{finalAction}</Pill>
          <Pill tone={isPreScreen ? 'neutral' : 'accent'}>
            {isPreScreen ? 'pre-screen' : 'full debate'}
          </Pill>
          {executed && <Pill tone="positive">order placed</Pill>}
          <span className="ml-auto text-[11px] text-muted">
            {new Date(trade.created_at).toLocaleString()}
          </span>
        </div>
        <p className="mt-1.5 max-w-3xl text-sm text-muted">{trade.headline}</p>
      </section>

      {/* React Flow replay + detail panel */}
      <section className="glass-panel rounded-2xl p-2">
        <div className="relative h-[640px] w-full overflow-hidden rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-bg)]">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            onNodeClick={(_, node) => setSelected(Number(node.id))}
            fitView
            fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
            minZoom={0.4}
            maxZoom={1.4}
            nodesDraggable={false}
            nodesConnectable={false}
            proOptions={{ hideAttribution: true }}
            defaultEdgeOptions={{
              type: 'smoothstep',
              markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'var(--accent)' },
            }}
          >
            <Background gap={16} size={1} color="var(--dashboard-border)" />
            <Controls className="border-line bg-surface-2 fill-primary text-primary" showInteractive={false} />

            {/* detail panel for the selected node */}
            {sel && (
              <Panel position="top-right" className="m-3 w-[330px] max-w-[85vw]">
                <div className="max-h-[600px] overflow-auto rounded-2xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-4 shadow-[var(--dashboard-shadow)] backdrop-blur-md">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="text-base">{sel.icon}</span>
                    <h3 className="text-sm font-bold text-[var(--dashboard-text)]">{sel.title}</h3>
                    {sel.status && <Pill tone={sel.status.tone}>{sel.status.label}</Pill>}
                  </div>
                  {sel.body}
                </div>
              </Panel>
            )}

            <Panel position="bottom-center" className="mb-2">
              <span className="rounded-full border border-line bg-surface/80 px-3 py-1 text-[10px] text-muted backdrop-blur">
                click any stage to inspect it
              </span>
            </Panel>
          </ReactFlow>
        </div>
      </section>
    </div>
  );
}
