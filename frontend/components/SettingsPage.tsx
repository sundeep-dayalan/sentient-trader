import { useEffect, useRef, useState } from 'react';
import { ApiError, apiFetch } from '@/lib/api';
import { useAuth } from '@/components/AuthProvider';

// ── Types ────────────────────────────────────────────────────────────────────

interface ModelTier {
  id: string;
  label: string;
  reqDay: string;
  tpm: string;
  quality: 'high' | 'mid' | 'fallback';
}

interface OpenRouterModelConfig {
  priority: number;
  id: string;
  temperature: number;
  top_p: number;
}

interface OpenRouterRoutingConfig {
  strategy?: string;
  max_wait_seconds: number;
  default_cooldown_seconds: number;
  key_status_check_interval_seconds: number;
}

interface OpenRouterProfile {
  base_url?: string;
  routing?: OpenRouterRoutingConfig;
  models?: OpenRouterModelConfig[];
}

interface LLMProviderConfig extends OpenRouterProfile {
  type: 'groq-always-free' | 'openrouter';
  openrouter?: OpenRouterProfile;
}

interface EnhancedTradingConfig {
  bracket_orders: boolean;
  stop_loss_pct: number;
  take_profit_pct: number;
  atr_stops: boolean;
  atr_period: number;
  atr_stop_mult: number;
  atr_tp_mult: number;
  atr_stop_min_pct: number;
  atr_stop_max_pct: number;
  trailing_stops: boolean;
  trailing_stop_pct: number;
  trailing_stop_activation_pct: number;
  concentration_limits: boolean;
  max_single_ticker_pct: number;
  circuit_breaker: boolean;
  max_daily_loss_pct: number;
  dynamic_position_sizing: boolean;
  max_position_pct: number;
  feedback_loop: boolean;
  feedback_loop_lookback_days: number;
  use_limit_orders: boolean;
  limit_order_buffer_pct: number;
  price_move_gate: boolean;
  max_price_move_pct: number;
  technical_indicators: boolean;
  signal_momentum: boolean;
  source_credibility: boolean;
  market_hours_awareness: boolean;
  structured_synthesis: boolean;
}

interface AgentConfig {
  thresholds: { buy_sentiment: number; sell_sentiment: number; confidence: number };
  execution: { order_qty: number };
  llm_provider: LLMProviderConfig;
  model: { cascade: ModelTier[]; override: string | null };
  prompts: { momentum: string; value: string; risk: string; synthesis: string };
  enhanced_trading: EnhancedTradingConfig;
  consumer: { batch_size: number; poll_interval: number; error_retry: number };
}

// ── Tab registry — add future tabs here only ─────────────────────────────────

const TABS = [
  { id: 'agent-config', label: 'Agent Config', icon: <CogIcon /> },
  { id: 'enhanced-trading', label: 'Enhanced Trading', icon: <ShieldIcon /> },
  // { id: "notifications", label: "Notifications", icon: <BellIcon /> },  // future
] as const;

type TabId = (typeof TABS)[number]['id'];

const DEFAULT_OPENROUTER_BASE_URL = 'https://openrouter.ai/api/v1';
const DEFAULT_OPENROUTER_ROUTING = {
  strategy: 'ordered_fallback',
  max_wait_seconds: 600,
  default_cooldown_seconds: 60,
  key_status_check_interval_seconds: 300,
};
const DEFAULT_OPENROUTER_MODEL = {
  priority: 1,
  id: 'openai/gpt-4o-mini',
  temperature: 0.7,
  top_p: 0.7,
};

const cloneOpenRouterModels = (models?: OpenRouterModelConfig[]) =>
  models && models.length > 0
    ? models.map((model) => ({ ...model }))
    : [{ ...DEFAULT_OPENROUTER_MODEL }];

const openRouterProfileFrom = (provider: LLMProviderConfig): OpenRouterProfile => {
  const source = provider.type === 'openrouter' ? provider : provider.openrouter;
  return {
    base_url: source?.base_url || DEFAULT_OPENROUTER_BASE_URL,
    routing: { ...DEFAULT_OPENROUTER_ROUTING, ...(source?.routing ?? {}) },
    models: cloneOpenRouterModels(source?.models),
  };
};

// ── Root component ────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>('agent-config');
  const [config, setConfig] = useState<AgentConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const { isSuperUser } = useAuth();

  useEffect(() => {
    apiFetch<AgentConfig>('/agent-config')
      .then((data) => {
        setConfig(data);
        setLoading(false);
      })
      .catch((err) => {
        setFetchError(String(err));
        setLoading(false);
      });
  }, []);

  return (
    // GitHub-style: full-height panel. Sidebar + content side-by-side on md+;
    // stacked with a horizontal, scrollable tab strip on mobile.
    <div className="glass-panel flex min-h-[600px] flex-1 flex-col overflow-hidden rounded-2xl md:flex-row">
      {/* ── Nav: horizontal strip on mobile, left sidebar on md+ ──── */}
      <nav className="modern-scroll flex shrink-0 gap-1 overflow-x-auto border-b border-line px-2 py-2 md:w-52 md:flex-col md:gap-0 md:space-y-0.5 md:overflow-x-visible md:border-b-0 md:border-r md:py-4">
        <p className="mb-1 hidden px-3 text-[10px] font-semibold uppercase tracking-widest text-muted md:block">
          Settings
        </p>
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={[
              'flex shrink-0 items-center gap-2.5 whitespace-nowrap rounded-lg px-3 py-2 text-left text-sm font-medium transition-colors duration-150 md:w-full',
              activeTab === tab.id
                ? 'bg-accent-soft text-accent'
                : 'text-secondary hover:bg-hover hover:text-primary',
            ].join(' ')}
          >
            <span className={activeTab === tab.id ? 'text-accent' : 'text-muted'}>{tab.icon}</span>
            {tab.label}
          </button>
        ))}
      </nav>

      {/* ── Right content ─────────────────────────────────────────── */}
      <div className="modern-scroll min-w-0 flex-1 overflow-y-auto px-4 py-5 md:px-6 md:py-6">
        {loading && (
          <div className="flex h-48 items-center justify-center gap-2.5 text-sm text-muted">
            <span className="h-4 w-4 animate-spin rounded-full border-2 border-line border-t-accent" />
            Loading config…
          </div>
        )}
        {fetchError && (
          <div className="flex h-48 items-center justify-center text-sm text-negative">
            {fetchError}
          </div>
        )}
        {!loading && !fetchError && config && activeTab === 'agent-config' && (
          <AgentConfigTab config={config} onSave={setConfig} canEdit={isSuperUser} />
        )}
        {!loading && !fetchError && config && activeTab === 'enhanced-trading' && (
          <EnhancedTradingTab config={config} onSave={setConfig} canEdit={isSuperUser} />
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
  const [draft, setDraft] = useState<AgentConfig>(structuredClone(config));
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const openEdit = (section: string) => {
    setDraft(structuredClone(config));
    setEditing(section);
    setSaveErr(null);
  };
  const cancelEdit = () => {
    setEditing(null);
    setSaveErr(null);
  };

  const save = async (section: string) => {
    setSaving(true);
    setSaveErr(null);
    try {
      await apiFetch<{ ok: boolean }>('/agent-config', {
        method: 'POST',
        body: JSON.stringify(draft),
      });
      const updated = await apiFetch<AgentConfig>('/agent-config');
      onSave(structuredClone(updated));
      setDraft(structuredClone(updated));
      setEditing(null);
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const isEditing = (section: string) => editing === section;
  const provider = draft.llm_provider;
  const activeOpenRouterProfile = openRouterProfileFrom(provider);
  const openRouterModels =
    provider.type === 'openrouter' ? (activeOpenRouterProfile.models ?? []) : [];
  const openRouterRouting = activeOpenRouterProfile.routing ?? DEFAULT_OPENROUTER_ROUTING;

  const setProviderType = (type: LLMProviderConfig['type']) => {
    setDraft((d) => {
      const rememberedOpenRouter = openRouterProfileFrom(d.llm_provider);
      return {
        ...d,
        llm_provider:
          type === 'groq-always-free'
            ? { type: 'groq-always-free', openrouter: rememberedOpenRouter }
            : { type: 'openrouter', ...rememberedOpenRouter },
      };
    });
  };

  const updateOpenRouterModel = (index: number, patch: Partial<OpenRouterModelConfig>) => {
    setDraft((d) => {
      const models = [...(d.llm_provider.models ?? [DEFAULT_OPENROUTER_MODEL])];
      models[index] = { ...models[index], ...patch };
      return { ...d, llm_provider: { ...d.llm_provider, models } };
    });
  };

  const addOpenRouterModel = () => {
    setDraft((d) => {
      const models = d.llm_provider.models ?? [];
      const maxPriority = Math.max(0, ...models.map((model) => model.priority));
      return {
        ...d,
        llm_provider: {
          ...d.llm_provider,
          models: [
            ...models,
            {
              ...DEFAULT_OPENROUTER_MODEL,
              priority: maxPriority + 1,
              id: '',
            },
          ],
        },
      };
    });
  };

  const removeOpenRouterModel = (index: number) => {
    setDraft((d) => {
      const models = (d.llm_provider.models ?? []).filter((_, i) => i !== index);
      return {
        ...d,
        llm_provider: {
          ...d.llm_provider,
          models: models.length ? models : [DEFAULT_OPENROUTER_MODEL],
        },
      };
    });
  };

  return (
    <div className="max-w-2xl space-y-8">
      {/* Page heading */}
      <div>
        <h1 className="text-lg font-bold text-primary">Agent Config</h1>
        <p className="mt-1 text-sm text-muted">
          Tunable parameters for the trading agent. Changes are persisted to Supabase and applied on
          the next signal the agent processes.
        </p>
      </div>

      {/* ── Trade Thresholds ── */}
      <Section
        title="Trade Thresholds"
        description="A signal must clear both the directional sentiment bar and the confidence gate before an order is submitted to Alpaca."
        isEditing={isEditing('thresholds')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('thresholds') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('thresholds')}
        readOnly={!canEdit}
      >
        {isEditing('thresholds') ? (
          <div className="space-y-4">
            <NumberField
              label="Buy sentiment minimum"
              hint="Range 0.0 → 1.0 — must reach this to trigger BUY"
              value={draft.thresholds.buy_sentiment}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) =>
                setDraft((d) => ({ ...d, thresholds: { ...d.thresholds, buy_sentiment: v } }))
              }
            />
            <NumberField
              label="Sell sentiment maximum"
              hint="Range -1.0 → 0.0 — must fall below this to trigger SELL"
              value={draft.thresholds.sell_sentiment}
              min={-1}
              max={0}
              step={0.05}
              onChange={(v) =>
                setDraft((d) => ({ ...d, thresholds: { ...d.thresholds, sell_sentiment: v } }))
              }
            />
            <NumberField
              label="Confidence gate"
              hint="Range 0.0 → 1.0 — committee confidence must exceed this for any trade"
              value={draft.thresholds.confidence}
              min={0}
              max={1}
              step={0.05}
              onChange={(v) =>
                setDraft((d) => ({ ...d, thresholds: { ...d.thresholds, confidence: v } }))
              }
            />
            <NumberField
              label="Order size (shares)"
              hint="Shares per executed order — keep small for paper trading"
              value={draft.execution.order_qty}
              min={1}
              max={100}
              step={1}
              onChange={(v) =>
                setDraft((d) => ({ ...d, execution: { ...d.execution, order_qty: v } }))
              }
            />
          </div>
        ) : (
          <div className="divide-y divide-line rounded-xl border border-line">
            <ReadRow
              label="Buy sentiment min"
              value={`+${config.thresholds.buy_sentiment.toFixed(2)}`}
              colour="text-positive"
            />
            <ReadRow
              label="Sell sentiment max"
              value={`${config.thresholds.sell_sentiment.toFixed(2)}`}
              colour="text-negative"
            />
            <ReadRow
              label="Confidence gate"
              value={`${(config.thresholds.confidence * 100).toFixed(0)}%`}
              colour="text-accent"
            />
            <ReadRow
              label="Order size"
              value={`${config.execution.order_qty} share${config.execution.order_qty !== 1 ? 's' : ''}`}
            />
          </div>
        )}
      </Section>

      <Divider />

      {/* ── LLM Provider ── */}
      <Section
        title="LLM Provider"
        description="Choose one provider. The router records the exact model used for every LLM operation in each signal trace."
        isEditing={isEditing('llm-provider')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('llm-provider') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('llm-provider')}
        readOnly={!canEdit}
      >
        {isEditing('llm-provider') ? (
          <div className="space-y-4">
            <div>
              <label className="mb-1 block text-xs font-semibold text-primary">Provider</label>
              <select
                value={provider.type}
                onChange={(event) =>
                  setProviderType(event.target.value as LLMProviderConfig['type'])
                }
                className="w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
              >
                <option value="groq-always-free">Groq Always Free</option>
                <option value="openrouter">OpenRouter</option>
              </select>
              <p className="mt-1 text-[11px] text-muted">
                Groq auto-discovers free models. OpenRouter uses the ordered list below.
              </p>
            </div>

            {provider.type === 'openrouter' && (
              <div className="space-y-4">
                <TextField
                  label="OpenRouter base URL"
                  hint="OpenAI-compatible endpoint used by the OpenAI SDK."
                  placeholder={DEFAULT_OPENROUTER_BASE_URL}
                  value={provider.base_url ?? DEFAULT_OPENROUTER_BASE_URL}
                  onChange={(v) =>
                    setDraft((d) => ({
                      ...d,
                      llm_provider: { ...d.llm_provider, base_url: v },
                    }))
                  }
                />
                <div className="grid gap-4 md:grid-cols-3">
                  <NumberField
                    label="Max wait seconds"
                    hint="If all models are cooling down, wait up to this long."
                    value={openRouterRouting.max_wait_seconds}
                    min={1}
                    max={1800}
                    step={30}
                    onChange={(v) =>
                      setDraft((d) => ({
                        ...d,
                        llm_provider: {
                          ...d.llm_provider,
                          routing: { ...openRouterRouting, max_wait_seconds: v },
                        },
                      }))
                    }
                  />
                  <NumberField
                    label="Default cooldown"
                    hint="Fallback cooldown when the provider omits reset headers."
                    value={openRouterRouting.default_cooldown_seconds}
                    min={1}
                    max={600}
                    step={15}
                    onChange={(v) =>
                      setDraft((d) => ({
                        ...d,
                        llm_provider: {
                          ...d.llm_provider,
                          routing: { ...openRouterRouting, default_cooldown_seconds: v },
                        },
                      }))
                    }
                  />
                  <NumberField
                    label="Key check interval"
                    hint="How often the agent refreshes /key credit status."
                    value={openRouterRouting.key_status_check_interval_seconds}
                    min={30}
                    max={3600}
                    step={30}
                    onChange={(v) =>
                      setDraft((d) => ({
                        ...d,
                        llm_provider: {
                          ...d.llm_provider,
                          routing: {
                            ...openRouterRouting,
                            key_status_check_interval_seconds: v,
                          },
                        },
                      }))
                    }
                  />
                </div>

                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <h3 className="text-xs font-bold text-primary">OpenRouter models</h3>
                    <button
                      type="button"
                      onClick={addOpenRouterModel}
                      className="rounded-lg border border-line bg-surface px-3 py-1.5 text-xs font-medium text-secondary hover:border-accent-border hover:text-primary transition-colors"
                    >
                      Add model
                    </button>
                  </div>
                  {openRouterModels.map((model, index) => (
                    <div
                      key={`${model.priority}-${index}`}
                      className="rounded-xl border border-line p-3"
                    >
                      <div className="grid gap-3 md:grid-cols-[88px_1fr_110px_110px_auto] md:items-end">
                        <CompactNumberField
                          label="Priority"
                          hint="Lower runs first"
                          value={model.priority}
                          min={1}
                          max={20}
                          step={1}
                          onChange={(v) => updateOpenRouterModel(index, { priority: v })}
                        />
                        <TextField
                          label="Model ID"
                          hint="Example: openai/gpt-4o-mini or a :free model."
                          placeholder="openai/gpt-4o-mini"
                          value={model.id}
                          onChange={(v) => updateOpenRouterModel(index, { id: v })}
                        />
                        <CompactNumberField
                          label="Temperature"
                          hint="0.0 to 2.0"
                          value={model.temperature}
                          min={0}
                          max={2}
                          step={0.1}
                          onChange={(v) => updateOpenRouterModel(index, { temperature: v })}
                        />
                        <CompactNumberField
                          label="Top P"
                          hint="0.0 to 1.0"
                          value={model.top_p}
                          min={0}
                          max={1}
                          step={0.05}
                          onChange={(v) => updateOpenRouterModel(index, { top_p: v })}
                        />
                        <button
                          type="button"
                          onClick={() => removeOpenRouterModel(index)}
                          className="rounded-lg border border-line bg-surface px-3 py-2 text-xs font-medium text-secondary hover:border-negative-border hover:text-negative transition-colors"
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <div className="divide-y divide-line rounded-xl border border-line">
              <ReadRow
                label="Active provider"
                value={
                  config.llm_provider.type === 'openrouter' ? 'OpenRouter' : 'Groq Always Free'
                }
                badge="active"
              />
              {config.llm_provider.type === 'openrouter' && (
                <ReadRow
                  label="Base URL"
                  value={config.llm_provider.base_url ?? DEFAULT_OPENROUTER_BASE_URL}
                  mono
                />
              )}
            </div>
            <div className="space-y-2">
              {config.model.cascade.map((tier, i) => (
                <ModelTierRow key={`${tier.id}-${i}`} tier={tier} index={i + 1} />
              ))}
            </div>
          </div>
        )}
      </Section>

      {/* Persona system prompts are proprietary strategy IP — the backend only
          returns them to super-users, so only render the section when editable. */}
      {canEdit && (
      <>
      <Divider />

      {/* ── Persona Prompts ── */}
      <Section
        title="Persona System Prompts"
        description="Each of the four LLM calls uses its own system prompt. These define the analytical worldview each persona brings to the committee debate."
        isEditing={isEditing('prompts')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('prompts') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('prompts')}
        readOnly={!canEdit}
      >
        {isEditing('prompts') ? (
          <div className="space-y-4">
            {(['momentum', 'value', 'risk', 'synthesis'] as const).map((key) => (
              <PromptField
                key={key}
                label={PROMPT_LABELS[key].name}
                callOrder={PROMPT_LABELS[key].order}
                colour={PROMPT_LABELS[key].colour}
                initial={PROMPT_LABELS[key].initial}
                value={draft.prompts[key]}
                onChange={(v) => setDraft((d) => ({ ...d, prompts: { ...d.prompts, [key]: v } }))}
              />
            ))}
          </div>
        ) : (
          <div className="space-y-2">
            {(['momentum', 'value', 'risk', 'synthesis'] as const).map((key) => (
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
      </>
      )}

      <Divider />

      {/* ── Stream Consumer (read-only) ── */}
      <Section
        title="Redis Stream Consumer"
        description="Infrastructure settings for the Valkey/Redis stream consumer. Change via environment variables."
        isEditing={false}
        readOnly
      >
        <div className="divide-y divide-line rounded-xl border border-line">
          <ReadRow label="Batch size" value={`${config.consumer.batch_size} messages`} />
          <ReadRow label="Poll interval" value={`${config.consumer.poll_interval}s`} />
          <ReadRow label="Error retry" value={`${config.consumer.error_retry}s`} />
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
  title: string;
  description: string;
  isEditing: boolean;
  saving?: boolean;
  saveError?: string | null;
  readOnly?: boolean;
  onEdit?: () => void;
  onCancel?: () => void;
  onSave?: () => void;
  children: React.ReactNode;
}) {
  return (
    <section>
      {/* Section header */}
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-sm font-bold text-primary">{title}</h2>
          <p className="mt-0.5 text-xs leading-relaxed text-muted">{description}</p>
        </div>
        {!readOnly &&
          (isEditing ? (
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
                {saving && (
                  <span className="h-3 w-3 animate-spin rounded-full border border-white border-t-transparent" />
                )}
                {saving ? 'Saving…' : 'Save'}
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
          ))}
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
  label,
  hint,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
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
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="w-28 rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
        />
        <input
          type="range"
          value={value}
          min={min}
          max={max}
          step={step}
          onChange={(e) => onChange(parseFloat(e.target.value))}
          className="flex-1 accent-accent"
        />
      </div>
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function CompactNumberField({
  label,
  hint,
  value,
  min,
  max,
  step,
  onChange,
}: {
  label: string;
  hint: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-primary">{label}</label>
      <input
        type="number"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
      />
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function TextField({
  label,
  hint,
  placeholder,
  value,
  onChange,
}: {
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div>
      <label className="mb-1 block text-xs font-semibold text-primary">{label}</label>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-line bg-surface px-3 py-2 font-mono text-sm text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
      />
      <p className="mt-1 text-[11px] text-muted">{hint}</p>
    </div>
  );
}

function PromptField({
  label,
  callOrder,
  colour,
  initial,
  value,
  onChange,
}: {
  label: string;
  callOrder: number;
  colour: string;
  initial: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${el.scrollHeight}px`;
  }, [value]);

  return (
    <div>
      <div className="mb-1.5 flex items-center gap-2">
        <span
          className={`flex h-5 w-5 items-center justify-center rounded border text-[10px] font-bold ${colour}`}
        >
          {initial}
        </span>
        <label className="text-xs font-semibold text-primary">{label}</label>
        <span className="text-[10px] text-muted">call #{callOrder}</span>
      </div>
      <textarea
        ref={ref}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        rows={3}
        className="w-full resize-none overflow-hidden rounded-lg border border-line bg-surface px-3 py-2 text-xs leading-relaxed text-primary outline-none transition focus:border-accent-border focus:ring-1 focus:ring-accent/20"
      />
    </div>
  );
}

// ── Read-only display components ──────────────────────────────────────────────

function ReadRow({
  label,
  value,
  mono,
  colour,
  badge,
}: {
  label: string;
  value: string;
  mono?: boolean;
  colour?: string;
  badge?: string;
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
        <span
          className={`text-right text-xs font-semibold ${colour ?? 'text-primary'} ${mono ? 'font-mono' : ''}`}
        >
          {value}
        </span>
      </div>
    </div>
  );
}

function ModelTierRow({ tier, index }: { tier: ModelTier; index: number }) {
  const qualityColour = {
    high: 'text-positive bg-positive-soft border-positive-border',
    mid: 'text-accent bg-accent-soft border-accent-border',
    fallback: 'text-muted bg-surface-2 border-line',
  }[tier.quality];
  const qualityLabel = { high: 'Best quality', mid: 'Strong', fallback: 'Fallback' }[tier.quality];

  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3">
      <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-surface-3 font-mono text-[11px] font-bold text-muted">
        {index}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-xs font-semibold text-primary">{tier.id}</span>
          <span className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${qualityColour}`}>
            {qualityLabel}
          </span>
        </div>
        <p className="mt-0.5 text-[11px] text-muted">
          {tier.reqDay} · {tier.tpm}
        </p>
      </div>
    </div>
  );
}

const PROMPT_LABELS = {
  momentum: {
    name: 'Momentum Trader',
    order: 1,
    initial: 'M',
    colour: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  },
  value: {
    name: 'Value Investor',
    order: 2,
    initial: 'V',
    colour: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
  },
  risk: {
    name: 'Risk Manager',
    order: 3,
    initial: 'R',
    colour: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
  },
  synthesis: {
    name: 'Portfolio Manager (Synthesizer)',
    order: 4,
    initial: 'P',
    colour: 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  },
} as const;

function PromptReadCard({
  label,
  callOrder,
  colour,
  initial,
  value,
}: {
  label: string;
  callOrder: number;
  colour: string;
  initial: string;
  value: string;
}) {
  const [open, setOpen] = useState(false);

  return (
    <div className="rounded-xl border border-line bg-surface">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2.5 px-3.5 py-3 text-left"
      >
        <span
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded border text-[10px] font-bold ${colour}`}
        >
          {initial}
        </span>
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

// ── Enhanced Trading tab ──────────────────────────────────────────────────────

const ENHANCED_DEFAULTS: EnhancedTradingConfig = {
  bracket_orders: false,
  stop_loss_pct: 0.03,
  take_profit_pct: 0.06,
  atr_stops: false,
  atr_period: 14,
  atr_stop_mult: 2.0,
  atr_tp_mult: 4.0,
  atr_stop_min_pct: 0.025,
  atr_stop_max_pct: 0.12,
  trailing_stops: false,
  trailing_stop_pct: 0.03,
  trailing_stop_activation_pct: 0.02,
  concentration_limits: false,
  max_single_ticker_pct: 0.10,
  circuit_breaker: false,
  max_daily_loss_pct: 0.02,
  dynamic_position_sizing: false,
  max_position_pct: 0.05,
  feedback_loop: false,
  feedback_loop_lookback_days: 30,
  use_limit_orders: false,
  limit_order_buffer_pct: 0.005,
  price_move_gate: false,
  max_price_move_pct: 0.03,
  technical_indicators: false,
  signal_momentum: false,
  source_credibility: false,
  market_hours_awareness: false,
  structured_synthesis: false,
};

function EnhancedTradingTab({
  config,
  onSave,
  canEdit,
}: {
  config: AgentConfig;
  onSave: (updated: AgentConfig) => void;
  canEdit: boolean;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<EnhancedTradingConfig>(() => ({
    ...ENHANCED_DEFAULTS,
    ...config.enhanced_trading,
  }));
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const current = { ...ENHANCED_DEFAULTS, ...config.enhanced_trading };

  const openEdit = (section: string) => {
    setDraft({ ...ENHANCED_DEFAULTS, ...config.enhanced_trading });
    setEditing(section);
    setSaveErr(null);
  };
  const cancelEdit = () => {
    setEditing(null);
    setSaveErr(null);
  };

  const save = async (section: string) => {
    setSaving(true);
    setSaveErr(null);
    try {
      await apiFetch<{ ok: boolean }>('/agent-config', {
        method: 'POST',
        body: JSON.stringify({ enhanced_trading: draft }),
      });
      const updated = await apiFetch<AgentConfig>('/agent-config');
      onSave(structuredClone(updated));
      setDraft({ ...ENHANCED_DEFAULTS, ...updated.enhanced_trading });
      setEditing(null);
    } catch (e) {
      setSaveErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const isEditing = (section: string) => editing === section;

  const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

  return (
    <div className="max-w-2xl space-y-8">
      {/* Page heading */}
      <div>
        <h1 className="text-lg font-bold text-primary">Enhanced Trading</h1>
        <p className="mt-1 text-sm text-muted">
          Feature flags and tunable parameters for advanced trading strategies.
          Changes apply on the next signal the agent processes (~5 seconds).
        </p>
      </div>

      {/* ── Risk Management ── */}
      <Section
        title="🛡️ Risk Management"
        description="Protective measures that limit downside exposure. Bracket orders, trailing stops, position concentration limits, and daily loss circuit breakers."
        isEditing={isEditing('risk')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('risk') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('risk')}
        readOnly={!canEdit}
      >
        {isEditing('risk') ? (
          <div className="space-y-5">
            <FeatureEditCard
              label="Bracket Orders"
              description="Auto-attach stop-loss and take-profit to every BUY fill"
              enabled={draft.bracket_orders}
              onToggle={(v) => setDraft((d) => ({ ...d, bracket_orders: v }))}
            >
              <NumberField label="Stop-loss" hint="Max loss before auto-sell (0.1–50%)" value={draft.stop_loss_pct} min={0.001} max={0.5} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, stop_loss_pct: v }))} />
              <NumberField label="Take-profit" hint="Gain target to lock in (0.1–100%)" value={draft.take_profit_pct} min={0.001} max={1} step={0.01} onChange={(v) => setDraft((d) => ({ ...d, take_profit_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Volatility (ATR) Stops"
              description="Size the bracket stop to each stock's own daily range instead of a flat percent — wider for jumpy names, tighter for calm ones (requires Bracket Orders; falls back to the flat stop if ATR is unavailable)"
              enabled={draft.atr_stops}
              onToggle={(v) => setDraft((d) => ({ ...d, atr_stops: v }))}
            >
              <NumberField label="Stop multiplier" hint="Stop distance = ATR × this (≈ days of normal range)" value={draft.atr_stop_mult} min={0.5} max={10} step={0.5} onChange={(v) => setDraft((d) => ({ ...d, atr_stop_mult: v }))} />
              <NumberField label="Target multiplier" hint="Take-profit distance = ATR × this" value={draft.atr_tp_mult} min={0.5} max={20} step={0.5} onChange={(v) => setDraft((d) => ({ ...d, atr_tp_mult: v }))} />
              <NumberField label="Min stop" hint="Floor — never tighter than this" value={draft.atr_stop_min_pct} min={0.005} max={0.2} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, atr_stop_min_pct: v }))} />
              <NumberField label="Max stop" hint="Ceiling — never wider than this" value={draft.atr_stop_max_pct} min={0.02} max={0.5} step={0.01} onChange={(v) => setDraft((d) => ({ ...d, atr_stop_max_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Trailing Stops"
              description="Tighten stop-loss as the position gains, locking in profits"
              enabled={draft.trailing_stops}
              onToggle={(v) => setDraft((d) => ({ ...d, trailing_stops: v }))}
            >
              <NumberField label="Trail distance" hint="Trailing offset from high-water mark" value={draft.trailing_stop_pct} min={0.001} max={0.5} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, trailing_stop_pct: v }))} />
              <NumberField label="Activation" hint="Min profit before trail activates" value={draft.trailing_stop_activation_pct} min={0} max={0.5} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, trailing_stop_activation_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Concentration Limits"
              description="Cap max portfolio allocation to any single ticker"
              enabled={draft.concentration_limits}
              onToggle={(v) => setDraft((d) => ({ ...d, concentration_limits: v }))}
            >
              <NumberField label="Max per ticker" hint="Max % of portfolio in one ticker" value={draft.max_single_ticker_pct} min={0.01} max={1} step={0.01} onChange={(v) => setDraft((d) => ({ ...d, max_single_ticker_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Circuit Breaker"
              description="Pause trading if daily portfolio loss exceeds threshold"
              enabled={draft.circuit_breaker}
              onToggle={(v) => setDraft((d) => ({ ...d, circuit_breaker: v }))}
            >
              <NumberField label="Max daily loss" hint="Halt trading after this drawdown" value={draft.max_daily_loss_pct} min={0.001} max={0.5} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, max_daily_loss_pct: v }))} />
            </FeatureEditCard>
          </div>
        ) : (
          <div className="space-y-2">
            <FeatureReadCard label="Bracket Orders" enabled={current.bracket_orders} params={[['Stop-loss', pct(current.stop_loss_pct)], ['Take-profit', pct(current.take_profit_pct)]]} />
            <FeatureReadCard label="Volatility (ATR) Stops" enabled={current.atr_stops} params={[['Stop', `${current.atr_stop_mult}× ATR`], ['Target', `${current.atr_tp_mult}× ATR`], ['Clamp', `${pct(current.atr_stop_min_pct)}–${pct(current.atr_stop_max_pct)}`]]} />
            <FeatureReadCard label="Trailing Stops" enabled={current.trailing_stops} params={[['Trail distance', pct(current.trailing_stop_pct)], ['Activation', pct(current.trailing_stop_activation_pct)]]} />
            <FeatureReadCard label="Concentration Limits" enabled={current.concentration_limits} params={[['Max per ticker', pct(current.max_single_ticker_pct)]]} />
            <FeatureReadCard label="Circuit Breaker" enabled={current.circuit_breaker} params={[['Max daily loss', pct(current.max_daily_loss_pct)]]} />
          </div>
        )}
      </Section>

      <Divider />

      {/* ── Execution ── */}
      <Section
        title="⚡ Execution"
        description="Order execution strategies — dynamic sizing, limit orders, and price-move freshness gating to prevent chasing."
        isEditing={isEditing('execution')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('execution') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('execution')}
        readOnly={!canEdit}
      >
        {isEditing('execution') ? (
          <div className="space-y-5">
            <FeatureEditCard
              label="Dynamic Position Sizing"
              description="Scale order quantity with conviction and thesis quality"
              enabled={draft.dynamic_position_sizing}
              onToggle={(v) => setDraft((d) => ({ ...d, dynamic_position_sizing: v }))}
            >
              <NumberField label="Max position" hint="Max % of portfolio per trade" value={draft.max_position_pct} min={0.001} max={1} step={0.01} onChange={(v) => setDraft((d) => ({ ...d, max_position_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Limit Orders"
              description="Use limit orders instead of market orders for better fills"
              enabled={draft.use_limit_orders}
              onToggle={(v) => setDraft((d) => ({ ...d, use_limit_orders: v }))}
            >
              <NumberField label="Buffer" hint="Price buffer from current price (0–10%)" value={draft.limit_order_buffer_pct} min={0} max={0.1} step={0.001} onChange={(v) => setDraft((d) => ({ ...d, limit_order_buffer_pct: v }))} />
            </FeatureEditCard>

            <FeatureEditCard
              label="Price Move Gate"
              description="Block trades when stock has already moved too much since the headline"
              enabled={draft.price_move_gate}
              onToggle={(v) => setDraft((d) => ({ ...d, price_move_gate: v }))}
            >
              <NumberField label="Max move" hint="Block if price moved more than this" value={draft.max_price_move_pct} min={0.001} max={0.5} step={0.005} onChange={(v) => setDraft((d) => ({ ...d, max_price_move_pct: v }))} />
            </FeatureEditCard>
          </div>
        ) : (
          <div className="space-y-2">
            <FeatureReadCard label="Dynamic Position Sizing" enabled={current.dynamic_position_sizing} params={[['Max position', pct(current.max_position_pct)]]} />
            <FeatureReadCard label="Limit Orders" enabled={current.use_limit_orders} params={[['Buffer', pct(current.limit_order_buffer_pct)]]} />
            <FeatureReadCard label="Price Move Gate" enabled={current.price_move_gate} params={[['Max move', pct(current.max_price_move_pct)]]} />
          </div>
        )}
      </Section>

      <Divider />

      {/* ── Intelligence ── */}
      <Section
        title="🧠 Intelligence"
        description="Analytical enhancements that enrich the LLM debate context — historical feedback, technical indicators, source credibility, and more."
        isEditing={isEditing('intelligence')}
        saving={saving}
        saveError={saveErr}
        onEdit={canEdit ? () => openEdit('intelligence') : undefined}
        onCancel={cancelEdit}
        onSave={() => save('intelligence')}
        readOnly={!canEdit}
      >
        {isEditing('intelligence') ? (
          <div className="space-y-5">
            <FeatureEditCard
              label="Historical Feedback Loop"
              description="Feed past trade outcomes into the debate for learning"
              enabled={draft.feedback_loop}
              onToggle={(v) => setDraft((d) => ({ ...d, feedback_loop: v }))}
            >
              <NumberField label="Lookback days" hint="How many days of history to include (1–365)" value={draft.feedback_loop_lookback_days} min={1} max={365} step={1} onChange={(v) => setDraft((d) => ({ ...d, feedback_loop_lookback_days: Math.round(v) }))} />
            </FeatureEditCard>

            <FeatureEditCard label="Technical Indicators" description="Include RSI, MACD, Bollinger Bands in LLM context" enabled={draft.technical_indicators} onToggle={(v) => setDraft((d) => ({ ...d, technical_indicators: v }))} />
            <FeatureEditCard label="Signal Momentum" description="Aggregate sentiment across recent signals for the same ticker" enabled={draft.signal_momentum} onToggle={(v) => setDraft((d) => ({ ...d, signal_momentum: v }))} />
            <FeatureEditCard label="Source Credibility" description="Weight signals by news source track record" enabled={draft.source_credibility} onToggle={(v) => setDraft((d) => ({ ...d, source_credibility: v }))} />
            <FeatureEditCard label="Market Hours Awareness" description="Block or queue trades outside market hours" enabled={draft.market_hours_awareness} onToggle={(v) => setDraft((d) => ({ ...d, market_hours_awareness: v }))} />
            <FeatureEditCard label="Structured Synthesis" description="Use structured JSON framework for portfolio manager synthesis" enabled={draft.structured_synthesis} onToggle={(v) => setDraft((d) => ({ ...d, structured_synthesis: v }))} />
          </div>
        ) : (
          <div className="space-y-2">
            <FeatureReadCard label="Historical Feedback Loop" enabled={current.feedback_loop} params={[['Lookback', `${current.feedback_loop_lookback_days} days`]]} />
            <FeatureReadCard label="Technical Indicators" enabled={current.technical_indicators} />
            <FeatureReadCard label="Signal Momentum" enabled={current.signal_momentum} />
            <FeatureReadCard label="Source Credibility" enabled={current.source_credibility} />
            <FeatureReadCard label="Market Hours Awareness" enabled={current.market_hours_awareness} />
            <FeatureReadCard label="Structured Synthesis" enabled={current.structured_synthesis} />
          </div>
        )}
      </Section>
    </div>
  );
}

// ── Feature toggle card (edit mode) ───────────────────────────────────────────

function FeatureEditCard({
  label,
  description,
  enabled,
  onToggle,
  children,
}: {
  label: string;
  description: string;
  enabled: boolean;
  onToggle: (v: boolean) => void;
  children?: React.ReactNode;
}) {
  return (
    <div className={`rounded-xl border p-4 transition-colors duration-200 ${
      enabled
        ? 'border-positive-border bg-positive-soft/30'
        : 'border-line bg-surface'
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-primary">{label}</span>
            <StatusBadge enabled={enabled} />
          </div>
          <p className="mt-0.5 text-[11px] leading-relaxed text-muted">{description}</p>
        </div>
        <ToggleSwitch enabled={enabled} onChange={onToggle} />
      </div>
      {enabled && children && (
        <div className="mt-4 space-y-3 border-t border-line/50 pt-4">
          {children}
        </div>
      )}
    </div>
  );
}

// ── Feature read card (read-only mode) ────────────────────────────────────────

function FeatureReadCard({
  label,
  enabled,
  params,
}: {
  label: string;
  enabled: boolean;
  params?: [string, string][];
}) {
  return (
    <div className="flex items-center gap-3 rounded-xl border border-line bg-surface px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="text-xs font-semibold text-primary">{label}</span>
          <StatusBadge enabled={enabled} />
        </div>
        {enabled && params && params.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5">
            {params.map(([k, v]) => (
              <span key={k} className="text-[11px] text-muted">
                {k}: <span className="font-mono font-semibold text-secondary">{v}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Toggle switch ─────────────────────────────────────────────────────────────

function ToggleSwitch({ enabled, onChange }: { enabled: boolean; onChange: (v: boolean) => void }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      onClick={() => onChange(!enabled)}
      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors duration-200 focus:outline-none focus:ring-2 focus:ring-accent/30 ${
        enabled ? 'bg-positive' : 'bg-surface-3'
      }`}
    >
      <span
        className={`inline-block h-4 w-4 rounded-full bg-white shadow-sm transition-transform duration-200 ${
          enabled ? 'translate-x-6' : 'translate-x-1'
        }`}
      />
    </button>
  );
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ enabled }: { enabled: boolean }) {
  return enabled ? (
    <span className="rounded border border-positive-border bg-positive-soft px-1.5 py-0.5 text-[10px] font-bold text-positive">
      ON
    </span>
  ) : (
    <span className="rounded border border-line bg-surface-2 px-1.5 py-0.5 text-[10px] font-bold text-muted">
      OFF
    </span>
  );
}

function Divider() {
  return <div className="border-t border-line" />;
}

// ── Icons ─────────────────────────────────────────────────────────────────────

function CogIcon() {
  return (
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
      <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function PencilIcon() {
  return (
    <svg
      width="12"
      height="12"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </svg>
  );
}

function ChevronIcon({ expanded }: { expanded: boolean }) {
  return (
    <svg
      className={`h-3.5 w-3.5 shrink-0 text-muted transition-transform duration-200 ${expanded ? 'rotate-180' : ''}`}
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
      strokeWidth={2}
    >
      <path strokeLinecap="round" strokeLinejoin="round" d="m19 9-7 7-7-7" />
    </svg>
  );
}

function ShieldIcon() {
  return (
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
      <path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}
