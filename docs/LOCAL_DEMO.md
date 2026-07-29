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

## Deterministic Replay Mode

`REPLAY_MODE=true` runs the whole agent graph on three committed fixtures with
no Alpaca account, no market-data account, and no LLM provider key. Every run
produces the same debate, the same gate numbers, and the same trace, so it also
works as a demo you can screenshot and as a smoke path in CI.

### What it still needs

Replay removes the external provider keys. It does not replace infrastructure:

- Redis or Valkey must be running.
- A Supabase project (local or hosted dev) is still required. The agent reads
  its trading parameters from `agent_config` at startup and writes every trace
  to `trades` and `trade_decision_traces`.

There is no zero-configuration mode. Replacing Supabase would be a separate
change with its own design.

### Environment

```bash
REPLAY_MODE=true
MOCK_ALPACA=true
# leave GROQ_API_KEY / OPENROUTER_API_KEY unset or at their .env.example
# placeholder values
```

Only a blank value or the exact `.env.example` placeholder counts as "no key".
A real key, even an expired one, keeps the real provider so an authentication
error surfaces as itself instead of silently turning into canned output.

### Run it

Start the agent in one terminal:

```bash
cd backend/agent
REPLAY_MODE=true MOCK_ALPACA=true python main.py
```

Seed the fixtures in another:

```bash
cd backend/agent
python inject_dummy.py --replay
```

The seeder writes three entries with `is_simulated="true"` and stamps the
publication time at injection, so the freshness gate accepts them whenever you
run it. It does not start the agent.

### What you should see

| Fixture | Committee | Gate outcome |
| --- | --- | --- |
| `GOOG expands enterprise AI partnership with major cloud customers` | bullish, BUY | passes every threshold, then blocked: "Simulated signals are never sent to Alpaca." |
| `LULU cuts guidance after weaker store traffic in North America` | bearish, SELL against a canned 4-share long | passes every threshold, then blocked the same way |
| `LEN appoints new operating chief as housing demand remains mixed` | mixed, HOLD | no order to block |

The first two are the interesting ones: they show a recommendation that clears
the sentiment, confidence, quality, and execution-plan checks and is still
stopped, because the simulated-signal block in `assess_risk` is the last gate.
Each run writes one row with `is_simulated=true`, `order_id=null`, and
`execution.submitted=false` in the decision trace.

### What replay changes, and what it does not

In replay mode the agent:

- takes news, market context, and account/position context from
  `backend/agent/replay.py` instead of Alpaca;
- takes committee output from a deterministic provider, but only while the
  configured provider has no key. With a real key the real committee runs on
  the seeded news;
- constructs no Alpaca client and reads no Alpaca credential;
- does not start the outcome labeler or the position monitor.

It does not change any order path, risk semantics, graph edge, or database
schema. A replay signal is a simulated signal, so the same block that protects
the Signal Injector protects it.

### Limits worth knowing

- Headlines are deduplicated for two hours. Re-seeding inside that window is
  skipped as a duplicate, which is correct behavior, not a replay bug. Wait it
  out or use a fresh Redis for a repeat demo.
- Determinism covers the canned model and market inputs. Values read from
  Supabase at runtime (thresholds, enabled features) and historical feedback
  data still shape the gate, so changing `agent_config` changes the result.
- A headline that is not a fixture gets no canned answer. Under replay with no
  key the committee fails closed and the signal is recorded as a HOLD. Set a
  provider key to analyze your own headlines.

### Exit

Remove `REPLAY_MODE` or set it to `false` and restart the agent. Nothing
persists the flag, and no other setting is touched.

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
