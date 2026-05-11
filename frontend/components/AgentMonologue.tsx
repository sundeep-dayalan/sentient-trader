"use client";

import { articleSourceLabel, safeArticleUrl } from "@/lib/news";
import { DecisionTrace, LLMOperationTrace, PersonaOpinion, Trade } from "@/lib/types";

interface AgentMonologueProps { trade: Trade | null; }

const PERSONA_META: Record<string, { initial: string; colour: string }> = {
  "Momentum Trader": { initial: "M", colour: "bg-blue-500/20 text-blue-400 border-blue-500/30" },
  "Value Investor":  { initial: "V", colour: "bg-purple-500/20 text-purple-400 border-purple-500/30" },
  "Risk Manager":    { initial: "R", colour: "bg-amber-500/20 text-amber-400 border-amber-500/30" },
};

const TOOLTIPS = {
  sentiment:    "Score from -1.0 (very bearish) to +1.0 (very bullish). The Portfolio Manager produces this after weighing all three personas' stances and convictions.",
  confidence:   "How certain the committee is in their call. A split debate lowers this — a unanimous 3-0 vote at high conviction produces higher confidence than a 2-1 split.",
  committee:    "Three AI personas debate the headline sequentially. Each reads the previous persona's full opinion before responding, producing genuine disagreement rather than independent monologues.",
  conviction:   "How strongly this persona believes their own stance. A high-conviction dissenter (0.8+) can pull the Portfolio Manager's final confidence down even if outvoted 2-1.",
  bullish:      "Expects the stock price to rise. The bull thrusts its horns upward — prices going up.",
  bearish:      "Expects the stock price to fall. The bear swipes its paws downward — prices going down.",
  neutral:      "No strong directional view. Acts as a dissenting voice against whichever side has the majority, lowering overall confidence.",
  consensus:    "The Portfolio Manager's final synthesis — written after weighing all three debate opinions and their conviction scores. This is the reasoning behind the BUY / SELL / HOLD decision.",
  action:       "The final trade decision. BUY = purchase shares, SELL = sell shares, HOLD = do nothing. Only fires a real order if sentiment and confidence both clear the configured thresholds.",
  marketOrder:  "Executes immediately at the current market price. Chosen over limit orders because news-driven trades prioritise speed over price precision.",
  paperTrading: "Simulated trading with real market data but no real money. Used to validate strategy before ever risking live capital.",
};

function normaliseDecisionTrace(trade: Trade): DecisionTrace {
  const rawTrace = trade.decision_trace;
  if (Array.isArray(rawTrace)) {
    return { committee_debate: rawTrace };
  }

  const trace: DecisionTrace = rawTrace ?? {};
  return {
    ...trace,
    committee_debate: trace.committee_debate ?? trade.committee_debate ?? [],
    portfolio_manager_decision: trace.portfolio_manager_decision ?? {
      model: trade.model ?? null,
      sentiment: trade.sentiment_score,
      confidence: trade.confidence_score,
      reasoning: trade.reasoning,
      action: trade.trade_action,
    },
  };
}

// ── Tooltip ───────────────────────────────────────────────────────────────────

function Tooltip({ text, side = "top" }: { text: string; side?: "top" | "bottom" }) {
  const isTop = side === "top";
  return (
    <span className="group relative inline-flex shrink-0 cursor-default items-center">
      <InfoIcon />
      <span
        className={[
          "pointer-events-none absolute left-1/2 z-50 w-56 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2.5",
          "text-[11px] leading-relaxed text-secondary shadow-xl opacity-0 transition-opacity duration-150 group-hover:opacity-100",
          isTop ? "bottom-full mb-2" : "top-full mt-2",
        ].join(" ")}
      >
        {text}
        {/* arrow */}
        <span className={[
          "absolute left-1/2 -translate-x-1/2 border-4 border-transparent",
          isTop ? "top-full border-t-line -mt-px" : "bottom-full border-b-line -mb-px",
        ].join(" ")} />
      </span>
    </span>
  );
}

// ── Labelled section header with optional tooltip ─────────────────────────────

function SectionLabel({ label, tooltip, side }: { label: string; tooltip?: string; side?: "top" | "bottom" }) {
  return (
    <div className="flex items-center gap-1.5">
      <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">{label}</p>
      {tooltip && <Tooltip text={tooltip} side={side} />}
    </div>
  );
}

// ── Stance badge with tooltip ─────────────────────────────────────────────────

function StanceBadge({ stance }: { stance: string }) {
  const colour =
    stance === "BULLISH" ? "text-positive bg-positive-soft border-positive-border" :
    stance === "BEARISH" ? "text-negative bg-negative-soft border-negative-border" :
                           "text-muted bg-surface-3 border-line";

  const tip =
    stance === "BULLISH" ? TOOLTIPS.bullish :
    stance === "BEARISH" ? TOOLTIPS.bearish :
                           TOOLTIPS.neutral;

  return (
    <span className={`group relative inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-bold ${colour}`}>
      {stance}
      <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-48 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2 text-[11px] leading-relaxed font-normal text-secondary shadow-xl opacity-0 transition-opacity duration-150 group-hover:opacity-100">
        {tip}
        <span className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-line" />
      </span>
    </span>
  );
}

// ── Root component ────────────────────────────────────────────────────────────

export default function AgentMonologue({ trade }: AgentMonologueProps) {
  if (!trade) {
    return (
      <div className="glass-panel flex h-full min-h-[280px] items-center justify-center rounded-2xl p-10 xl:min-h-0">
        <div className="max-w-xs text-center">
          <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-surface-2 text-muted">
            <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.75 3.104v5.714a2.25 2.25 0 01-.659 1.591L5 14.5M9.75 3.104c-.251.023-.501.05-.75.082m.75-.082a24.301 24.301 0 014.5 0m0 0v5.714c0 .597.237 1.17.659 1.591L19.8 15.3M14.25 3.104c.251.023.501.05.75.082M19.8 15.3l-1.57.393A9.065 9.065 0 0112 15a9.065 9.065 0 00-6.23-.693L5 14.5m14.8.8l1.402 1.402c1.232 1.232.65 3.318-1.067 3.611A48.309 48.309 0 0112 21c-2.773 0-5.491-.235-8.135-.687-1.718-.293-2.3-2.379-1.067-3.61L5 14.5" />
            </svg>
          </div>
          <p className="text-base font-bold text-primary">Decision Core</p>
          <p className="mt-1.5 text-sm text-muted">Select a signal to inspect the agent&apos;s reasoning trace.</p>
        </div>
      </div>
    );
  }

  const isBuy  = trade.trade_action === "BUY";
  const isSell = trade.trade_action === "SELL";
  const actionColor = isBuy ? "text-positive" : isSell ? "text-negative" : "text-muted";
  const actionBg    = isBuy
    ? "bg-positive-soft border-positive-border"
    : isSell
    ? "bg-negative-soft border-negative-border"
    : "bg-surface-2 border-line";

  const sentimentPct  = Math.min(100, Math.max(0, ((trade.sentiment_score + 1) / 2) * 100));
  const confidencePct = Math.round(trade.confidence_score * 100);
  const articleUrl    = safeArticleUrl(trade.article_url);
  const decisionTrace = normaliseDecisionTrace(trade);
  const committee     = decisionTrace.committee_debate ?? [];
  const llmOperations = decisionTrace.llm_operations ?? [];
  const portfolioModel = decisionTrace.portfolio_manager_decision?.model ?? trade.model;

  return (
    <div className="glass-panel flex h-full min-h-[360px] flex-col overflow-hidden rounded-2xl xl:min-h-0">

      {/* Header */}
      <div className="shrink-0 border-b border-[var(--dashboard-divider)] px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="text-xs font-semibold text-[var(--dashboard-subtle)]">Decision Core</p>
            <div className="mt-1.5 flex flex-wrap items-center gap-2.5">
              <span className="font-mono text-2xl font-bold text-accent">{trade.ticker}</span>
              <span className="rounded-xl border border-line bg-surface-2 px-2.5 py-1 text-xs font-medium text-secondary">
                {new Date(trade.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
              </span>
            </div>
          </div>
          {/* Action badge with tooltip */}
          <span className={`group relative inline-flex cursor-default items-center gap-1.5 rounded-xl border px-3 py-1.5 text-xs font-bold ${actionColor} ${actionBg}`}>
            {trade.trade_action}
            <span className="pointer-events-none absolute right-0 top-full z-50 mt-2 w-56 rounded-xl border border-line bg-surface px-3 py-2.5 text-[11px] font-normal leading-relaxed text-secondary shadow-xl opacity-0 transition-opacity duration-150 group-hover:opacity-100">
              {TOOLTIPS.action}
              <span className="absolute bottom-full right-3 border-4 border-transparent border-b-line" />
            </span>
          </span>
        </div>
      </div>

      <div className="modern-scroll min-h-0 flex-1 space-y-3 overflow-x-hidden overflow-y-auto p-5 pr-4">

        {/* Headline */}
        <div className="rounded-xl border border-line bg-surface-2 p-4">
          <div className="mb-2 flex items-center justify-between gap-3">
            <p className="text-[11px] font-semibold uppercase tracking-wide text-muted">Market Headline</p>
            {articleUrl && (
              <a
                href={articleUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-surface px-2.5 py-1 text-[11px] font-semibold text-secondary transition hover:border-accent-border hover:text-accent"
              >
                <span className="max-w-[140px] truncate">{articleSourceLabel(trade.article_source)}</span>
                <ExternalLinkIcon />
              </a>
            )}
          </div>
          <p className="border-l-2 border-accent pl-3.5 text-sm leading-relaxed text-primary">
            &quot;{trade.headline}&quot;
          </p>
        </div>

        {/* Sentiment + Confidence */}
        <div className="grid gap-3 md:grid-cols-2">
          <div className="rounded-xl border border-line bg-surface-2 p-4">
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel label="Sentiment" tooltip={TOOLTIPS.sentiment} />
              <span className={`font-mono text-sm font-bold ${trade.sentiment_score >= 0 ? "text-positive" : "text-negative"}`}>
                {trade.sentiment_score >= 0 ? "+" : ""}{trade.sentiment_score.toFixed(3)}
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-surface-3">
              <div className="absolute bottom-0 left-1/2 top-0 w-px bg-line" />
              <div
                className={`absolute top-0 h-full w-2.5 rounded-full ${trade.sentiment_score >= 0 ? "bg-positive" : "bg-negative"}`}
                style={{ left: `calc(${sentimentPct}% - 5px)` }}
              />
            </div>
            <div className="mt-1.5 flex justify-between text-[10px] text-muted">
              <span>Bearish</span><span>Bullish</span>
            </div>
          </div>

          <div className="rounded-xl border border-line bg-surface-2 p-4">
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel label="Confidence" tooltip={TOOLTIPS.confidence} />
              <span className="font-mono text-sm font-bold text-accent">{confidencePct}%</span>
            </div>
            <div className="h-2 overflow-hidden rounded-full bg-surface-3">
              <div
                className="h-full rounded-full bg-gradient-to-r from-accent to-cyan transition-all duration-700"
                style={{ width: `${confidencePct}%` }}
              />
            </div>
            <div className="mt-1.5 flex justify-between text-[10px] text-muted">
              <span>Low</span><span>High</span>
            </div>
          </div>
        </div>

        {/* Committee Debate */}
        {committee.length > 0 && (
          <div className="rounded-xl border border-line bg-surface-2 p-4">
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel label="Committee Debate" tooltip={TOOLTIPS.committee} />
              <span className="text-[10px] text-muted">
                {summariseVote(committee)}
              </span>
            </div>
            <div className="space-y-3">
              {committee.map((p) => (
                <PersonaCard key={p.name} persona={p} />
              ))}
            </div>
          </div>
        )}

        {/* Consensus */}
        <div className="rounded-xl border border-line bg-surface-2 p-4">
          <div className="flex items-center justify-between">
            <SectionLabel label="Portfolio Manager Consensus" tooltip={TOOLTIPS.consensus} />
            {portfolioModel && (
              <span className="rounded bg-surface-3 px-1.5 py-0.5 font-mono text-[9px] text-muted">
                {portfolioModel.split('/').pop()} {/* E.g. "gpt-oss-120b" */}
              </span>
            )}
          </div>
          <p className="mt-2 text-sm leading-relaxed text-secondary">{trade.reasoning}</p>
        </div>

        {/* Raw Decision Core LLM operations */}
        {llmOperations.length > 0 && (
          <div className="rounded-xl border border-line bg-surface-2 p-4">
            <div className="mb-3 flex items-center justify-between">
              <SectionLabel label="LLM Operations" />
              <span className="text-[10px] text-muted">{llmOperations.length} steps</span>
            </div>
            <div className="space-y-2">
              {llmOperations.map((operation, index) => (
                <TraceOperationCard
                  key={`${operation.step}-${operation.recorded_at ?? index}`}
                  operation={operation}
                />
              ))}
            </div>
          </div>
        )}

        {/* SIM signal notice — simulated signals never trigger Alpaca orders */}
        {trade.is_simulated && (
          <div className="flex items-start gap-3 rounded-xl border border-warning-border bg-warning-soft px-4 py-3.5">
            <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-warning">
              <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
              </svg>
            </div>
            <div className="min-w-0">
              <p className="text-xs font-bold text-warning">Simulated Signal — No Orders Placed</p>
              <p className="mt-1 text-[11px] leading-relaxed text-warning opacity-80">
                This signal was injected via the Simulate feature. Alpaca orders are always
                skipped for simulated signals, regardless of confidence or sentiment scores.
              </p>
            </div>
          </div>
        )}

        {/* Order confirmation — only for real (non-simulated) signals */}
        {trade.order_id && !trade.is_simulated && (
          <div className="flex items-start gap-3 rounded-xl border border-positive-border bg-positive-soft px-4 py-3.5">
            <div className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-positive">
              <svg className="h-3 w-3 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
              </svg>
            </div>
            <div className="min-w-0">
              <p className="text-xs font-bold text-positive">Order Executed via Alpaca</p>
              <p className="mt-0.5 break-all font-mono text-[11px] text-positive opacity-80">{trade.order_id}</p>
              <div className="mt-0.5 flex items-center gap-1 text-[11px] text-positive opacity-70">
                {trade.quantity} share(s) ·{" "}
                <span className="group relative inline-flex cursor-default items-center gap-0.5">
                  Paper Trading
                  <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-52 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2 text-[11px] leading-relaxed font-normal text-secondary shadow-xl opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                    {TOOLTIPS.paperTrading}
                    <span className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-line" />
                  </span>
                </span>{" "}
                ·{" "}
                <span className="group relative inline-flex cursor-default items-center gap-0.5">
                  Market Order
                  <span className="pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-52 -translate-x-1/2 rounded-xl border border-line bg-surface px-3 py-2 text-[11px] leading-relaxed font-normal text-secondary shadow-xl opacity-0 transition-opacity duration-150 group-hover:opacity-100">
                    {TOOLTIPS.marketOrder}
                    <span className="absolute left-1/2 top-full -translate-x-1/2 border-4 border-transparent border-t-line" />
                  </span>
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────────────

function summariseVote(committee: PersonaOpinion[]): string {
  const counts: Record<string, number> = { BULLISH: 0, BEARISH: 0, NEUTRAL: 0 };
  committee.forEach((p) => counts[p.stance]++);
  return Object.entries(counts)
    .filter(([, n]) => n > 0)
    .map(([stance, n]) => `${n} ${stance.toLowerCase()}`)
    .join(" · ");
}

function stepTitle(step: string): string {
  return step
    .split("_")
    .map(word => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

function safeJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return "Unable to render trace payload.";
  }
}

function TraceOperationCard({ operation }: { operation: LLMOperationTrace }) {
  const model = operation.model?.split("/").pop();
  const failed = Boolean(operation.error);
  const payload = {
    messages: operation.messages ?? [],
    input: operation.input ?? null,
    output: operation.output ?? null,
    error: operation.error ?? null,
  };

  return (
    <details className="group rounded-lg border border-line bg-surface px-3 py-2.5">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3">
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold text-primary">{stepTitle(operation.step)}</p>
          <p className="mt-0.5 truncate font-mono text-[9px] text-muted">
            {operation.response_schema ?? operation.kind}{model ? ` · ${model}` : ""}
          </p>
        </div>
        <span className={[
          "shrink-0 rounded border px-1.5 py-0.5 text-[9px] font-bold",
          failed ? "border-negative-border bg-negative-soft text-negative" : "border-positive-border bg-positive-soft text-positive",
        ].join(" ")}>
          {failed ? "ERROR" : "OK"}
        </span>
      </summary>
      <pre className="modern-scroll mt-3 max-h-64 overflow-auto whitespace-pre-wrap rounded-md border border-line bg-surface-3 p-3 text-[10px] leading-relaxed text-secondary">
        {safeJson(payload)}
      </pre>
    </details>
  );
}

function PersonaCard({ persona }: { persona: PersonaOpinion }) {
  const meta = PERSONA_META[persona.name] ?? { initial: persona.name[0], colour: "bg-surface-3 text-muted border-line" };
  const convictionPct = Math.round(persona.conviction * 100);
  const convictionColour =
    convictionPct >= 70 ? "bg-accent" :
    convictionPct >= 40 ? "bg-amber-500" :
                          "bg-muted";

  return (
    <div className="min-w-0 rounded-lg border border-line bg-surface p-3.5">

      {/* Row 1: avatar + name + stance badge */}
      <div className="flex items-center gap-2.5">
        <div className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md border text-xs font-bold ${meta.colour}`}>
          {meta.initial}
        </div>
        <div className="flex flex-1 flex-col justify-center">
          <span className="text-xs font-semibold text-primary">{persona.name}</span>
          {persona.model && (
            <span className="font-mono text-[9px] text-muted">
              {persona.model.split('/').pop()}
            </span>
          )}
        </div>
        <StanceBadge stance={persona.stance} />
      </div>

      {/* Conviction bar with tooltip */}
      <div className="mt-2.5 flex items-center gap-2">
        <div className="flex shrink-0 items-center gap-1 text-[10px] text-muted">
          Conviction
          <Tooltip text={TOOLTIPS.conviction} side="bottom" />
        </div>
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-surface-3">
          <div
            className={`h-full rounded-full transition-all duration-500 ${convictionColour}`}
            style={{ width: `${convictionPct}%` }}
          />
        </div>
        <span className="w-7 text-right font-mono text-[10px] text-muted">{convictionPct}%</span>
      </div>

      {/* Sharp one-liner take */}
      <p className="mt-2.5 border-l-2 border-line pl-2.5 text-[11px] font-medium leading-relaxed text-primary">
        {persona.view}
      </p>

      {/* Full reasoning */}
      {persona.reasoning && persona.reasoning !== persona.view && (
        <p className="mt-1.5 text-[11px] leading-relaxed text-secondary">
          {persona.reasoning}
        </p>
      )}
    </div>
  );
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function InfoIcon() {
  return (
    <svg className="h-3 w-3 shrink-0 text-muted opacity-60 transition-opacity group-hover:opacity-100" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4" />
      <path d="M12 8h.01" />
    </svg>
  );
}

function ExternalLinkIcon() {
  return (
    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M14 3h7v7" />
      <path d="M10 14 21 3" />
      <path d="M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5" />
    </svg>
  );
}
