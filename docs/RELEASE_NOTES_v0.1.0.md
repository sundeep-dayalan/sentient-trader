# Sentient Trader v0.1.0 - public paper-trading demo

Sentient Trader is an autonomous AI paper-trading system where a sequential
four-agent LLM committee debates market headlines, applies deterministic risk
gates, and persists a complete decision trace for every signal.

## Highlights

- Live no-login dashboard with signal feed, PnL, system status, and pipeline
  replay
- Four-agent LangGraph debate with structured Pydantic outputs
- Redis/Valkey Streams pipeline with at-least-once delivery and pending rescue
- Supabase Postgres audit trail with JSONB decision traces
- FastAPI backend with auth forwarding, rate limits, and paper-account APIs
- Simulated signal injector for safe end-to-end demos
- CI for backend tests, API tests, frontend typecheck, and dependency audit

## Safety

- Paper trading only
- Simulated signals cannot submit Alpaca orders
- Educational project, not financial advice

## Try It

- Live dashboard: https://apps.sundeepdayalan.in/sentient-trader
- Setup: https://github.com/sundeep-dayalan/sentient-trader#quick-start
- Local demo guide: docs/LOCAL_DEMO.md
- Security policy: SECURITY.md
