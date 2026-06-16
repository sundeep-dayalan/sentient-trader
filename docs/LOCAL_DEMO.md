# Local Demo Guide

This guide is the shortest path for someone who wants to see the full pipeline
move without waiting for a live market headline.

## What Works Today

Sentient Trader already supports safe simulated signals:

- The dashboard has a Signal Injector.
- The API exposes `POST /simulate`.
- The agent marks simulated messages with `is_simulated=true`.
- Simulated signals are blocked from Alpaca order submission in the risk gate.

That makes the simulation path the recommended local demo path.

## Minimal Local Flow

1. Start Redis or Valkey.
2. Run `supabase/schema.sql` in a Supabase project.
3. Start the agent, ingestion worker, API, and frontend as shown in the README.
4. Open the frontend.
5. Use Signal Injector to submit a ticker and headline.
6. Open the new signal and inspect the four-agent debate and risk gate.

## CLI Injection Alternative

If you only want to push a message into Redis while the agent is running:

```bash
cd backend/agent
python inject_dummy.py
```

The script writes a simulated message into the configured Redis Stream. It does
not bypass the normal agent pipeline.

## Recommended Demo Headlines

Use headlines that are realistic enough for the source-quality and relevance
checks:

```text
GOOG expands enterprise AI partnership with major cloud customers
```

```text
LULU cuts quarterly guidance after weaker store traffic
```

```text
LEN appoints new operating chief as housing demand remains mixed
```

## Next Improvement

The highest-impact onboarding improvement is a no-key replay fixture mode:

- Seed a small set of normalized news messages.
- Use deterministic mock market context.
- Run the agent with `MOCK_ALPACA=true`.
- Persist traces into a local or hosted Supabase dev schema.
- Let contributors experience the debate, risk gate, and dashboard without
  Alpaca or live market dependencies.

Track this as a `good first issue` or `help wanted` task after the initial
public release.
