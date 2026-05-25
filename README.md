# Sentient Trader

An autonomous AI trading system that reads live financial news, debates market impact across three AI personas, and executes paper trades — end to end, with zero human input in the loop.

```
Alpaca News API → Valkey Stream → LangGraph Agent → Groq LLM Committee → Alpaca Orders → Supabase → FastAPI → React Dashboard
```

---

## Table of Contents

1. [What It Does](#what-it-does)
2. [System Overview](#system-overview)
3. [Full Data Flow](#full-data-flow)
4. [Service 1 — Ingestion](#service-1--ingestion)
5. [Service 2 — Agent](#service-2--agent)
   - [LangGraph Graph](#langgraph-graph)
   - [AI Committee — The Debate](#ai-committee--the-debate)
   - [ModelRouter — Quota-Aware Cascade](#modelrouter--quota-aware-cascade)
   - [Risk Gate](#risk-gate)
   - [Signal Visibility Guarantee](#signal-visibility-guarantee)
6. [Service 3 — Frontend](#service-3--frontend)
7. [Persistence Layer](#persistence-layer)
8. [Configuration — Supabase as Single Source of Truth](#configuration--supabase-as-single-source-of-truth)
9. [Tech Stack](#tech-stack)
10. [Project Structure](#project-structure)
11. [Running Locally](#running-locally)
12. [Historical Replay Runbook](#historical-replay-runbook)
13. [Deployment](#deployment)
14. [Key Design Decisions](#key-design-decisions)

---

## What It Does

Sentient Trader monitors financial news 24/7, processes each article through a four-call AI committee that debates the market impact from three investment worldviews, then either executes a paper trade or logs a reasoned HOLD. Every decision — trade or hold — is stored in full detail and visualized in a live dashboard.

**It is not a bot that fires on keywords.** The system runs a real sequential argument: the momentum trader's opinion conditions the value investor's response, and the risk manager stress-tests both before the portfolio manager makes a final call. The full debate transcript is stored and shown in the UI.

---

## System Overview

Four deployable processes, each with a distinct failure domain:

```mermaid
graph TD
    A["🗞️ Alpaca News\nWebSocket + REST backfill"]
    B["📡 sentient-trader-ingestion\n(Oracle Cloud worker)\n• WebSocket reconnect loop\n• REST gap backfill\n• durable store + outbox\n• dedupe before Redis"]
    C[("🔴 Valkey/Redis Stream\nmarket-news\npersistent · ordered · consumer groups")]
    D["🤖 sentient-trader-agent\n(Oracle Cloud worker)\n• XREADGROUP + pending rescue\n• freshness gate + retry/DLQ\n• LangGraph committee\n• Alpaca paper trades"]
    E[("🟢 Supabase Postgres\ntrades + agent_config")]
    F["🖥️ React Dashboard\n(Netlify)\n• Live signal feed\n• PnL chart\n• Signal Injector\n• Settings"]
    G["📈 Alpaca\nPaper Trading API"]
    H["🛡️ sentient-trader-api\n(Oracle Cloud)\n• Supabase service DB access\n• Alpaca portfolio/orders\n• Redis rate limits"]

    A -->|"live stream + gap replay"| B
    B -->|"store first, then XADD"| C
    C -->|"XREADGROUP\nat-least-once"| D
    D -->|"INSERT"| E
    D -->|"paper order"| G
    F -->|"HTTPS JSON + Supabase JWT"| H
    H -->|"read/write service role"| E
    H -->|"portfolio/orders"| G
    H -->|"heartbeat / XADD / rate limit"| C

    style C fill:#dc2626,color:#fff
    style E fill:#22c55e,color:#fff
```

The services share no in-process state. A Groq rate limit on the agent doesn't affect ingestion. A noisy news day doesn't slow the LLM. A frontend deploy doesn't touch the Oracle Cloud workers.

---

## Full Data Flow

Every headline that enters the system follows this exact path:

```
1.  Alpaca News WebSocket + REST API
      └─▶ ingestion/listener.py streams live articles, reconnects on failure,
            and REST-backfills from the durable cursor with a safety overlap

2.  ingestion/store.py
      └─▶ Stores raw article payloads in Supabase, dedupes by article id,
            normalized URL, normalized headline + ticker window, and article/ticker pair

3.  ingestion/filter.py + ingestion/store.py
      └─▶ Creates durable outbox rows only for relevant article/ticker signals

4.  ingestion/producer.py
      └─▶ Publishes outbox payloads with XADD "market-news" * ticker headline source published_at
            [summary] [article_url] [article_id]; failed publishes stay retryable

5.  agent/consumer.py
      └─▶ XREADGROUP reads new entries, XAUTOCLAIM rescues idle pending entries,
            stale-news freshness gates prevent late trades, and failed analysis
            moves to an independent retry queue or DLQ instead of blocking fresh news

6.  agent/analyst.py — LangGraph state machine (see graph below)
      └─▶ check_cache → fetch_context → 4× Groq calls → assess_risk → trade/hold → log

7.  agent/logger.py
      └─▶ INSERT into Supabase "trades" table
            Full trace payload is stored server-side for the dashboard API

8.  frontend — React dashboard
      └─▶ Polls FastAPI for new trades and detail rows; the browser never talks to Valkey or server-side Supabase APIs directly
```

---

## Service 1 — Ingestion

**Location:** `backend/ingestion/`  
**Host:** Oracle Cloud worker
**Entry point:** `main.py` → `NewsListener.run()`

### How live streaming and backfill work

`listener.py` uses Alpaca's live news WebSocket for low-latency capture. On startup and after every stream disconnect, it also calls Alpaca's REST news endpoint from the last durable cursor minus a safety overlap. Dedupe makes that overlap safe: already-seen articles are recorded as duplicate events and are not published again.

The ingestion flow is store-first:

```text
Alpaca article -> normalize -> Supabase raw_news_articles -> dedupe -> news_outbox -> Redis XADD
```

If Redis is unavailable, the article remains in `news_outbox` and the retry thread republishes it later.

### Relevance filter

`filter.py` implements a fast in-process relevance check. Articles with no `symbols` field are dropped. A symbol creates an outbox row only when the ticker text appears in the headline or the headline contains high-signal market-moving language.

### Ingestion dedupe

The ingestion service suppresses duplicate publishes using four conservative rules:

- exact `provider + source_article_id`
- same normalized URL hash
- same normalized headline + same ticker within the configured window
- same stored article + same ticker outbox pair

### What gets published

Each Redis stream entry contains:

| Field          | Source                             | Always present |
| -------------- | ---------------------------------- | -------------- |
| `ticker`       | Alpaca `symbols[0]`                | Yes            |
| `headline`     | Alpaca `headline`                  | Yes            |
| `source`       | Alpaca `source`                    | Yes            |
| `published_at` | Alpaca `created_at` ISO-8601       | Yes            |
| `summary`      | Alpaca `summary` (~150–400 tokens) | When available |
| `article_url`  | Alpaca `url`                       | When available |
| `article_id`   | Alpaca `id`                        | When available |

The stream is capped at 1,000 entries by default via `XADD MAXLEN ~` (configurable with `REDIS_STREAM_MAX_LEN`). Supabase is the durable replay source; Redis is the hot queue. For historical replay tests, set `REDIS_STREAM_MAX_LEN` comfortably above the expected `news_outbox` count so the agent can drain the full replay before Redis trims older entries.

### Simulated signals

The frontend's Signal Injector (`POST /simulate` on FastAPI) bypasses ingestion entirely — the Oracle backend API publishes to the Redis Stream with `XADD` and sets `is_simulated=true`. The agent processes simulated messages identically to real ones. All four fields (ticker, headline, summary, article_url) can be provided, so simulation exercises the full summary-aware prompt path.

---

## Service 2 — Agent

**Location:** `backend/agent/`  
**Host:** Oracle Cloud worker
**Entry point:** `main.py` → `config.reload_from_supabase()` → `build_agent_graph()` → `RedisStreamConsumer.start()`

### Startup sequence

```
main.py
  1. load_dotenv()                         # load local env when present
  2. logging.basicConfig(...)              # configure before any imports that log
  3. config.reload_from_supabase()         # fetch agent_config row — crash loudly on failure
  4. graph = build_agent_graph(...)        # compile LangGraph, init Groq client + ModelRouter
  5. RedisStreamConsumer.start(...)        # consume Redis stream forever
```

`config.reload_from_supabase()` is the only place trading parameters are loaded. If the Supabase row is missing or the connection fails, the process raises `RuntimeError` and exits — it never silently runs on hardcoded defaults.

### Agent reliability gates

The agent resolves every Redis message into one of four durable outcomes:

- **processed** — analysis/trade/hold was logged successfully, then `XACK`
- **expired** — article is older than the trading window, so it logs a HOLD without spending LLM or order API budget
- **retry scheduled** — temporary failure is moved to `market-news:agent-retry`, then the original stream entry is `XACK`'d so fresh news keeps flowing
- **dead-lettered** — malformed messages, max-attempt failures, or articles beyond the audit window are written to `market-news:agent-dlq`

The consumer also uses `XAUTOCLAIM` to rescue pending entries left behind by a crashed/stalled worker. Health is written through the shared worker health key (`sentient:workers:health`) with agent-specific counters for processed, expired, retried, dead-lettered, and errored messages.

---

### LangGraph Graph

The agent pipeline is a LangGraph `StateGraph`. Every node is a pure function that reads from `AgentState` and returns a partial dict update. Nodes never call each other directly.

```mermaid
flowchart TD
    START([▶ START]) --> CC["check_cache\nSHA-256 Redis lookup"]
    CC -->|cache HIT| SKIP([⏭ END — duplicate skipped])
    CC -->|cache MISS| FC["fetch_context\nAlpaca Data API\nlive price + day change %"]
    FC --> MA["momentum_analyst\nGroq call #1\nTrend · Price action"]
    MA --> VA["value_analyst\nGroq call #2\nFundamentals · reads #1"]
    VA --> RA["risk_analyst\nGroq call #3\nTail risk · reads #1 + #2"]
    RA --> SY["synthesizer\nGroq call #4\nPortfolio Manager · reads all three"]
    SY --> AR["assess_risk\nPure Python\nsentiment + confidence thresholds"]
    AR -->|thresholds cleared| ET["execute_trade\nAlpaca paper order"]
    AR -->|HOLD| LR["log_result\nSupabase INSERT\nfull debate stored as JSONB"]
    ET --> LR
    LR --> END2([■ END])

    style MA fill:#3b82f6,color:#fff
    style VA fill:#8b5cf6,color:#fff
    style RA fill:#ef4444,color:#fff
    style SY fill:#f59e0b,color:#fff
    style ET fill:#22c55e,color:#fff
    style LR fill:#64748b,color:#fff
    style AR fill:#1e293b,color:#fff
```

**AgentState fields** (accumulated across nodes):

```python
news:             NewsMessage       # immutable input
is_cached:        bool
market_context:   {price, day_change_pct} | None
momentum_opinion: PersonaAnalysis | None
value_opinion:    PersonaAnalysis | None
risk_opinion:     PersonaAnalysis | None
analysis:         TradeAnalysis | None   # assembled by synthesizer
should_trade:     bool
trade_order_id:   str | None
error:            str | None
is_simulated:     bool
```

#### Node: check_cache

SHA-256 hashes the headline text and checks Redis with a 5-minute TTL. If the hash exists → cached HIT → route to END. This prevents the same headline from firing twice in the same news cycle (Alpaca can return the same article across consecutive polls if it straddles the boundary).

#### Node: fetch_context

Calls Alpaca's Stock Snapshot endpoint for the ticker. Returns `{price, day_change_pct}` or `None` on any failure (non-standard tickers in simulate mode, market closed, network error). Fails gracefully — a missing price doesn't abort analysis, it just degrades the prompt context.

The day-change context matters substantively: a +8% NVDA headline reads differently on a day where NVDA is already +8% (momentum crowded) vs. a flat day (genuine surprise).

---

### AI Committee — The Debate

Four sequential Groq calls. Each call uses `instructor.from_groq(..., mode=instructor.Mode.JSON)` for schema-validated structured output.

`Mode.JSON` is used instead of `Mode.TOOLS` because Groq's tool-calling implementation is unreliable — models sometimes invent tool names or return plain text when tool use is required. JSON mode produces consistent results.

#### What every prompt includes

```
MARKET: {ticker} @ ${price} ({day_change_pct:+.2f}% today)
HEADLINE: "{headline}" — {source}

ARTICLE SUMMARY:           ← only when Alpaca provides a summary field
{summary text, ~150–400 tokens}
```

```mermaid
sequenceDiagram
    participant N as 📰 NewsMessage<br/>(headline + summary + price)
    participant M as 🟦 Momentum Trader<br/>Groq Call #1
    participant V as 🟣 Value Investor<br/>Groq Call #2
    participant R as 🔴 Risk Manager<br/>Groq Call #3
    participant S as 🟡 Portfolio Manager<br/>Groq Call #4 (Synthesizer)
    participant DB as 🟢 Supabase<br/>trades table

    N->>M: headline + market price + summary
    M-->>V: stance + conviction + full reasoning
    N->>V: headline + market price + summary
    V-->>R: own stance + conviction + reasoning
    N->>R: headline + market price + summary
    R-->>S: all three opinions in full
    N->>S: headline + market price + summary
    S-->>DB: action (BUY/SELL/HOLD) + sentiment + confidence + full decision trace
```

#### Call #1 — Momentum Trader

**System prompt:** Conditions the model to think purely in terms of price action, trend momentum, and technical signals. Told to ignore long-term fundamentals.

**User prompt:** The market line + headline + summary only. No prior opinions. This is an unconditioned, first-read reaction.

**Output:** `PersonaAnalysis` → `{stance: BULLISH|BEARISH|NEUTRAL, conviction: float 0–1, headline_take: str, analysis: str}`

#### Call #2 — Value Investor

**System prompt:** Long-horizon fundamental lens. Told to look past short-term noise and evaluate intrinsic value impact.

**User prompt:** Market line + headline + summary + the **full text of the Momentum Trader's opinion** (stance, conviction, full take and reasoning). The value investor reacts to what the momentum trader actually said — genuine disagreement surfaces here when they diverge.

**Output:** Same `PersonaAnalysis` schema.

#### Call #3 — Risk Manager

**System prompt:** Adversarial mandate. Told to find the flaw in both prior arguments. Stress-test for tail risk, regulatory exposure, macro factors, liquidity risk.

**User prompt:** Market line + headline + summary + **both prior opinions** in full. The risk manager reads the complete debate so far.

**Output:** Same `PersonaAnalysis` schema.

#### Call #4 — Portfolio Manager (Synthesizer)

**System prompt:** Weighs the committee's views. Told to consider conviction scores — a high-conviction BEARISH from risk should suppress a bullish consensus. Must resolve split decisions explicitly.

**User prompt:** Market line + headline + summary + **all three opinions** formatted as a debate transcript.

**Output:** `SynthesisResult` → `{sentiment: float -1 to 1, confidence: float 0–1, action: BUY|SELL|HOLD, reasoning: str}`

The synthesizer then assembles the final `TradeAnalysis`:

```python
TradeAnalysis(
    committee = [momentum_opinion, value_opinion, risk_opinion],  # full PersonaOpinion objects
    sentiment  = synthesis.sentiment,
    confidence = synthesis.confidence,
    action     = synthesis.action,
    reasoning  = synthesis.reasoning,
)
```

This is what gets stored in Supabase as the signal record. The top-level trade columns keep the dashboard fast, while the complete raw Decision Core audit trail is preserved as JSONB in `decision_trace`: exact LLM messages, structured outputs, committee debate, Portfolio Manager synthesis, risk gate, and execution metadata.

---

### ModelRouter — Quota-Aware Cascade

Every LLM call goes through `ModelRouter.call()`, which discovers active Groq models from `/openai/v1/models` at startup and ranks candidates with a local policy. The endpoint changes over time, so the router does not require a hardcoded model list.

**Auto-ranking policy:**

| Step | Rule                                                                                                                                         |
| ---- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| 1    | Keep only `active: true` models with enough context and completion capacity                                                                  |
| 2    | Exclude non-analysis systems: audio/transcription, prompt guards, safeguards, TTS/speech, and Groq compound systems                          |
| 3    | Score candidates by parameter size, context window, max completion tokens, instruction/reasoning signals, and known general-purpose families |
| 4    | Sort by score and use that as the cascade                                                                                                    |
| 5    | If `GROQ_MODEL_PINNED_ORDER` is set, try those active models first, then append the auto-ranked remainder                                    |

Groq's models endpoint does not expose per-day token quota or subjective quality, so runtime fallback still matters: a model that is active but quota-limited is cooled down and the router moves to the next candidate.

**Groq limit/failure types handled by the router:**

| Error type              | Detection                                     | Behaviour                                                                                |
| ----------------------- | --------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Per-minute (RPM/TPM)    | `429` without "per day"/"daily" in message    | Cool down that model using Groq's retry-after value, then fall back within the same call |
| Daily token quota (TPD) | `429` with "per day"/"daily"/"TPD" in message | Cool down that model using Groq's retry-after value, then fall back within the same call |
| Missing model           | `404 model_not_found` or equivalent text      | Disable that model for the process and continue to the next configured tier              |

```mermaid
flowchart LR
    START["Agent startup"] --> MODELS["Fetch Groq /models"]
    MODELS --> FILTER["Filter text-analysis candidates"]
    FILTER --> RANK["Score and auto-rank"]
    RANK --> CALL["LLM call requested"]
    CALL --> NEXT["Try next available model"]
    NEXT --> OK{"Success?"}
    OK -->|"yes"| RETURN["Return parsed response + model"]
    OK -->|"429"| COOL["Set retry-after cooldown"]
    OK -->|"404 model_not_found"| DISABLE["Disable model for process"]
    COOL --> NEXT
    DISABLE --> NEXT
    NEXT -->|"none available"| WAIT["Wait for soonest cooldown up to 10m"]
    WAIT --> NEXT

    style RETURN fill:#22c55e,color:#fff
```

**Key implementation detail — dynamic availability + retry-after cooldown:**

```python
self.tiers = _resolve_model_tiers(config.GROQ_MODEL_PINNED_ORDER)
self._cooldown_until: dict[str, float] = {}
self._disabled_models: set[str] = set()

# In call():
available = [
    m for m in self.tiers
    if m not in self._disabled_models
    and time.time() >= self._cooldown_until.get(m, 0)
]

# On 429:
self._cooldown_until[model] = time.time() + retry_after
continue  # try next tier in the same call()
```

Because the fallback happens inside the same `call()` invocation, if Call #3 (Risk Analyst) hits a token limit on the first-ranked candidate, the risk analyst can still get a valid response from the next available tier in that same call. Once Groq's retry-after window expires, the cooled-down model re-enters the pool.

**Hard override:** If `MODEL_OVERRIDE` is set in agent_config (Supabase), the entire cascade is bypassed and every call goes to that specific model. Useful for local testing.

**Operator preference:** Set `GROQ_MODEL_PINNED_ORDER=openai/gpt-oss-120b,qwen/qwen3-32b` only when you want to force a short preferred prefix. Leaving it empty keeps the router fully auto-ranked from Groq's active model list.

---

### Risk Gate

`assess_risk` node — pure Python, no LLM call.

```python
is_strong_buy  = action == "BUY"  and sentiment >= config.BUY_SENTIMENT_THRESHOLD
is_strong_sell = action == "SELL" and sentiment <= config.SELL_SENTIMENT_THRESHOLD
is_confident   = confidence >= config.CONFIDENCE_THRESHOLD

should_trade = (is_strong_buy or is_strong_sell) and is_confident
```

Requires **both** a strong directional signal AND high confidence. The synthesizer prompt is designed to produce lower confidence on split committee votes, so a 2-vs-1 debate is naturally harder to clear this gate than a unanimous one.

Default thresholds (seeded in Supabase, editable via Settings UI):

| Parameter                  | Default |
| -------------------------- | ------- |
| `BUY_SENTIMENT_THRESHOLD`  | 0.65    |
| `SELL_SENTIMENT_THRESHOLD` | -0.65   |
| `CONFIDENCE_THRESHOLD`     | 0.70    |

---

### Signal Visibility Guarantee

Every unique headline that enters the graph **always produces a row in the `trades` table**, regardless of how analysis went.

| Outcome                                                       | `trade_action` written | `reasoning`                               |
| ------------------------------------------------------------- | ---------------------- | ----------------------------------------- |
| Full debate, threshold cleared                                | `BUY` or `SELL`        | Synthesizer's reasoning text              |
| Full debate, threshold not cleared                            | `HOLD`                 | Synthesizer's reasoning text              |
| Partial debate (some persona calls failed), threshold cleared | `BUY` or `SELL`        | Synthesizer reasons on available opinions |
| All LLM calls failed / all models exhausted                   | `HOLD`                 | `"Analysis skipped — <error message>"`    |

Cached (duplicate) headlines are the only case that produces no new row — they were already logged the first time through.

---

## Service 3 — Frontend

**Location:** `frontend/`  
**Host:** Netlify
**Framework:** React + Vite + Tailwind CSS

### Views

The React app is a static single-page application. Netlify serves `dist/`, and the SPA redirect sends every route back to `index.html`.

| View                | What it shows                                                                                                 |
| ------------------- | ------------------------------------------------------------------------------------------------------------- |
| `/`                 | Live signal feed, PnL chart, pipeline diagram, dashboard stats                                                |
| Signal detail panel | Full signal detail: all three persona opinions, conviction bars, article link, tooltips on every trading term |
| Settings panel      | Live-edit all agent config values (thresholds, system prompts, model override, order qty)                     |

### Backend API

| Route            | Method | Purpose                                                                      |
| ---------------- | ------ | ---------------------------------------------------------------------------- |
| `/auth/me`       | GET    | Validates the Supabase access token and returns dashboard role flags         |
| `/simulate`      | POST   | Authenticates the user, rate-limits with Valkey, and injects a test headline |
| `/status`        | GET    | Combines Supabase, Alpaca, Redis, Groq, and agent heartbeat checks           |
| `/agent-config`  | GET    | Reads current agent_config row from Supabase                                 |
| `/agent-config`  | POST   | Super-user only; writes updated config with the Supabase service role        |
| `/stats`         | GET    | Aggregated dashboard stats                                                   |
| `/portfolio`     | GET    | Alpaca paper trading portfolio history                                       |
| `/orders`        | GET    | Alpaca account, positions, and order list                                    |
| `/orders/cancel` | POST   | Super-user only; cancels selected Alpaca orders                              |
| `/trades`        | GET    | Paginated trade summaries and polling cursor support                         |
| `/trades/{id}`   | GET    | Trade detail plus Decision Core trace                                        |

### Live feed

`DashboardClient.tsx` polls FastAPI for new rows using the latest known `created_at` cursor. The browser only uses Supabase for auth; all DB reads, config writes, Alpaca calls, and Redis operations stay inside the Oracle Cloud backend.

### Signal detail view

`AgentMonologue.tsx` renders the `decision_trace` JSONB stored per signal:

- Stance badge (BULLISH / BEARISH / NEUTRAL) with color coding
- Conviction bar (0–100% filled)
- Full reasoning text per persona
- Synthesizer action badge (BUY / SELL / HOLD) with sentiment and confidence scores
- Raw LLM operation trace with the exact messages and structured response for each Decision Core call
- Info icon tooltips on every trading term (sentiment, confidence, conviction, stance, consensus, action, paper trading)

### Signal Injector

`CustomNewsForm.tsx` accepts all four fields that Alpaca provides:

- **Ticker** (required) — validated to uppercase alpha only
- **Headline** (required, min 10 chars)
- **Article Summary** (optional) — gives personas richer context, same as a real Alpaca summary
- **Article URL** (optional) — stored and linked in the signal detail view

Submits to `/simulate` on FastAPI. The backend API rate-limits the user and publishes to Redis. The full pipeline fires within seconds and the new signal appears in the live feed.

---

## Persistence Layer

| Store            | Technology                        | Used for                                                             |
| ---------------- | --------------------------------- | -------------------------------------------------------------------- |
| Raw news archive | Supabase `raw_news_articles`      | Durable source payloads, normalized fields, dedupe links             |
| Ingestion outbox | Supabase `news_outbox`            | Retryable Redis publish queue                                        |
| Message bus      | Valkey/Redis Stream `market-news` | Hot ordered queue between ingestion and agent                        |
| Agent retry      | Valkey sorted set + hash          | Failed analysis retries outside the hot stream                       |
| Agent DLQ        | Valkey/Redis Stream               | Malformed, exhausted, or too-stale messages for operator inspection  |
| Agent cache      | Valkey/Redis                      | Last-mile duplicate LLM suppression with `HeadlineCache`             |
| Signal log       | Supabase `trades` table           | Every decision summary, linked to full Decision Core trace storage   |
| Config           | Supabase `agent_config` table     | Single row (id=1) — all trading parameters, editable via Settings UI |

### Supabase `trades` table schema

| Column             | Type          | Notes                                                                                                               |
| ------------------ | ------------- | ------------------------------------------------------------------------------------------------------------------- |
| `id`               | `uuid`        | Primary key                                                                                                         |
| `created_at`       | `timestamptz` | Auto                                                                                                                |
| `ticker`           | `text`        |                                                                                                                     |
| `headline`         | `text`        |                                                                                                                     |
| `sentiment_score`  | `float4`      | -1.0 to 1.0                                                                                                         |
| `confidence_score` | `float4`      | 0.0 to 1.0                                                                                                          |
| `reasoning`        | `text`        | Synthesizer's final reasoning                                                                                       |
| `trade_action`     | `text`        | `BUY` / `SELL` / `HOLD`                                                                                             |
| `order_id`         | `text`        | Alpaca order ID (null if HOLD)                                                                                      |
| `quantity`         | `int4`        | Shares ordered                                                                                                      |
| `is_simulated`     | `bool`        | True for Signal Injector submissions                                                                                |
| `article_source`   | `text`        | News source name                                                                                                    |
| `article_url`      | `text`        | Link to original article                                                                                            |
| `article_id`       | `text`        | Alpaca article ID                                                                                                   |
| `decision_trace`   | `jsonb`       | Generic Decision Core trace: LLM inputs/outputs, committee debate, Portfolio Manager decision, risk gate, execution |

### Redis Stream message format

```
XADD market-news * ticker TSLA headline "Tesla misses Q3..." source alpaca published_at 2026-05-10T12:00:00Z summary "Tesla reported..." article_url "https://..." article_id "12345" is_simulated false
```

Consumer reads with `XREADGROUP` — at-least-once delivery. On success, expiry logging, retry scheduling, or DLQ write, the entry is `XACK`'d. `XAUTOCLAIM` rescues idle pending entries left behind by a crashed/stalled worker.

---

## Configuration — Supabase as Single Source of Truth

All agent parameters live in a single row in `agent_config` (id=1). Python declares them as type annotations without assignment — the process has no fallback defaults and will raise `AttributeError` if any value is missing from Supabase.

```python
# config.py — declared, never assigned at module level
BUY_SENTIMENT_THRESHOLD:  float
SELL_SENTIMENT_THRESHOLD: float
CONFIDENCE_THRESHOLD:     float
ORDER_QTY:                int
MODEL_OVERRIDE:           str | None
MOMENTUM_SYSTEM_PROMPT:   str
VALUE_SYSTEM_PROMPT:      str
RISK_SYSTEM_PROMPT:       str
SYNTHESIS_SYSTEM_PROMPT:  str
```

`reload_from_supabase()` fetches the row at startup and binds all values via `global`. If the row is missing or the connection fails, the process exits with a `RuntimeError` — by design, so failures are loud and visible in worker logs immediately.

### Editing config without redeploying

The Settings page in the dashboard writes to `agent_config` via `POST /agent-config` on FastAPI. The new values take effect on the next agent process restart. The agent does not hot-reload config at runtime — a restart is intentional so config changes are auditable in deployment history.

### Why FastAPI owns Settings writes

The React app sends the user's Supabase access token to FastAPI. FastAPI validates that token against Supabase Auth, checks `SUPER_USER_EMAILS`, and performs the update with the Supabase service role key inside Oracle Cloud. Netlify never receives the service role key, Alpaca secrets, or direct Valkey access.

---

## Tech Stack

| Layer          | Technology                                      | Why                                                                                            |
| -------------- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------- |
| AI reasoning   | Groq API (`instructor` JSON mode)               | Sub-second structured LLM output; `instructor` handles Pydantic validation + retries           |
| Agent pipeline | LangGraph 0.2 StateGraph                        | Explicit conditional routing, composable nodes, no hidden side-effects                         |
| Message bus    | Valkey/Redis Streams                            | At-least-once delivery, consumer groups, persistent backlog                                    |
| Market data    | Alpaca News REST + Data API + Paper Trading API | Free tier; news, live prices, and paper orders in one platform                                 |
| Database       | Supabase Postgres                               | JSONB for Decision Core traces; all server-side reads and writes go through FastAPI or workers |
| Backend deploy | Oracle Cloud API + workers                      | Keeps private Valkey access inside the Oracle subnet                                           |
| Frontend       | React + Vite + Tailwind CSS                     | Static Netlify deploy; browser talks only to FastAPI and Supabase Auth                         |
| Charts         | Recharts                                        | PnL equity curve and stats panels                                                              |

---

## Project Structure

```
sentient-trader/
│
├── backend/
│   ├── api/
│   │   ├── main.py          # FastAPI service for /status, /simulate, and Redis rate limits
│   │   ├── redis_client.py  # Valkey/Redis client factory
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── ingestion/
│   │   ├── main.py          # Entry point — starts NewsListener
│   │   ├── listener.py      # WebSocket loop, REST backfill, outbox retry
│   │   ├── backfill.py      # Alpaca REST gap recovery
│   │   ├── store.py         # Supabase raw article store, dedupe, outbox
│   │   ├── models.py        # Article normalization and hash helpers
│   │   ├── health.py        # Redis-backed ingestion health state
│   │   ├── filter.py        # Ticker relevance filter (keyword matching)
│   │   ├── producer.py      # RedisStreamProducer — XADD to Redis Stream
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   │
│   ├── agent/
│       ├── main.py          # Entry point — reload config, build graph, start consumer
│       ├── analyst.py       # Full LangGraph graph: all nodes, ModelRouter, AI committee
│       ├── consumer.py      # XREADGROUP consumer loop — one message at a time
│       ├── config.py        # Module-level type annotations + reload_from_supabase()
│       ├── trader.py        # AlpacaTrader — wraps alpaca-py order submission
│       ├── cache.py         # HeadlineCache — SHA-256 dedup in Redis, 5-min TTL
│       ├── logger.py        # SupabaseLogger — inserts into trades table
│       ├── schemas.py       # Pydantic models: NewsMessage, PersonaAnalysis, TradeAnalysis, etc.
│       ├── requirements.txt
│       └── Dockerfile
│   │
│   └── shared/
│       └── worker_health.py # Shared Redis worker health helpers
│
├── frontend/
│   ├── index.html                       # Vite HTML shell
│   ├── main.tsx                         # React root + AuthProvider
│   ├── DashboardClient.tsx              # Dashboard SPA state and polling
│   ├── globals.css                      # Tailwind + theme variables
│   ├── vite.config.ts
│   │
│   ├── components/
│   │   ├── AgentMonologue.tsx    # Full signal detail: personas, conviction, tooltips
│   │   ├── CustomNewsForm.tsx    # Signal Injector form (ticker/headline/summary/url)
│   │   ├── PnLChart.tsx          # Recharts equity curve
│   │   ├── PipelineViz.tsx       # React Flow interactive pipeline diagram
│   │   └── TradeCard.tsx         # Individual signal card in the feed
│   │
│   ├── lib/
│   │   ├── api.ts                # FastAPI client with Supabase bearer token forwarding
│   │   ├── supabase-browser.ts   # Supabase browser auth client
│   │   └── types.ts
│   │
│   └── package.json
│
├── supabase/
│   └── migrations/
│       └── 001_current_schema.sql # current sentient_trader schema baseline
│
└── README.md
```

---

## Running Locally

### Prerequisites

- Python 3.11+
- Node.js 20+
- Valkey or Redis instance available at `REDIS_HOST` / `REDIS_PORT`
- Supabase project (free tier works)
- Alpaca account (free paper trading)
- Groq API key (free tier works)

`127.0.0.1` works when Valkey is running on the same host/network namespace as the process. For Docker or hosted workers, set `REDIS_HOST` to the reachable service hostname or private network address.

### 1. Apply Supabase migrations

Run `supabase/migrations/001_current_schema.sql` via the Supabase SQL Editor.

### 2. Agent service

```bash
cd backend/agent
pip install -r requirements.txt

# Required env vars:
export SUPABASE_URL=https://xxx.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=...
export SUPABASE_DB_SCHEMA=sentient_trader
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_STREAM_KEY=market-news          # default
export REDIS_CONSUMER_START_ID=0             # catch up retained stream entries on first boot
export WORKER_HEALTH_KEY=sentient:workers:health
export AGENT_WORKER_NAME=agent
export AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS=900
export AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS=86400
export AGENT_MAX_PROCESSING_ATTEMPTS=3
export AGENT_RETRY_BASE_DELAY_SECONDS=30
export AGENT_RETRY_MAX_DELAY_SECONDS=300
export AGENT_RETRY_BATCH_SIZE=5
export AGENT_PENDING_IDLE_SECONDS=60
export AGENT_PENDING_BATCH_SIZE=5
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...
export GROQ_API_KEY=...

python main.py
```

### 3. Ingestion service

```bash
cd backend/ingestion
pip install -r requirements.txt

# Required env vars:
export SUPABASE_URL=https://xxx.supabase.co
export SUPABASE_SERVICE_ROLE_KEY=...
export SUPABASE_DB_SCHEMA=sentient_trader
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_STREAM_KEY=market-news
export REDIS_STREAM_MAX_LEN=1000
export WORKER_HEALTH_KEY=sentient:workers:health
export INGESTION_WORKER_NAME=ingestion
export INGESTION_LIVE_ENABLED=true
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...
export ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets
export TICKER_META_HASH_KEY=sentient:ticker:meta
export TICKER_DIRECTORY_STATE_KEY=sentient:ticker:directory:state
export TICKER_ALIAS_OVERRIDES_KEY=sentient:ticker:alias-overrides
export TICKER_DIRECTORY_REFRESH_SECONDS=86400
export TICKER_DIRECTORY_REFRESH_CHECK_SECONDS=60
export TICKER_PUBLISH_SCORE_THRESHOLD=80

python main.py
```

### 4. Backend API

```bash
cd backend/api
pip install -r requirements.txt

# Required env vars:
export CORS_ORIGINS=http://localhost:3000
export SUPABASE_URL=https://xxx.supabase.co
export SUPABASE_ANON_KEY=...
export SUPABASE_SERVICE_ROLE_KEY=...
export SUPABASE_DB_SCHEMA=sentient_trader
export SUPER_USER_EMAILS=you@example.com
export REDIS_HOST=127.0.0.1
export REDIS_PORT=6379
export REDIS_DB=0
export REDIS_STREAM_KEY=market-news
export REDIS_STREAM_MAX_LEN=1000
export WORKER_HEALTH_KEY=sentient:workers:health
export AGENT_WORKER_NAME=agent
export ALPACA_API_KEY=...
export ALPACA_SECRET_KEY=...
export ALPACA_BASE_URL=https://paper-api.alpaca.markets
export GROQ_API_KEY=...

uvicorn main:app --host 0.0.0.0 --port 8000
```

### 5. Frontend

```bash
cd frontend
npm install

# .env.local:
VITE_SUPABASE_URL=https://xxx.supabase.co
VITE_SUPABASE_ANON_KEY=...
VITE_SUPABASE_DB_SCHEMA=sentient_trader
VITE_BACKEND_API_URL=http://127.0.0.1:8000

npm run dev
# open http://localhost:3000
```

### Testing the pipeline without waiting for news

Open the dashboard → Signal Injector panel → enter a ticker and headline → click Inject. The signal travels through the full pipeline and appears in the live feed within a few seconds.

---

## Historical Replay Runbook

Historical replay is for reliability testing. It exercises the normal ingestion
pipeline from Alpaca REST news through normalization, durable storage, dedupe,
ticker selection, outbox, Redis Stream, agent processing, Supabase logging, and
optional paper order submission. It intentionally does not test the Alpaca live
WebSocket connection.

### Replay environment overrides

Use these overrides before running a multi-day replay. The example values are
for a 10-day replay.

| Env var | Normal live value | Replay value | Why |
| --- | --- | --- | --- |
| `INGESTION_LIVE_ENABLED` | `true` | `false` | Prevent live WebSocket/backfill news from mixing with replay data. |
| `REDIS_STREAM_MAX_LEN` | `1000` | `20000` | Redis uses `XADD MAXLEN ~`; the cap must be higher than expected `news_outbox` rows or replay entries can be trimmed before the agent reads them. |
| `AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS` | `900` | `1209600` | Lets 10-day historical articles go through the full agent/trade path instead of being logged as expired HOLDs. |
| `AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS` | `86400` | `1209600` | Prevents older replay articles from being dead-lettered before audit logging. |
| `REDIS_CONSUMER_START_ID` | `0` | `0` | New consumer groups should start from retained stream history. Do not use `$` for replay. |

For a different replay window, set the two agent age values to at least the
window length plus runtime cushion. For example, 14 days is `1209600` seconds.

### Preflight reset

Only run this against a disposable paper-trading test environment. Preserve
`agent_config`; the agent needs it at startup.

```sql
truncate table
  sentient_trader.trade_decision_traces,
  sentient_trader.trades,
  sentient_trader.news_outbox,
  sentient_trader.news_article_symbols,
  sentient_trader.raw_news_articles,
  sentient_trader.ingestion_events,
  sentient_trader.ingestion_cursors
restart identity cascade;
```

If the Redis database is dedicated to this app, clear replay state and cache:

```redis
FLUSHDB
```

Restart/redeploy the services after changing env vars. Before replay, verify:

```redis
XLEN market-news
XINFO GROUPS market-news
ZCARD market-news:agent-retry
XLEN market-news:agent-dlq
HGET sentient:workers:health ingestion
HGET sentient:workers:health agent
```

Expected preflight state:

- `XLEN market-news` is `0`
- retry and DLQ are `0`
- ingestion health phase is `live_paused`
- agent health phase is `polling`
- ticker directory has loaded assets

### Dry run

Run a small dry run first. It fetches Alpaca news and prints counts without
writing Supabase or Redis.

```bash
cd backend/ingestion
python replay_historical.py --days 1 --dry-run
```

### Real replay

Run the real replay from the ingestion container or local ingestion environment:

```bash
cd backend/ingestion
python replay_historical.py --days 10 --max-pages 300 --confirm-replay
```

The completion summary should show nonzero `raw_news_articles`,
`news_outbox`, `ingestion_events`, and `redis_stream`. With
`REDIS_STREAM_MAX_LEN=20000`, `redis_stream` should be near the published
outbox count for a 10-day replay. If it is around `1000`, the stream cap is
still too low or the env var is not reaching the ingestion process.

### During-run checks

Redis:

```redis
XINFO STREAM market-news
XINFO GROUPS market-news
XPENDING market-news sentient-agent-group
ZCARD market-news:agent-retry
XLEN market-news:agent-dlq
HGET sentient:workers:health agent
HGET sentient:workers:health ingestion
```

Healthy signs:

- `XINFO STREAM entries-added` rises with published outbox rows
- `XINFO STREAM length` stays comfortably above `1000` for large replays
- `lag` may rise while replay publishes faster than the agent drains
- `pending` stays small
- retry and DLQ remain `0`
- agent `messages_processed` increases
- ingestion `articles_seen` and `articles_published` increase

Supabase:

```sql
select status, count(*)
from sentient_trader.news_outbox
group by status
order by status;
```

During a healthy replay, `PUBLISHED` should rise and `PENDING` /
`RETRYING` / `FAILED` should not accumulate.

### Post-run checks

Once the replay command finishes, ingestion is done but the agent may still be
draining Redis. Wait for Redis lag to approach zero:

```redis
XINFO GROUPS market-news
ZCARD market-news:agent-retry
XLEN market-news:agent-dlq
HGET sentient:workers:health agent
```

Then validate the database:

```sql
select status, count(*)
from sentient_trader.news_outbox
group by status
order by status;

select coalesce(dedupe_reason, 'not_duplicate') as dedupe_reason,
       is_duplicate,
       count(*)
from sentient_trader.raw_news_articles
group by dedupe_reason, is_duplicate
order by count(*) desc;

select 'trades_total' as metric, count(*)::bigint as value
from sentient_trader.trades
union all
select 'traces_total', count(*)::bigint
from sentient_trader.trade_decision_traces
union all
select 'outbox_published', count(*)::bigint
from sentient_trader.news_outbox
where status = 'PUBLISHED';

select trade_action, count(*)
from sentient_trader.trades
group by trade_action
order by count(*) desc;

select count(*) as trades_without_trace
from sentient_trader.trades t
left join sentient_trader.trade_decision_traces d on d.trade_id = t.id
where d.trade_id is null;
```

Expected post-run state:

- all outbox rows are `PUBLISHED`
- retry and DLQ are `0`
- Redis group `lag` is near `0`
- `trades_total` and `traces_total` are close to `outbox_published`
- `trades_without_trace` is `0`

If `outbox_published` is much larger than `trades_total` while `XINFO STREAM
length` is near `1000`, Redis trimmed the replay before the agent could read it.
Increase `REDIS_STREAM_MAX_LEN`, flush/reset, and rerun.

### Revert after replay

After the replay test is complete, restore normal live settings and redeploy or
restart affected services:

```env
INGESTION_LIVE_ENABLED=true
REDIS_STREAM_MAX_LEN=1000
AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS=900
AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS=86400
REDIS_CONSUMER_START_ID=0
```

If the replay database should not remain visible in the dashboard, run the
preflight reset again after exporting any evidence you need. If the Alpaca paper
account was used for order placement, cancel open orders and close/reset paper
positions before returning to live ingestion.

---

## Deployment

### Oracle Cloud backend services

```bash
# All Oracle backend services need the private Redis settings:
  REDIS_HOST=127.0.0.1 \
  REDIS_PORT=6379 \
  REDIS_DB=0 \
  REDIS_STREAM_KEY=market-news \
  REDIS_STREAM_MAX_LEN=1000 \
  WORKER_HEALTH_KEY=sentient:workers:health

# backend/api additionally needs:
  CORS_ORIGINS=https://your-netlify-site.netlify.app \
  AGENT_WORKER_NAME=agent \
  SUPABASE_URL=... \
  SUPABASE_ANON_KEY=... \
  SUPABASE_SERVICE_ROLE_KEY=... \
  SUPABASE_DB_SCHEMA=sentient_trader \
  SUPER_USER_EMAILS=you@example.com \
  ALPACA_API_KEY=... \
  ALPACA_SECRET_KEY=... \
  ALPACA_BASE_URL=https://paper-api.alpaca.markets \
  GROQ_API_KEY=...

# backend/agent additionally needs:
  SUPABASE_URL=...
  SUPABASE_SERVICE_ROLE_KEY=...
  SUPABASE_DB_SCHEMA=sentient_trader
  REDIS_CONSUMER_START_ID=0
  WORKER_HEALTH_KEY=sentient:workers:health
  AGENT_WORKER_NAME=agent
  AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS=900
  AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS=86400
  AGENT_MAX_PROCESSING_ATTEMPTS=3
  AGENT_RETRY_BASE_DELAY_SECONDS=30
  AGENT_RETRY_MAX_DELAY_SECONDS=300
  AGENT_RETRY_BATCH_SIZE=5
  AGENT_PENDING_IDLE_SECONDS=60
  AGENT_PENDING_BATCH_SIZE=5
  ALPACA_API_KEY=...
  ALPACA_SECRET_KEY=...
  GROQ_API_KEY=...

# backend/ingestion additionally needs:
  SUPABASE_URL=...
  SUPABASE_SERVICE_ROLE_KEY=...
  SUPABASE_DB_SCHEMA=sentient_trader
  WORKER_HEALTH_KEY=sentient:workers:health
  INGESTION_WORKER_NAME=ingestion
  INGESTION_LIVE_ENABLED=true
  ALPACA_API_KEY=...
  ALPACA_SECRET_KEY=...
  ALPACA_TRADING_BASE_URL=https://paper-api.alpaca.markets
  TICKER_META_HASH_KEY=sentient:ticker:meta
  TICKER_DIRECTORY_STATE_KEY=sentient:ticker:directory:state
  TICKER_ALIAS_OVERRIDES_KEY=sentient:ticker:alias-overrides
  TICKER_DIRECTORY_REFRESH_SECONDS=86400
  TICKER_DIRECTORY_REFRESH_CHECK_SECONDS=60
  TICKER_PUBLISH_SCORE_THRESHOLD=80
```

Optional ticker nicknames live in Redis. For example, `HSET sentient:ticker:alias-overrides GOOG '["google"]'` lets ingestion treat "Google" as a GOOG/GOOGL alias without hardcoding that nickname in the app.

### Frontend (Netlify)

Connect the repo to Netlify. Set environment variables in the Netlify dashboard (same values as `frontend/.env.local.example`). Netlify auto-deploys on push to `main`.

### Config changes (no redeploy needed)

Update agent parameters via the Settings page in the dashboard. Then restart the agent machine to apply:

```bash
restart the agent worker in Oracle Cloud
```

---

## Key Design Decisions

**Sequential debate over parallel persona calls**

Parallel gives three independent opinions — each persona talks in a vacuum. Sequential gives a real argument: the value investor reads the momentum trader's actual take before responding, so disagreement is substantive rather than coincidental. The risk manager then stress-tests both. This is the core of why the committee produces nuanced analysis rather than averaged noise.

**Redis Streams as the message bus**

Redis Streams provide the queue semantics this pipeline needs: a persistent ordered log, consumer groups for at-least-once delivery, `XACK` for processing confirmation, and auto-ID generation. The same Valkey/Redis instance also handles headline deduplication.

**Separate backend workers**

A single process would mean a Groq rate limit pauses news ingestion. Splitting the services means ingestion always stays current regardless of LLM quota. The Redis Stream buffer absorbs any processing delay.

**Alpaca `summary` field, not full article body**

Alpaca's `summary` field is ~150–400 tokens. A full article body is ~2,000–3,000 tokens. With four sequential Groq calls per signal and Groq's 6K–12K TPM limits, passing full article bodies would exhaust per-minute quotas under any moderate news volume. The summary field adds meaningful context (earnings numbers, specific details) while staying well within budget.

**`instructor` JSON mode over TOOLS mode**

Groq's tool-calling implementation inconsistently invents tool names or returns unstructured text when `Mode.TOOLS` is requested. `Mode.JSON` with a system prompt containing the JSON schema produces consistent, validatable output. `instructor` handles Pydantic deserialization and retries automatically.

**Supabase as the only source of config defaults**

Python type annotations without assignment (`BUY_SENTIMENT_THRESHOLD: float`) create no module-level attribute. If `reload_from_supabase()` is never called or fails, any code that reads these values crashes with `AttributeError` immediately — loud and obvious. The alternative (hardcoded Python defaults) would let the process run silently on stale values after a Supabase connection failure, producing trades at wrong thresholds with no log indication of why.

**60-second per-minute rate limit cooldown**

Groq's per-minute limit is transient — it resets after 60 seconds. Rather than retrying the same rate-limited model on every call (wasteful round-trips), `ModelRouter` timestamps the cooldown expiry. Models that are cooling down are excluded from the `available` list before the first attempt. This eliminates one guaranteed failed API call per model per signal during high-volume periods.
