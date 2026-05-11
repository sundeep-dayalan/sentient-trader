"use client";

import { useEffect, useRef, useState } from "react";
import { BASE_PATH } from "@/lib/config";
import { useAuth } from "@/components/AuthProvider";

// ── Types ────────────────────────────────────────────────────────────────────

interface ModelTier {
  id: string; label: string; reqDay: string; tpm: string;
  quality: "high" | "mid" | "fallback";
}

interface AgentConfig {
  thresholds: { buy_sentiment: number; sell_sentiment: number; confidence: number };
  execution:  { order_qty: number };
  model:      { cascade: ModelTier[]; override: string | null };
  prompts:    { momentum: string; value: string; risk: string; synthesis: string };
  consumer:   { batch_size: number; poll_interval: number; error_retry: number };
}

// ── Tab registry — add future tabs here only ─────────────────────────────────

const TABS = [
  { id: "agent-config",  label: "Agent Config",  icon: <CogIcon /> },
  // { id: "notifications", label: "Notifications", icon: <BellIcon /> },  // future
] as const;

type TabId = typeof TABS[number]["id"];

// ── Root component ────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("agent-config");
  const [config,    setConfig]    = useState<AgentConfig | null>(null);
  const [loading,   setLoading]   = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const { isSuperUser } = useAuth();

  useEffect(() => {
    fetch(`${BASE_PATH}/api/agent-config`)
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() as Promise<AgentConfig>; })
      .then(data  => { setConfig(data);    setLoading(false); })
      .catch(err  => { setFetchError(String(err)); setLoading(false); });
  }, []);

  return (
    // GitHub-style: full-height panel, left sidebar + scrollable right content
    <div className="glass-panel flex min-h-[600px] flex-1 overflow-hidden rounded-2xl">

      {/* ── Left nav ─────────────────────────────────────────────── */}
      <nav className="w-52 shrink-0 border-r border-line px-2 py-4">
        <p className="mb-1 px-3 text-[10px] font-semibold uppercase tracking-widest text-muted">Settings</p>
        {TABS.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={[
              "flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors duration-150",
              activeTab === tab.id
                ? "bg-accent-soft text-accent"
                : "text-secondary hover:bg-hover hover:text-primary",
            ].join(" ")}
          >
            <span className={activeTab === tab.id ? "text-accent" : "text-muted"}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </nav>

      {/* ── Right content ─────────────────────────────────────────── */}
      <div className="modern-scroll min-w-0 flex-1 overflow-y-auto px-6 py-6">
        {loading && (
          <div className="flex h-48 items-center justify-center gap-2.5 text-sm text-muted">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
            Loading config…
          </div>
        )}
        {fetchError && (
          <div className="flex h-48 items-center justify-center text-sm text-negative">{fetchError}</div>
        )}
        {!loading && !fetchError && config && activeTab === "agent-config" && (
          <AgentConfigTab config={config} onSave={setConfig} canEdit={isSuperUser} />
        )}
      </div>
    </div>
  );
}

// ── Agent Config tab ──────────────────────────────────────────────────────────

function AgentConfigTab({
  config,
  onSave,
  canEdit,
}: {
  config: AgentConfig;
  onSave: (updated: AgentConfig) => void;
  canEdit: boolean;
}) {
  // Which section is open for editing
  const [editing, setEditing] = useState<string | null>(null);
  // Deep-copy of config that accumulates edits; committed on Save
  const [draft,   setDraft]   = useState<AgentConfig>(structuredClone(config));
  const [saving,  setSaving]  = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const openEdit  = (section: string) => { setDraft(structuredClone(config)); setEditing(section); setSaveErr(null); };
  const cancelEdit = () => { setEditing(null); setSaveErr(null); };

  const save = async (section: string) => {
    setSaving(true);
    setSaveErr(null);
    try {
      const res = await fetch(`${BASE_PATH}/api/agent-config`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (!res.ok) {
        const { error } = await res.json() as { error?: string };
        throw new Error(error ?? `HTTP ${res.status}`);
      }
      onSave(structuredClone(draft));
      setEditing(null);
    } catch (e) {
      setSaveErr(String(e));
    } finally {
      setSaving(false);
    }
  };

  const isEditing = (section: string) => editing === section;

  return (
    <div className="max-w-2xl space-y-8">

      {/* Page heading */}
      <div>
        <h1 className="text-lg font-bold text-primary">Agent Config</h1>
        <p className="mt-1 text-sm text-muted">
          Tunable parameters for the trading agent. Changes are persisted to Supabase
          and applied on the next agent restart.
        </p>
      </div>

      {/* ── Trade Thresholds ── */}
      <Section
        title="Trade Thresholds"
        description="A signal must clear both the directional sentiment bar and the confidence gate before an order is submitted to Alpaca."
        isEditing={isEditing("thresholds")}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit("thresholds") : undefined}
        onCancel={cancelEdit}
        onSave={() => save("thresholds")}
        readOnly={!canEdit}
      >
        {isEditing("thresholds") ? (
          <div className="space-y-4">
            <NumberField
              label="Buy sentiment minimum"
              hint="Range 0.0 → 1.0 — must reach this to trigger BUY"
              value={draft.thresholds.buy_sentiment}
              min={0} max={1} step={0.05}
              onChange={v => setDraft(d => ({ ...d, thresholds: { ...d.thresholds, buy_sentiment: v } }))}
            />
            <NumberField
              label="Sell sentiment maximum"
              hint="Range -1.0 → 0.0 — must fall below this to trigger SELL"
              value={draft.thresholds.sell_sentiment}
              min={-1} max={0} step={0.05}
              onChange={v => setDraft(d => ({ ...d, thresholds: { ...d.thresholds, sell_sentiment: v } }))}
            />
            <NumberField
              label="Confidence gate"
              hint="Range 0.0 → 1.0 — committee confidence must exceed this for any trade"
              value={draft.thresholds.confidence}
              min={0} max={1} step={0.05}
              onChange={v => setDraft(d => ({ ...d, thresholds: { ...d.thresholds, confidence: v } }))}
            />
            <NumberField
              label="Order size (shares)"
              hint="Shares per executed order — keep small for paper trading"
              value={draft.execution.order_qty}
              min={1} max={100} step={1}
              onChange={v => setDraft(d => ({ ...d, execution: { ...d.execution, order_qty: v } }))}
            />
          </div>
        ) : (
          <div className="divide-y divide-line rounded-xl border border-line">
            <ReadRow label="Buy sentiment min"  value={`+${config.thresholds.buy_sentiment.toFixed(2)}`}  colour="text-positive" />
            <ReadRow label="Sell sentiment max" value={`${config.thresholds.sell_sentiment.toFixed(2)}`}  colour="text-negative" />
            <ReadRow label="Confidence gate"    value={`${(config.thresholds.confidence * 100).toFixed(0)}%`} colour="text-accent" />
            <ReadRow label="Order size"         value={`${config.execution.order_qty} share${config.execution.order_qty !== 1 ? "s" : ""}`} />
          </div>
        )}
      </Section>

      <Divider />

      {/* ── Model Cascade ── */}
      <Section
        title="Model Cascade"
        description="Models are tried in quality order. Daily-exhausted tiers are skipped automatically — each model's quota bucket is independent."
        isEditing={isEditing("model")}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit("model") : undefined}
        onCancel={cancelEdit}
        onSave={() => save("model")}
        readOnly={!canEdit}
      >
        {/* Cascade is always read-only — order is set in code */}
        <div className="mb-4 space-y-2">
          {config.model.cascade.map((tier, i) => (
            <ModelTierRow key={tier.id} tier={tier} index={i + 1} />
          ))}
        </div>

        {isEditing("model") ? (
          <TextField
            label="Model override"
            hint="Enter a Groq model ID to bypass the cascade, or leave empty to use the cascade."
            placeholder="e.g. llama-3.1-8b-instant  (empty = use cascade)"
            value={draft.model.override ?? ""}
            onChange={v => setDraft(d => ({ ...d, model: { ...d.model, override: v || null } }))}
          />
        ) : (
          <div className="divide-y divide-line rounded-xl border border-line">
            <ReadRow
              label="Override"
              value={config.model.override ?? "— using cascade"}
              mono={!!config.model.override}
              badge={config.model.override ? "active" : undefined}
            />
          </div>
        )}
      </Section>

      <Divider />

      {/* ── Persona Prompts ── */}
      <Section
        title="Persona System Prompts"
        description="Each of the four LLM calls uses its own system prompt. These define the analytical worldview each persona brings to the committee debate."
        isEditing={isEditing("prompts")}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit("prompts") : undefined}
        onCancel={cancelEdit}
        onSave={() => save("prompts")}
        readOnly={!canEdit}
      >
        {isEditing("prompts") ? (
          <div className="space-y-4">
            {(["momentum", "value", "risk", "synthesis"] as const).map(key => (
              <PromptField
                key={key}
                label={PROMPT_LABELS[key].name}
                callOrder={PROMPT_LABELS[key].order}
                colour={PROMPT_LABELS[key].colour}
                initial={PROMPT_LABELS[key].initial}
                value={draft.prompts[key]}
                onChange={v => setDraft(d => ({ ...d, prompts: { ...d.prompts, [key]: v } }))}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {(["momentum", "value", "risk", "synthesis"] as const).map(key => (
              <PromptReadCard
                key={key}
                label={PROMPT_LABELS[key].name}
                callOrder={PROMPT_LABELS[key].order}
                colour={PROMPT_LABELS[key].colour}
                initial={PROMPT_LABELS[key].initial}
                value={config.prompts[key]}
              />
            ))}
          </div>
        )}
      </Section>

      <Divider />

      {/* ── Stream Consumer (read-only) ── */}
      <Section
        title="Redis Stream Consumer"
        description="Infrastructure settings for the Upstash Redis stream consumer. Change via environment variables."
        isEditing={false}
        readOnly
      >
        <div className="divide-y divide-line rounded-xl border border-line">
          <ReadRow label="Batch size"    value={`${config.consumer.batch_size} messages`} />
          <ReadRow label="Poll interval" value={`${config.consumer.poll_interval}s`} />
          <ReadRow label="Error retry"   value={`${config.consumer.error_retry}s`} />
        </div>
      </Section>

    </div>
  );
}

// ── Section wrapper ───────────────────────────────────────────────────────────

function Section({
  title,
  description,
  isEditing,
  saving,
  saveError,
  readOnly,
  onEdit,
  onCancel,
  onSave,
  children,
}: {
  title:        string;
  description:  string;
  isEditing:    boolean;
  saving?:      boolean;
  saveError?:   string | null;
  readOnly?:    boolean;
  onEdit?:      () => void;
  onCancel?:    () => void;
  onSave?:      () => void;
  children:     React.ReactNode;
}) {
  return (
    <section>
      {/* Section header */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-bold text-primary">{title}</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
        </div>
        {!readOnly && (
          isEditing ? (
            <div className="flex shrink-0 items-center gap-2">
              <button
                onClick={onCancel}
                className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary hover:border-accent-border hover:text-primary transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={onSave}
                disabled={saving}
                className="flex items-center gap-1.5 rounded-lg bg-accent px-3 py-1.5 text-xs font-semibold text-white shadow-sm transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                {saving && <span className="h-3 w-3 animate-spin rounded-full border border-white border-t-transparent" />}
                {saving ? "Saving…" : "Save"}
              </button>
            </div>
          ) : (
            <button
              onClick={onEdit}
              className="flex shrink-0 items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary hover:border-accent-border hover:text-primary transition-colors"
            >
              <PencilIcon />
              Edit
            </button>
          )
        )}
      </div>

      {saveError && (
        <div className="mb-3 rounded-lg border border-negative-border bg-negative-soft px-3 py-2 text-xs text-negative">
          {saveError}
        </div>
      )}

      {children}
    </section>
  );
}

// ── Form fields ───────────────────────────────────────────────────────────────

function NumberField({
  label, hint, value, min, max, step, onChange,
}: {
  label: string; hint: string; value: number;
  min: number; max: number; step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-primary">{label}</label>
      <div className="flex items-center gap-3">
        <input
          type="number"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="w-28 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
        />
        <input
          type="range"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={e => onChange(parseFloat(e.target.value))}
          className="flex-1 accent-accent"
        />
      </div>
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function TextField({
  label, hint, placeholder, value, onChange,
}: {
  label: string; hint: string; placeholder: string; value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-primary">{label}</label>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
      />
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function PromptField({
  label, callOrder, colour, initial, value, onChange,
}: {
  label: string; callOrder: number; colour: string; initial: string;
  value: string; onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span className={`flex h-5 w-5 items-center justify-center rounded border text-[10px] font-bold ${colour}`}>{initial}</span>
        <label className="text-xs font-semibold text-primary">{label}</label>
        <span className="text-[10px] text-muted">call #{callOrder}</span>
      </div>
      <textarea
        ref={ref}
        value={value}
        onChange={e => onChange(e.target.value)}
        rows={3}
        className="w-full resize-none overflow-hidden rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
      />
    </div>
  );
}

// ── Read-only display components ──────────────────────────────────────────────

function ReadRow({
  label, value, mono, colour, badge,
}: {
  label: string; value: string; mono?: boolean;
  colour?: string; badge?: string;
}) {
  return (
    <div className="flex items-center justify-between gap-4 px-4 py-3">
      <span className="text-xs font-medium text-muted">{label}</span>
      <div className="flex items-center gap-2">
        {badge && (
          <span className="rounded border border-warning-border bg-warning-soft px-1.5 py-0.5 text-[10px] font-bold text-warning">
            {badge}
          </span>
        )}
        <span className={`text-right text-xs font-semibold ${colour ?? "text-primary"} ${mono ? "font-mono" : ""}`}>
          {value}
        </span>
      </div>
    </div>
  );
}

function ModelTierRow({ tier, index }: { tier: ModelTier; index: number }) {
  const qualityColour = {
    high:     "text-positive bg-positive-soft border-positive-border",
    mid:      "text-accent bg-accent-soft border-accent-border",
    fallback: "text-muted bg-surface-2 border-line",
  }[tier.quality];
  const qualityLabel = { high: "Best quality", mid: "Strong", fallback: "Fallback" }[tier.quality];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-surface-3 font-mono text-[11px] font-bold text-muted">{index}</span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs font-semibold text-primary">{tier.id}</span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${qualityColour}`}>{qualityLabel}</span>
        </div>
        <p className="mt-0.5 text-[11px] text-muted">{tier.reqDay} req/day · {tier.tpm} TPM · 30 RPM</p>
      </div>
    </div>
  );
}

const PROMPT_LABELS = {
  momentum:  { name: "Momentum Trader",              order: 1, initial: "M", colour: "bg-blue-500/20 text-blue-400 border-blue-500/30"    },
  value:     { name: "Value Investor",               order: 2, initial: "V", colour: "bg-purple-500/20 text-purple-400 border-purple-500/30" },
  risk:      { name: "Risk Manager",                 order: 3, initial: "R", colour: "bg-amber-500/20 text-amber-400 border-amber-500/30"    },
  synthesis: { name: "Portfolio Manager (Synthesizer)", order: 4, initial: "P", colour: "bg-emerald-500/20 text-emerald-400 border-emerald-500/30" },
} as const;

function PromptReadCard({
  label, callOrder, colour, initial, value,
}: {
  label: string; callOrder: number; colour: string; initial: string; value: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-surface">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center gap-2.5 px-3.5 py-3 text-left"
      >
        <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded border text-[10px] font-bold ${colour}`}>{initial}</span>
        <span className="flex-1 text-xs font-semibold text-primary">{label}</span>
        <span className="text-[10px] text-muted">call #{callOrder}</span>
        <ChevronIcon expanded={open} />
      </button>
      {open && (
        <div className="border-t border-line px-4 pb-4 pt-3">
          <p className="whitespace-pre-wrap text-[11px] leading-relaxed text-secondary">{value}</p>
        </div>
      )}
    </div>
  );
}

function Divider() {
  return <div className="border-t border-line" />;
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function CogIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" /><path d="m15 5 4 4" />
    </svg>
  );
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-200 ${expanded ? "rotate-180" : ""}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
      <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
    </svg>
  );
}
