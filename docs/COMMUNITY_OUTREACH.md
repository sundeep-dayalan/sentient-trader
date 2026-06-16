# Community Outreach Playbook

Use this to launch Sentient Trader without looking like drive-by promotion.
The goal is feedback, contributors, and technical discussion; stars follow if
the project is useful.

## Ground Rules

- Do not ask for stars, upvotes, or comments.
- Disclose that you built/maintain the project.
- Tailor each post to the community.
- Prefer live demo + GitHub repo over a marketing page.
- Be around for the first 24-48 hours to answer questions.
- If a community says "no promotion", skip it or ask moderators first.
- For Reddit communities that restrict AI-generated posts, rewrite drafts in
  your own voice before posting and disclose if AI helped polish wording.

## Priority Order

1. Hacker News Show HN
2. LangChain/LangGraph community forum or Slack
3. Alpaca forum or Slack
4. Supabase Discord
5. Reddit selectively, not everywhere

## Hacker News

Status: good fit.

Why: Show HN is explicitly for something you made that others can try. Use the
GitHub repo or live dashboard as the URL. Do not post a generated comment; if
you want a first comment, write it yourself in your own voice.

Title:

```text
Show HN: Sentient Trader - auditable multi-agent AI paper trading
```

URL:

```text
https://github.com/sundeep-dayalan/sentient-trader
```

Optional first comment, rewrite before posting:

```text
I built Sentient Trader as an experiment in auditable AI decision-making rather than "AI predicts the market".

The system ingests market headlines, runs a sequential four-agent LangGraph debate, applies deterministic quality/risk gates, and stores every decision as a replayable trace. It is paper trading only; simulated signals are explicitly blocked from Alpaca order submission.

I would love feedback on the architecture, especially the event pipeline, risk-gate design, and how to make the local demo easier to run.
```

## Reddit

### r/algotrading

Status: do not post directly.

Reason: the subreddit rules prohibit marketing/self-promotion and posts that
mainly bring attention to a personal site/repo. If you want to engage there,
ask moderators first or participate in existing technical threads with useful
answers. Do not drop the repo as a standalone post.

Moderator message draft:

```text
Hi mods, I built an open-source paper-trading system using Alpaca, Redis Streams, Supabase, FastAPI, React, and a LangGraph multi-agent debate pipeline.

I saw the no-promotion rule and do not want to violate it. Would a technical architecture write-up focused on risk gates, event delivery, and paper-trading safety be appropriate here if it includes the GitHub link, or should I avoid posting it?
```

### r/LocalLLaMA

Status: possible, but rewrite in your own voice and disclose affiliation.

Angle: focus on multi-agent debate, local/OSS LLM provider routing, structured
outputs, and audit traces. Do not make it mostly about trading performance.

Title:

```text
I built an open-source multi-agent LLM paper-trading demo with replayable decision traces
```

Body draft:

```text
I built and open-sourced Sentient Trader, a paper-trading system that uses a sequential four-agent LLM debate rather than a single model call.

The interesting part for this community is the agent architecture:
- LangGraph StateGraph pipeline
- four personas that see and respond to previous reasoning
- structured Pydantic outputs
- provider routing across Groq/OpenRouter
- deterministic pre-screening before spending LLM calls
- persisted JSONB decision traces so every BUY/SELL/HOLD can be replayed

It is not meant as financial advice and it does not claim profit. Simulated signals are blocked from Alpaca order submission.

Repo: https://github.com/sundeep-dayalan/sentient-trader
Live demo: https://apps.sundeepdayalan.in/sentient-trader

I am looking for feedback on the agent design: would you keep the debate sequential, make agents independent and aggregate, or use a judge/critic loop?
```

### r/Python

Status: do not post as a normal main-feed showcase.

Reason: the rules say AI showcase posts should go to the appropriate monthly
showcase post or daily thread. If posting, use that thread and include the
required project sections.

Thread comment draft:

```text
What My Project Does

Sentient Trader is an open-source paper-trading system where Python services ingest market headlines, push them through Redis Streams, run a LangGraph four-agent debate, apply deterministic risk gates, and persist every decision trace to Supabase.

Target Audience

Developers interested in event-driven Python services, LangGraph agents, FastAPI, Redis Streams, and auditable automation. It is educational/paper trading only, not financial advice.

Comparison

Most trading-bot examples focus on indicators or keyword triggers. This project focuses on the audit trail: every LLM output, risk-gate decision, and pipeline step is replayable.

Repo: https://github.com/sundeep-dayalan/sentient-trader
```

### r/reactjs

Status: low priority.

Reason: the project is not primarily a React library. If you post there, make it
about the dashboard implementation only: React/Vite/Tailwind, React Flow
pipeline visualization, Recharts, auth/error states. Avoid generic project promo.

Title:

```text
Built a React dashboard for replaying multi-agent AI decision pipelines
```

Body draft:

```text
I built a React/Vite dashboard for an open-source paper-trading demo. The React-specific pieces are a live signal feed, Recharts PnL panels, Supabase-authenticated API calls, and a React Flow view that replays each AI decision pipeline from news -> pre-screen -> agent debate -> risk gate -> final decision.

Repo: https://github.com/sundeep-dayalan/sentient-trader
Live dashboard: https://apps.sundeepdayalan.in/sentient-trader

I would value frontend feedback on the pipeline replay UX and first-run empty states.
```

## LangChain / LangGraph Community

Status: good fit.

Where: LangChain forum or Slack. Use a showcase/projects-style channel if one
exists.

Title:

```text
Sentient Trader: LangGraph multi-agent paper-trading demo with replayable traces
```

Body:

```text
I built Sentient Trader, an open-source LangGraph project focused on auditable multi-agent decision-making.

It ingests market headlines, runs a sequential four-agent debate, applies deterministic quality/risk gates, and persists every step as a replayable decision trace. The trading side is paper-only; the design goal is transparency and reliability, not financial advice.

LangGraph pieces:
- StateGraph pipeline with pre-screen, market context, four persona calls, risk gate, and execution/audit nodes
- sequential debate where later personas see earlier reasoning
- structured Pydantic outputs
- provider-aware routing across Groq/OpenRouter
- persisted traces shown in a React pipeline replay UI

Repo: https://github.com/sundeep-dayalan/sentient-trader
Live demo: https://apps.sundeepdayalan.in/sentient-trader

I would love feedback from LangGraph builders: would you model this as a sequential debate, a parallel committee with synthesis, or a critic loop?
```

## Alpaca Community

Status: good fit if posted as a paper-trading/API integration example.

Where: Alpaca forum or Slack.

Title:

```text
Open-source Alpaca paper-trading demo with AI debate + risk gates
```

Body:

```text
I built Sentient Trader, an open-source paper-trading system using Alpaca market/news/order APIs.

The goal is not to claim performance. The focus is an auditable pipeline:
- Alpaca news + paper trading APIs
- Redis/Valkey Streams for at-least-once signal delivery
- four-agent LLM debate over each headline
- deterministic risk gates before any paper order
- simulated signals are blocked from Alpaca order submission
- full decision traces and pipeline replays in a React dashboard

Repo: https://github.com/sundeep-dayalan/sentient-trader
Live demo: https://apps.sundeepdayalan.in/sentient-trader

I would appreciate feedback from Alpaca builders on the order-safety model, paper-trading assumptions, and execution observability.
```

## Supabase Community

Status: good fit if framed around Postgres/RLS/JSONB/realtime observability.

Where: Supabase Discord. Look for showcase, built-with-supabase, or community
project channels.

Body:

```text
I built Sentient Trader, an open-source paper-trading demo that uses Supabase as the audit/config backbone.

Supabase pieces:
- Postgres schema for trades, raw news, outbox, and decision traces
- JSONB decision_trace storage for every multi-agent reasoning step
- RLS with service-role backend access instead of browser direct table access
- dashboard stats/calibration queries
- agent_config in Postgres so thresholds can change without redeploying workers

Repo: https://github.com/sundeep-dayalan/sentient-trader
Live demo: https://apps.sundeepdayalan.in/sentient-trader

I would love feedback on the schema/RLS boundary and JSONB trace design.
```
