# Launch Plan

This is the practical growth checklist for turning Sentient Trader from a cool
repo into a project people can understand, try, star, and share.

## Positioning

One-line pitch:

```text
Sentient Trader is an autonomous AI paper-trading system where four LLM agents debate real market headlines, pass through deterministic risk gates, and publish a complete replayable decision trace.
```

Short pitch:

```text
Most trading bots pattern-match headlines and fire orders. Sentient Trader runs a sequential four-agent debate, applies quality and risk gates, and stores every reasoning step so users can replay exactly why a BUY, SELL, or HOLD happened. It is paper trading only, built with LangGraph, FastAPI, Redis Streams, Supabase, Alpaca, and React.
```

## Launch Assets

- 20-40 second dashboard recording
- 3 still screenshots: dashboard, expanded decision trace, pipeline replay
- `v0.1.0` GitHub release
- README top section with live demo, safety note, and quick-start path
- One pinned GitHub Discussion announcement

## Demo Recording Shot List

1. Open the live dashboard.
2. Show the live signal feed and stats bar.
3. Expand a signal to show the four-agent debate.
4. Open a pipeline replay.
5. Show the risk gate and final decision.
6. Show the Signal Injector and mention simulated signals are blocked from
   Alpaca order submission.

Recommended export:

```bash
ffmpeg -i demo.mov -vf "fps=12,scale=1280:-1" -loop 0 .github/assets/demo.gif
```

Keep the GIF under roughly 10 MB. A hosted MP4 is better for social posts.

## GitHub Admin Checklist

- Set repository homepage to `https://apps.sundeepdayalan.in/sentient-trader`.
- Enable Discussions.
- Create Discussion categories: Announcements, Q&A, Ideas, Show and tell.
- Pin a welcome/launch discussion.
- Publish `v0.1.0`.
- Add a social preview image in repository settings.
- Review the 22 open Dependabot PRs and merge/close the stale ones.
- Create 5-10 human-readable starter issues with `good first issue` and
  `help wanted` labels.

## Starter Issues to Create

- `docs: add local setup screenshots for Supabase and Redis`
- `docs: record and add a 30 second dashboard demo GIF`
- `feat: add seeded replay fixtures for no-key demo mode`
- `test: cover Signal Injector quota messaging`
- `feat: add health badge for live demo dependencies`
- `docs: add architecture decision record for sequential debate`
- `frontend: improve empty state for first local run`
- `docs: add deployment guide for Railway or Render`

## Suggested Launch Posts

X / LinkedIn:

```text
I open-sourced Sentient Trader: an autonomous AI paper-trading system where 4 LLM agents debate real market headlines before a risk gate allows any paper trade.

It includes:
- LangGraph multi-agent debate
- Redis Streams pipeline
- Supabase decision traces
- FastAPI + React dashboard
- replayable BUY/SELL/HOLD decisions

Live demo + repo:
https://github.com/sundeep-dayalan/sentient-trader
```

Hacker News:

```text
Show HN: Sentient Trader - an auditable multi-agent AI paper-trading system

I built a paper-trading system where market headlines flow through a Redis Stream into a LangGraph committee. Four LLM personas debate sequentially, a deterministic risk gate decides whether a trade is allowed, and every step is persisted as a replayable decision trace.

The live dashboard requires no login for browsing decisions. Simulated signals are supported, and simulated signals cannot submit Alpaca orders.
```

Reddit/community angle:

```text
I built an open-source LangGraph paper-trading demo focused less on "AI makes money" and more on auditable decision-making: every headline gets a full multi-agent debate trace, deterministic risk gates, and outcome labels. Looking for feedback on the architecture and local setup.
```

## Places to Share

- Hacker News `Show HN`
- LangChain/LangGraph community
- Alpaca community
- Supabase community
- Reddit: `r/LocalLLaMA`, `r/algotrading`, `r/Python`, `r/reactjs`
- X/LinkedIn with the demo video
- Awesome lists for AI agents, LangGraph examples, and trading bots

## Launch Day Rhythm

- Morning: publish release, pin Discussion, post social thread.
- Midday: post to one technical community with the demo video.
- Afternoon: answer every comment, collect friction points as issues.
- Next day: publish a small follow-up PR that fixes the most common setup
  question. This shows the project is alive.
