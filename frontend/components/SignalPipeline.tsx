/**
 * SignalPipeline — per-signal pipeline replay.
 *
 * Renders the *exact* path one signal took through the agent, reconstructed
 * from the `decision_trace` JSONB stored in Supabase. This is the in-house,
 * permanent alternative to an external trace viewer: every stage that ran is
 * shown top-to-bottom with its real data (news → pre-screen → market context →
 * 4-agent committee → portfolio manager → risk gate → price gate → execution),
 * and the path reflects where the signal actually exited (pre-screen filter,
 * risk-gate HOLD, or a filled order).
 */
import { useMemo } from 'react';
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
  ticker?: string;
  summary?: string;
  headline?: string;
  article_url?: string;
  published_at?: string;
  is_simulated?: boolean;
}
interface MarketContext {
  price?: number;
  day_change_pct?: number;
  position?: { qty?: number; side?: string; avg_entry_price?: number | null };
  account?: { equity?: number; buying_power?: number; cash?: number };
  technical_indicators_unavailable_reason?: string;
}
interface BracketOrders {
  entry_price?: number;
  stop_loss_price?: number;
  take_profit_price?: number;
  stop_loss_pct?: number;
  take_profit_pct?: number;
  status?: string;
}
interface PriceMoveGate {
  blocked?: boolean;
  enabled?: boolean;
  move_pct?: number;
  threshold_pct?: number;
  live_price?: number;
  snapshot_price?: number;
}
interface ExecutionTrace {
  action?: string;
  status?: string;
  order_id?: string;
  quantity?: number;
  submitted?: boolean;
  fill_status?: string;
  limit_price?: number;
  filled_avg_price?: number;
  error?: string | null;
  bracket_orders?: BracketOrders;
  price_move_gate?: PriceMoveGate;
  execution_plan?: {
    sizing_scale?: number;
    sizing_method?: string;
    sizing_reasons?: string[];
    estimated_notional?: number;
  };
  fill_verification?: { status?: string; filled_qty?: number; filled_avg_price?: number };
}
interface FeatureActivation {
  feature?: string;
  enabled?: boolean;
  activated?: boolean;
  outcome?: string;
  impact?: string;
}
interface EnhancedFeatures {
  summary?: { active?: string[]; skipped?: string[]; errors?: string[] };
  activations?: FeatureActivation[];
  total_features_enabled?: number;
  total_features_activated?: number;
  total_features_skipped?: number;
}

type Tone = 'neutral' | 'positive' | 'negative' | 'accent' | 'warn';

// ── Small presentational helpers ─────────────────────────────────────────────

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

// ── A single pipeline stage (left rail + content card) ───────────────────────

function Stage({
  index,
  total,
  icon,
  title,
  status,
  tone = 'neutral',
  children,
}: {
  index: number;
  total: number;
  icon: React.ReactNode;
  title: string;
  status?: { label: string; tone: Tone };
  tone?: Tone;
  children?: React.ReactNode;
}) {
  const isLast = index === total - 1;
  return (
    <div className="flex gap-3">
      {/* rail */}
      <div className="flex flex-col items-center">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border ${toneClasses(
            tone,
          )}`}
        >
          {icon}
        </div>
        {!isLast && <div className="mt-1 w-px flex-1 bg-[var(--dashboard-border)]" />}
      </div>
      {/* content */}
      <div className={`min-w-0 flex-1 ${isLast ? '' : 'pb-5'}`}>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-bold text-[var(--dashboard-text)]">{title}</h3>
          {status && <Pill tone={status.tone}>{status.label}</Pill>}
        </div>
        {children && (
          <div className="rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] p-3.5">
            {children}
          </div>
        )}
      </div>
    </div>
  );
}

function ConvictionBar({ value, tone }: { value: number; tone: Tone }) {
  const barTone =
    tone === 'positive'
      ? 'bg-positive'
      : tone === 'negative'
        ? 'bg-negative'
        : 'bg-amber-500';
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className={`h-full rounded-full ${barTone}`}
        style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
      />
    </div>
  );
}

// ── Trace normalization ──────────────────────────────────────────────────────

function normalizeTrace(trade: Trade): DecisionTrace {
  const dt = trade.decision_trace;
  if (Array.isArray(dt)) return { committee_debate: dt };
  return (dt ?? {}) as DecisionTrace;
}

// ── Main component ───────────────────────────────────────────────────────────

export interface SignalPipelineProps {
  trade: Trade | null;
  loading: boolean;
  error: string | null;
  onBack: () => void;
}

export default function SignalPipeline({ trade, loading, error, onBack }: SignalPipelineProps) {
  const trace = useMemo<DecisionTrace>(() => (trade ? normalizeTrace(trade) : {}), [trade]);

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

  const finalAction = (trade.executed_action ??
    trade.pm_recommendation ??
    trade.trade_action) as string;
  const isPreScreen = trade.decision_path === 'pre_screen' || !committee?.length;
  const executed = Boolean(trade.executed_action && trade.order_id);

  const durationMs =
    trade.processing_started_at && trade.processing_finished_at
      ? new Date(trade.processing_finished_at).getTime() -
        new Date(trade.processing_started_at).getTime()
      : null;

  // Build the ordered list of stages that actually ran.
  const stages: Array<{
    icon: React.ReactNode;
    title: string;
    tone?: Tone;
    status?: { label: string; tone: Tone };
    body: React.ReactNode;
  }> = [];

  // 1. News
  stages.push({
    icon: '📰',
    title: 'News received',
    tone: 'accent',
    status: news?.is_simulated || trade.is_simulated ? { label: 'Simulated', tone: 'warn' } : undefined,
    body: (
      <div className="space-y-2">
        <p className="text-sm font-medium text-primary">{news?.headline ?? trade.headline}</p>
        {news?.summary && <p className="text-xs leading-relaxed text-muted">{news.summary}</p>}
        <div className="flex flex-wrap gap-1.5 pt-1">
          {(news?.source ?? trade.article_source) && (
            <Pill>{news?.source ?? trade.article_source}</Pill>
          )}
          {news?.published_at && (
            <Pill>{new Date(news.published_at).toLocaleString()}</Pill>
          )}
          {(news?.article_url ?? trade.article_url) && (
            <a
              href={(news?.article_url ?? trade.article_url) as string}
              target="_blank"
              rel="noreferrer"
              className="text-[10px] font-semibold uppercase tracking-wide text-accent hover:underline"
            >
              Source article ↗
            </a>
          )}
        </div>
      </div>
    ),
  });

  // 2. Pre-screen / article quality
  if (quality) {
    const grade = (quality.grade ?? '').toUpperCase();
    const qTone: Tone = grade === 'HIGH' ? 'positive' : grade === 'LOW' ? 'negative' : 'warn';
    stages.push({
      icon: '🔍',
      title: 'Pre-screen — article quality',
      tone: qTone,
      status: {
        label: isPreScreen ? `${grade || 'SCORED'} · short-circuit` : grade || 'SCORED',
        tone: qTone,
      },
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
              Resolved deterministically — no LLM calls spent on this signal.
            </p>
          )}
        </div>
      ),
    });
  }

  // 3. Market context
  if (market) {
    stages.push({
      icon: '📊',
      title: 'Market context',
      body: (
        <div className="grid grid-cols-2 gap-x-6">
          <KV label="Price" value={money(market.price)} />
          <KV
            label="Day change"
            value={
              market.day_change_pct !== undefined ? `${market.day_change_pct.toFixed(2)}%` : undefined
            }
          />
          <KV
            label="Position"
            value={
              market.position
                ? `${market.position.side ?? 'flat'} · ${market.position.qty ?? 0}`
                : undefined
            }
          />
          <KV label="Buying power" value={money(market.account?.buying_power)} />
          {market.technical_indicators_unavailable_reason && (
            <div className="col-span-2 pt-1">
              <Pill tone="warn">Technical indicators unavailable</Pill>
            </div>
          )}
        </div>
      ),
    });
  }

  // 4. Committee debate
  if (committee?.length) {
    stages.push({
      icon: '🧠',
      title: 'AI committee debate',
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
                    <span className="font-mono text-[10px] text-muted">
                      {pct(p.conviction, 0)}
                    </span>
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

  // 5. Portfolio manager synthesis
  if (pm) {
    stages.push({
      icon: '🧑‍⚖️',
      title: 'Portfolio manager — synthesis',
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
    stages.push({
      icon: '🛡️',
      title: 'Risk gate',
      tone: should ? 'positive' : 'warn',
      status: { label: should ? 'CLEARED' : 'HELD', tone: should ? 'positive' : 'warn' },
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
            <KV label="Calibrated confidence" value={pct(metrics?.calibrated_confidence, 0)} />
            <KV label="Confidence cap" value={pct(metrics?.confidence_cap, 0)} />
            <KV label="Committee agreement" value={pct(metrics?.agreement, 0)} />
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

  // 7. Execution (or HOLD terminal)
  if (executed && execution) {
    const gate = execution.price_move_gate;
    const bracket = execution.bracket_orders;
    stages.push({
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
            <KV label="Limit price" value={money(execution.limit_price)} />
            <KV label="Filled @" value={money(execution.filled_avg_price)} />
            <KV
              label="Sizing"
              value={
                execution.execution_plan?.sizing_scale !== undefined
                  ? `${pct(execution.execution_plan.sizing_scale, 0)} · ${execution.execution_plan.sizing_method ?? ''}`
                  : undefined
              }
            />
            <KV label="Order ID" value={execution.order_id?.slice(0, 8)} />
          </div>
          {bracket && (bracket.stop_loss_price || bracket.take_profit_price) && (
            <div className="flex flex-wrap gap-1.5 pt-1">
              {bracket.entry_price && <Pill tone="accent">entry {money(bracket.entry_price)}</Pill>}
              {bracket.take_profit_price && (
                <Pill tone="positive">TP {money(bracket.take_profit_price)}</Pill>
              )}
              {bracket.stop_loss_price && (
                <Pill tone="negative">SL {money(bracket.stop_loss_price)}</Pill>
              )}
            </div>
          )}
          {gate && (
            <p className="text-[11px] text-muted">
              price-move gate · {gate.blocked ? 'blocked' : 'passed'}
              {gate.move_pct !== undefined ? ` (${pct(gate.move_pct, 2)} move)` : ''}
            </p>
          )}
        </div>
      ),
    });
  } else {
    stages.push({
      icon: '🟡',
      title: 'No order placed — HOLD',
      tone: 'warn',
      status: { label: 'HOLD', tone: 'warn' },
      body: (
        <p className="text-xs leading-relaxed text-muted">
          {trade.gate_reason ??
            risk?.reason ??
            'The decision did not clear every gate, so no order was placed. The signal is logged for audit and outcome labeling.'}
        </p>
      ),
    });
  }

  // 8. Enhanced features (compact grid)
  if (features?.activations?.length) {
    stages.push({
      icon: '⚙️',
      title: 'Enhanced features',
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

  return (
    <div className="w-full space-y-5">
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
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="mb-1 flex items-center gap-2">
              <span className="text-2xl font-extrabold tracking-tight text-[var(--dashboard-text)]">
                {trade.ticker}
              </span>
              <Pill tone={actionTone(finalAction)}>{finalAction}</Pill>
              <Pill tone={isPreScreen ? 'neutral' : 'accent'}>
                {isPreScreen ? 'pre-screen' : 'full debate'}
              </Pill>
              {executed && <Pill tone="positive">order placed</Pill>}
            </div>
            <p className="max-w-2xl text-sm text-muted">{trade.headline}</p>
          </div>
          <div className="text-right text-[11px] text-muted">
            <div>{new Date(trade.created_at).toLocaleString()}</div>
            {durationMs !== null && <div>processed in {(durationMs / 1000).toFixed(1)}s</div>}
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: 'Sentiment', value: trade.sentiment_score?.toFixed(2) },
            { label: 'Confidence', value: pct(trade.confidence_score, 0) },
            { label: 'Calibrated', value: pct(trade.calibrated_confidence ?? undefined, 0) },
            { label: 'Path', value: trade.decision_path ?? '—' },
          ].map((s) => (
            <div key={s.label} className="rounded-xl border border-[var(--dashboard-border)] bg-[var(--dashboard-card)] px-3 py-2">
              <div className="text-[10px] uppercase tracking-wide text-muted">{s.label}</div>
              <div className="font-mono text-sm font-semibold text-primary">{s.value ?? '—'}</div>
            </div>
          ))}
        </div>
      </section>

      {/* pipeline replay */}
      <section className="glass-panel rounded-2xl p-5">
        <h2 className="mb-4 text-sm font-bold text-[var(--dashboard-text)]">
          Pipeline replay
          <span className="ml-2 font-normal text-muted">
            — exactly how this signal flowed through the agent
          </span>
        </h2>
        <div>
          {stages.map((s, i) => (
            <Stage
              key={i}
              index={i}
              total={stages.length}
              icon={<span className="text-base">{s.icon}</span>}
              title={s.title}
              tone={s.tone}
              status={s.status}
            >
              {s.body}
            </Stage>
          ))}
        </div>
      </section>
    </div>
  );
}
