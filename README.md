# Sentient Trader

An autonomous AI trading agent that reads live financial news, reasons about market impact using a large language model, and executes paper trades — end to end, with zero human input.

```
Alpaca News API  →  Redis Stream  →  LangGraph Agent  →  Groq LLaMA  →  Alpaca Orders  →  Supabase  →  Next.js Dashboard
```

---

## What it does

Every 30 seconds the ingestion service polls Alpaca's news feed. Each headline flows through a LangGraph state machine that calls Groq (LLaMA 3.1 8B) via `instructor` for structured output — a sentiment score, confidence score, and trade action. A dual-gate risk filter rejects any trade where sentiment < 0.8 or confidence < 0.9. Trades that pass go to Alpaca's paper trading API. Every decision is logged to Supabase and surfaces in a realtime Next.js dashboard.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  sentient-trader-ingestion          [Fly.io worker]          │
│  Alpaca NewsClient → filter → RedisStreamProducer            │
└────────────────────────┬────────────────────────────────────┘
                         │  Upstash Redis Stream (XADD/XREADGROUP)
┌────────────────────────▼────────────────────────────────────┐
│  sentient-trader-agent              [Fly.io worker]          │
│                                                              │
│  START → check_cache → analyze → assess_risk → execute_trade │
│                ↓ (cached)              ↓ (threshold failed)  │
│               END                  log_result → END          │
│                                                              │
│  LangGraph 0.2  ·  Groq LLaMA 3.1 8B  ·  instructor         │
└────────────────────────┬────────────────────────────────────┘
                         │  Supabase (postgres_changes realtime)
┌────────────────────────▼────────────────────────────────────┐
│  Next.js 15 Dashboard               [localhost / Vercel]     │
│  Live feed  ·  Agent reasoning  ·  P&L chart  ·  Simulate   │
└─────────────────────────────────────────────────────────────┘
```

---

## Tech stack

| Layer | Technology |
|-------|-----------|
| AI reasoning | Groq (LLaMA 3.1 8B) + `instructor` for schema-validated output |
| Agent pipeline | LangGraph state machine with conditional routing |
| Message bus | Upstash Redis Streams (XADD / XREADGROUP / XACK) |
| Market data | Alpaca News REST API + Paper Trading API |
| Database | Supabase (Postgres + Realtime subscriptions) |
| Deduplication | SHA-256 headline hashing in Redis (5-min TTL) |
| Backend deploy | Two independent Fly.io workers (shared-cpu-1x, 256 MB) |
| Frontend | Next.js 15 App Router + Tailwind CSS + Recharts |

---

## LangGraph pipeline

```python
START
  └─▶ check_cache      # SHA-256 Redis lookup — skip duplicates instantly
        ├─▶ END          (cache hit)
        └─▶ analyze      # Groq LLaMA → TradeAnalysis(sentiment, confidence, action)
              └─▶ assess_risk   # sentiment ≥ 0.8 AND confidence ≥ 0.9
                    ├─▶ execute_trade → log_result → END   (thresholds cleared)
                    └─▶ log_result → END                   (hold)
```

Each node is a pure function. Adding a new step (e.g. earnings lookup, macro filter) requires zero changes to existing nodes — just wire a new edge.

---

## Key design decisions

**Why Redis Streams over a message queue?**
At-least-once delivery with consumer groups, persistent backlog, and zero extra infrastructure — Upstash Redis was already in the stack.

**Why `instructor` over raw JSON prompting?**
Automatic Pydantic validation with retries. The LLM output is guaranteed to match `TradeAnalysis(sentiment: float, confidence: float, action: Literal["BUY","SELL","HOLD"])` or it retries — no manual parsing.

**Why two separate Fly.io workers?**
Ingestion and reasoning have different failure modes. A Groq rate limit doesn't drop news. A noisy news day doesn't slow the LLM.

**Why dual-gate thresholds?**
Requiring BOTH strong sentiment AND high confidence keeps the false-positive rate low on noisy news days. A confident neutral signal never fires.

---

## Project structure

```
├── backend/
│   ├── ingestion/          # News poller → Redis stream
│   │   ├── listener.py     # Alpaca NewsClient, 30s poll loop
│   │   ├── filter.py       # Ticker relevance filter
│   │   ├── producer.py     # XADD to Upstash Redis
│   │   └── fly.toml
│   └── agent/              # LangGraph agent
│       ├── analyst.py      # Full graph definition + all nodes
│       ├── consumer.py     # XREADGROUP consumer loop
│       ├── trader.py       # Alpaca order execution
│       ├── cache.py        # Redis deduplication
│       ├── logger.py       # Supabase insert
│       ├── schemas.py      # Pydantic models (NewsMessage, TradeAnalysis)
│       └── fly.toml
└── frontend/               # Next.js 15 dashboard
    ├── app/
    │   ├── api/simulate/   # Injects test headlines into Redis
    │   └── api/portfolio/  # Alpaca portfolio history (server-side)
    └── components/
        ├── LiveTicker.tsx      # Supabase Realtime feed
        ├── AgentMonologue.tsx  # LLM reasoning panel
        ├── PnLChart.tsx        # Recharts equity curve
        └── SimulateButton.tsx  # One-click end-to-end test
```

---

## Running locally

```bash
# 1. Agent
cd backend/agent && pip install -r requirements.txt
cp .env.example .env   # fill in keys
python main.py

# 2. Ingestion
cd backend/ingestion && pip install -r requirements.txt
python main.py

# 3. Dashboard
cd frontend && npm install && npm run dev
```

**Required env vars** — see `.env.example` at root.

---

## Simulate a trade (no market hours needed)

The dashboard has a **Simulate** button that pushes a synthetic headline directly into the Redis stream. The agent processes it in real time — you can watch the LLM reasoning, risk decision, and Supabase log appear live without waiting for market hours.

---

## Configuration

All trading parameters are environment variables — change without redeploying:

```bash
flyctl -a sentient-trader-agent secrets set \
  GROQ_MODEL=llama-3.3-70b-versatile \
  BUY_SENTIMENT_THRESHOLD=0.75 \
  CONFIDENCE_THRESHOLD=0.85 \
  ORDER_QUANTITY=10
```
