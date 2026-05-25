# 3-Day Historical Replay Test Report

Date: 2026-05-25  
Environment: production deployment, Alpaca paper trading, live ingestion paused  
Replay purpose: reliability validation of ingestion, Redis stream delivery, agent processing, Groq/LangGraph behavior, risk gate behavior, and Supabase audit persistence.

## Executive Summary

The 3-day replay passed the core reliability test.

Final verified state:

```text
Redis stream delivered:     518
Agent messages consumed:    518
Agent messages processed:   518
Agent expired messages:     0
Agent retried messages:     0
Agent dead-lettered:        0
Agent processing errors:    0

Supabase trades:            517
Supabase decision traces:   517
Supabase outbox published:  517

Full LLM debates:           199
Deterministic pre-screen:   318
Partial LLM debates:        0
Cache/duplicate no DB row:  1
```

The one-count Redis surplus (`518` Redis messages vs `517` published outbox/trade rows) is the only count mismatch. Because `trades = traces = outbox_published = 517`, the database pipeline is internally consistent. The extra Redis message was consumed and did not create retry/DLQ/errors. The most likely cause is a duplicate publish race during replay or outbox retry overlap; the agent/cache absorbed it.

No Alpaca orders were placed. This was not an Alpaca failure. The risk gate approved zero trades:

```text
should_trade_true: 0
orders_logged:     0
```

The strongest conclusion: ingestion-to-agent reliability is strong; order submission still needs a controlled test because no replay signal passed the risk gate.

Latency must be interpreted in two layers:

- Queue lifecycle during replay was long because all messages were published quickly and then drained serially.
- Actual full-debate processing was much shorter: median about `21.56s`, P99 about `70.86s`.

## Setup And Safety Controls

Live ingestion was paused before replay:

```env
INGESTION_LIVE_ENABLED=false
```

Agent age windows were widened for replay so historical articles could be analyzed instead of expired:

```env
AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS=<replay-sized window>
AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS=<replay-sized window>
```

Redis stream retention was increased from the normal live default because an earlier 10-day attempt proved `REDIS_STREAM_MAX_LEN=1000` trims replay messages too aggressively.

Recommended replay retention:

```env
REDIS_STREAM_MAX_LEN=10000   # enough for the 3-day replay observed here
```

After replay, revert production live values:

```env
INGESTION_LIVE_ENABLED=true
AGENT_MAX_TRADE_SIGNAL_AGE_SECONDS=900
AGENT_MAX_AUDIT_SIGNAL_AGE_SECONDS=86400
REDIS_STREAM_MAX_LEN=1000
```

## Preflight Observations

Before running replay, Redis was clean:

```text
XLEN market-news:                 0
ZCARD market-news:agent-retry:    0
XLEN market-news:agent-dlq:       0
```

Agent health before replay:

```json
{
  "worker": "agent",
  "status": "healthy",
  "phase": "polling",
  "messages_consumed": 0,
  "messages_processed": 0,
  "messages_expired": 0,
  "messages_retried": 0,
  "messages_dead_lettered": 0,
  "processing_errors": 0
}
```

Ingestion health before replay:

```json
{
  "worker": "ingestion",
  "status": "healthy",
  "phase": "live_paused",
  "detail": "Live Alpaca ingestion disabled by INGESTION_LIVE_ENABLED=false",
  "stream_connected": false,
  "articles_seen": 0,
  "articles_published": 0,
  "pending_outbox_count": 0,
  "ticker_directory_assets": 13729
}
```

Dry-run checks:

```text
python replay_historical.py --hours 1 --dry-run
Fetched 0 Alpaca articles; DB/Redis untouched.

python replay_historical.py --days 1 --dry-run
Fetched 41 Alpaca articles; DB/Redis untouched.
```

## Replay Command

The 3-day replay was run through the normal ingestion path:

```bash
python replay_historical.py --days 3 --max-pages 300 --confirm-replay
```

The wrapper uses `NewsListener._handle_article()`, so replay exercises the same normalization, ticker selection, durable store, dedupe, outbox, and Redis publish path as live ingestion. It intentionally does not test the Alpaca websocket subscription itself.

## Replay Ingestion Result

Replay completion output:

```text
Replay complete
  fetched_from_alpaca: 676
  raw_news_articles: before=0 after=627 delta=627
  news_outbox: before=0 after=517 delta=517
  ingestion_events: before=0 after=1683 delta=1683
  redis_stream: before=0 after=518 delta=518
```

Interpretation:

- Alpaca returned `676` raw news items for the 3-day window.
- Ingestion stored `627` unique raw articles.
- Dedupe/filtering/ticker relevance produced `517` outbox rows.
- Ingestion events recorded `1683` audit events.
- Redis stream ended with `518` entries, one more than published outbox rows.

Raw article dedupe observed during replay:

```text
not_duplicate                 4808
same_url                       202
same_headline_ticker_window      4
```

Earlier 10-day background finding:

```text
fetched_from_alpaca: 5215
raw_news_articles:  5014
news_outbox:        4323
ingestion_events:   13875
redis_stream:       ~1006
```

The 10-day attempt was invalid as an end-to-end replay because `REDIS_STREAM_MAX_LEN=1000` trimmed older stream entries while the agent was still processing. This led to the replay runbook and env guidance being updated.

## Redis And Agent Drain

During replay, Redis lag decreased steadily while retry and DLQ remained empty.

Mid-run sample:

```text
XINFO GROUPS market-news
pending:      4
entries-read: 149
lag:          369

Agent health:
messages_consumed:  141
messages_processed: 140
processing_errors:  0
```

Later:

```text
entries-read: 289
lag:          229
pending:      4
retry:        0
DLQ:          0
```

Final Redis state:

```text
XINFO GROUPS market-news
consumers:         1
pending:           0
last-delivered-id: 1779672024009-0
entries-read:      518
lag:               0

ZCARD market-news:agent-retry
0

XLEN market-news:agent-dlq
0
```

Final agent health:

```json
{
  "worker": "agent",
  "status": "healthy",
  "phase": "polling",
  "detail": "Redis stream consumer is polling for news",
  "messages_consumed": 518,
  "messages_processed": 518,
  "messages_expired": 0,
  "messages_retried": 0,
  "messages_dead_lettered": 0,
  "processing_errors": 0,
  "current_entry_id": "1779672024009-0",
  "current_ticker": "XPEV",
  "current_source": "stream",
  "last_heartbeat_at": "2026-05-25T03:17:53Z"
}
```

Conclusion: Redis stream delivery, consumer ACKing, retry handling, and DLQ behavior passed.

## Consumer Suspect Investigated

At one point Redis showed two consumers:

```text
agent-1ef4f6351dfb    pending=5    idle about 89s
agent-86d7428ede9e    pending=0    idle about 43m
```

The second consumer was stale and had no pending messages. It was not doubling Groq traffic. It was safe to remove with:

```redis
XGROUP DELCONSUMER market-news sentient-agent-group agent-86d7428ede9e
```

After cleanup, the group had one consumer and continued processing normally.

## Database Final State

Final consistency check:

```sql
select
  'trades_total' as metric, count(*)::bigint as value
from sentient_trader.trades
union all
select
  'traces_total', count(*)::bigint
from sentient_trader.trade_decision_traces
union all
select
  'outbox_published', count(*)::bigint
from sentient_trader.news_outbox
where status = 'PUBLISHED';
```

Result:

```text
trades_total:      517
traces_total:      517
outbox_published: 517
```

Interpretation:

- Every logged trade row has a decision trace.
- Every published outbox row has a corresponding trade/trace result.
- No trace persistence gap was observed.

## Alpaca Order Findings

No Alpaca orders were placed.

This is expected because the risk gate approved zero trades:

```text
should_trade_true:      0
orders_logged:          0
directional_decisions:  observed rising during run; final directional audit found 18 candidates
```

Important distinction:

- `trades.trade_action` stores the Portfolio Manager's decision: `BUY`, `SELL`, or `HOLD`.
- Alpaca order submission only happens when `risk_gate.should_trade = true`.
- A `BUY` row in `trades` does not guarantee Alpaca was called.

The graph route is:

```text
portfolio_manager_decision
  -> assess_risk
  -> execute_trade only if should_trade=true
  -> log_result
```

Final directional candidate audit:

```text
directional_candidates: 18
sentiment_passed:       4
confidence_passed:      0
quality_passed:         18
execution_plan_passed:  11
fully_passed:           0
```

Because `fully_passed = 0`, no Alpaca orders were expected.

## Threshold Sensitivity

What-if replay gate sensitivity:

```text
buy >= 0.80, sell <= -0.80, confidence >= 0.80  -> 0 orders
buy >= 0.80, sell <= -0.80, confidence >= 0.75  -> 1 order
buy >= 0.75, sell <= -0.75, confidence >= 0.75  -> 1 order
buy >= 0.70, sell <= -0.70, confidence >= 0.70  -> 2 orders
buy >= 0.65, sell <= -0.65, confidence >= 0.65  -> 9 orders
```

Interpretation:

- Current thresholds are conservative.
- They are not suppressing dozens of obvious trades.
- Lowering confidence from `0.80` to `0.75` would have produced only one order.
- Lowering to `0.65` would produce many more trades and likely overtrade weaker/softer catalysts.

Recommendation: do not lower production thresholds yet. First collect more replay windows and surface calibrated confidence in the UI.

## Debate Health Findings

The debate looked generally healthy. Most directional candidates had a consistent pattern:

```text
Momentum Trader: directional, often high conviction
Value Investor: cautious, often asks for fundamentals/valuation details
Risk Manager: neutral or cautious, highlights execution/regulatory/macro risk
Portfolio Manager: directional candidate with moderate confidence
Risk Gate: blocks because calibrated confidence is below threshold
```

This is desirable behavior for ambiguous headlines, analyst upgrades/downgrades, already-extended movers, and articles missing financial details.

Examples:

### RKLB

Headline: "Rocket Lab Lands $90 Million US Space Force Deal"

```text
Action:       BUY
Sentiment:    0.85
Raw conf:     0.75
Gate result:  blocked
Reason:       calibrated confidence below 0.80
```

Committee:

- Momentum Trader: BULLISH, conviction `0.90`
- Value Investor: BULLISH, conviction `0.60`
- Risk Manager: NEUTRAL, conviction `0.65`

Gate rationale:

- Strong catalyst, but partial committee agreement.
- Already extended intraday.
- Structural risks around margins/execution.

This was the most reasonable near-miss. At a `0.75` confidence gate it likely would have ordered. Under the current conservative gate, blocking was rational.

### MTVA

Headline theme: peer-reviewed MASH therapy data, stock already up sharply.

Slim trade row looked order-worthy:

```text
Action:          BUY
Sentiment:       0.80
Raw confidence:  0.90
```

Risk gate revealed the true execution score:

```text
calibrated_confidence: 0.70
required:              0.80
execution_plan_ok:     true
estimated_notional:    3.85
```

Calibration cap reasons:

```text
medium article quality
split committee
already extended intraday
```

Committee metrics:

```text
agreement:        0.564
bullish_weight:   0.85
neutral_weight:   1.10
confidence_cap:   0.70
thesis_quality:   WATCH
```

Interpretation:

MTVA exposed an observability mismatch. The slim `trades.confidence_score` stores raw Portfolio Manager confidence, while execution uses calibrated confidence. The risk gate did not ignore a high-confidence signal; it downgraded execution confidence because the committee was split and the stock was already extended.

Recommendation: surface `calibrated_confidence` in the UI and/or store it on `trades` so operators do not misread raw confidence as execution confidence.

### SELL Candidates

SELL candidates such as FUTU/YMT/GMM/DAVA/PS were mostly blocked for one or both reasons:

- Calibrated confidence below threshold.
- No long position to reduce; short sells are disabled.

This is correct for a fresh paper account with no holdings. To test the SELL order path, seed a long position first or explicitly enable a controlled short-selling test policy.

## LLM Debate Volume

Question answered: out of the `518` Redis messages processed, how many actually entered the full LLM debate?

The audit counted `decision_trace.llm_operations`, not `committee_debate`. This matters because deterministic pre-screen HOLDs still create committee cards for UI/audit purposes, but they do not call Groq.

Result:

```text
Redis processed:          518
DB logged signals:        517
Full LLM debates:         199
Partial LLM debates:      0
Deterministic pre-screen: 318
Cache/duplicate skip:     1
```

Percentages:

```text
199 / 518 = 38.4% full LLM debate
318 / 518 = 61.4% deterministic pre-screen HOLD
1 / 518   = 0.2% cache/duplicate/no DB row
```

Interpretation:

- Only `199` signals truly paid for the 4-step Groq debate.
- All `199` completed the full debate path.
- `partial_llm_debate = 0`, which means no signal got stuck after only momentum/value/risk or before synthesis.
- The `318` deterministic pre-screen rows were low-quality or low-catalyst HOLDs handled without LLM spend.
- The one missing DB row aligns with the one extra Redis stream entry over outbox/trade counts and was most likely duplicate/cache absorbed.

Full debate stages verified:

```text
momentum_analyst
value_analyst
risk_analyst
portfolio_manager_synthesis
```

Portfolio Manager synthesis model distribution for the `199` full debates:

```text
qwen/qwen3-32b                           75
meta-llama/llama-4-scout-17b-16e         73
openai/gpt-oss-20b                       23
llama-3.3-70b-versatile                  17
openai/gpt-oss-120b                      11
```

Interpretation:

- The model cascade worked under Groq rate limits.
- Higher-preference models were throttled during replay, so later fallback tiers carried many synthesis calls.
- The fallback behavior did not create partial debates, retries, DLQ entries, or processing errors.

## Groq And LangGraph Findings

LangSmith metrics observed:

```text
Run count:      787
Total tokens:   1,668,256
Median tokens:  1,694
Error rate:     0%
Streaming:      0%
Latency P50:    0.10s
Latency P99:    78.09s
```

Interpretation:

- P50 is excellent because most signals are cheap HOLD/pre-screen/cache paths.
- P99 is high because replay burst traffic hit Groq rate limits and retry-after cooldowns.
- The tail latency did not cause agent failures, retries, DLQ, or lost messages.

Agent log sample findings during replay:

```text
Groq SDK retries:              129
Model rate-limit cooldowns:    13
Structured output failures:    4
Agent ERROR/DLQ/all-tiers-fail: none observed
```

Model usage in sampled logs:

```text
67  llama-3.3-70b-versatile
38  qwen/qwen3-32b
26  openai/gpt-oss-120b
15  openai/gpt-oss-20b
5   meta-llama/llama-4-scout-17b-16e-instruct
```

Suspects:

- `openai/gpt-oss-120b` hit repeated retry-after cooldowns.
- `llama-3.3-70b-versatile` had one very large cooldown.
- `openai/gpt-oss-20b` had structured-output failures and is less reliable for strict JSON output.

Conclusion: Groq was the throughput bottleneck during replay, but not a correctness problem. The model cascade worked.

## Latency Findings

Two latency measurements were collected. They answer different questions.

### Queue Lifecycle Latency

Definition:

```text
news_outbox.published_at -> trade_decision_traces.created_at
```

This measures end-to-end lifecycle after Redis publish, including time spent waiting behind earlier replay messages. Because replay published all messages quickly and the agent processed them one-by-one under Groq throttling, this metric is dominated by queue wait.

Overall queue lifecycle:

```text
signals:      517
avg:          3997.12s  (66.62m)
p50:          4698.43s  (78.31m)
p75:          6357.39s  (105.96m)
p99:          6885.02s  (114.75m)
max:          6928.99s  (115.48m)
```

By path:

```text
deterministic_prescreen
signals: 318
avg:     3885.22s
p50:     4528.65s
p75:     6335.77s
p99:     6845.86s
max:     6928.94s

full_llm_debate
signals: 199
avg:     4175.88s
p50:     4868.00s
p75:     6404.05s
p99:     6907.96s
max:     6928.84s
```

Interpretation:

- These numbers are not per-signal active processing time.
- Pre-screen and full-debate rows have similar lifecycle latency, proving queue position dominates this measurement.
- The high p50/p99 came from replay dumping hundreds of messages at once while Groq enforced retry-after backpressure.

### Full LLM Debate Processing Latency

Approximate definition:

```text
first LLM operation recorded_at -> trace logged at
```

This is the best available persisted estimate of active LLM debate lifecycle for full-debate signals. It still misses time before the first LLM operation completed, because the system did not yet persist `processing_started_at`.

Result for the `199` full debates:

```text
full_llm_debates: 199
avg:              27.86s
p50:              21.56s
p75:              49.84s
p99:              70.86s
max:              86.82s
```

Interpretation:

- Median full debate completed in about `22s`.
- 75% completed within about `50s`.
- P99 landed around `71s`, matching LangSmith's high tail latency and Groq retry behavior.
- For live production, where messages arrive gradually, expected latency should be closer to the active full-debate numbers than the replay queue lifecycle numbers.

Observability gap:

For exact active processing latency, add timestamps to the decision trace:

```text
processing_started_at
processing_finished_at
queue_received_at / redis_entry_id timestamp, if useful
```

This would let future reports separate queue wait, active agent processing, LLM wait, and DB logging precisely.

## Suspects And Resolutions

### Suspect: Redis Stream Trimmed Messages

Status: confirmed in the earlier 10-day replay, resolved by increasing `REDIS_STREAM_MAX_LEN` for replay.

Evidence:

- 10-day outbox published over `4300` messages.
- Redis stream retained only about `1000`.
- Agent could not process messages that Redis had trimmed.

Action:

- Added replay guidance to use larger stream max length.
- 3-day replay retained all messages and drained cleanly.

### Suspect: Two Agents Consuming And Doubling Groq Load

Status: false.

Evidence:

- Second consumer was stale, idle, and had `pending=0`.
- Active consumer was the only one processing.

Action:

- Removed stale consumer.

### Suspect: Groq Broken Or Causing Failures

Status: false.

Evidence:

- Groq throttled heavily during replay.
- Agent had `0` processing errors, `0` retries, `0` DLQ.
- LangSmith error rate was `0%`.

Action:

- Treat Groq as replay throughput bottleneck.
- Consider chunked replay or replay rate limiting if faster drains are needed.

### Suspect: Alpaca Order Path Broken

Status: not proven; not exercised.

Evidence:

- `should_trade_true = 0`.
- `orders_logged = 0`.
- Therefore Alpaca order submission was never expected to happen.

Action:

- Run a controlled order-path test separately.

### Suspect: Gate Too Strict

Status: conservative but defensible.

Evidence:

- At current threshold, `0` orders.
- At `0.75` confidence, only `1` would order.
- At `0.70`, only `2` would order.
- At `0.65`, `9` would order, which may be too loose.

Action:

- Do not loosen production thresholds yet.
- Collect more replay windows.
- Add calibrated confidence visibility.

### Suspect: Debate Is Ignoring Strong Signals

Status: no strong evidence.

Evidence:

- Strongest raw signal (MTVA raw confidence `0.90`) was downgraded by calibration due split committee, medium quality, and already extended price action.
- RKLB was a legitimate near-miss but still had risk/value caution.
- SELLs were correctly blocked because no long position existed.

Action:

- Review near-miss candidates manually after each replay.
- Track calibrated confidence separately from raw confidence.

## What This Test Proved

Proved:

- Replay wrapper exercises the normal ingestion path.
- Alpaca REST news fetch worked.
- Ticker selection/outbox generation worked.
- Redis stream delivery worked.
- Agent consumer group drained all messages.
- No retry backlog.
- No DLQ.
- No processing errors.
- Groq fallback survived throttling.
- `199` signals entered and completed the full 4-step LLM debate.
- `318` signals were handled by deterministic pre-screen without LLM spend.
- No partial LLM debates were observed.
- Supabase trade logging worked.
- Supabase decision trace logging worked.
- Cache/idempotency avoided visible duplicate trade rows from the one extra Redis message.
- Risk gate correctly blocked all non-executable signals under current policy.

Not proved:

- Live Alpaca websocket reconnect behavior.
- Alpaca order submission success path.
- Alpaca order rejection handling in this replay window.
- SELL execution path, because no long positions existed.
- ROI or financial performance.
- Exact active processing latency from message receipt to finish, because `processing_started_at` is not yet persisted.

## Recommended Next Tests

1. Controlled BUY order-path test

Temporarily lower thresholds in a non-public paper account or inject one crafted signal that should pass. Verify:

```text
risk_gate.should_trade = true
execution.submitted = true
trades.order_id is not null
Alpaca dashboard shows order
```

Then restore strict thresholds.

2. Controlled SELL path test

Create or seed a small long position first. Then test a SELL signal for that ticker. Verify position-aware sell sizing and Alpaca submission.

3. Add calibrated confidence to observability

Recommended fields:

```text
raw_confidence
calibrated_confidence
confidence_cap
cap_reasons
should_trade
gate_reason
```

This prevents raw Portfolio Manager confidence from being mistaken for execution confidence.

4. Replay another 3-day or 5-day window

Confirm whether the "no orders" behavior is consistent or whether this replay window was unusually weak.

5. Optional replay rate limiter

If replay speed matters, add a delay/batch size control so Groq P99 does not stretch into retry-after tails.

6. Persist active processing timestamps

Add `processing_started_at` and `processing_finished_at` to `decision_trace` so future audits can calculate exact active agent processing latency rather than inferring from LLM operation timestamps.

## Final Verdict

The 3-day replay was a strong reliability pass.

The system did not place Alpaca orders because no signal passed the calibrated risk gate. Based on the detailed candidate audit, this looks like conservative behavior rather than a broken order path. The debate is mostly healthy: it produces directional candidates, includes dissent, lowers confidence on split committees, and avoids chasing already-extended movers.

The biggest improvement is observability: store or display calibrated confidence so operators can see why a raw high-confidence `BUY` did not execute.
