# Monitoring — self-hosted Prometheus + Alertmanager → Telegram

Watches the four **safety invariants** that every silent mechanism failure in
the [🐞 Bug Log](../README.md#-bug-log) violated (dead reaper, naked positions,
wedged monitor, zombie orders), and pages Telegram when one breaks. Fully
self-hosted, $0, ~300MB RAM total, hard-capped so it can never crowd the apps.

```
┌─ node-3-coolify ────────────────────────────────────────────┐
│  api / agent / ingestion          monitoring (this stack)   │
│  agent → Redis worker-health      Prometheus ──► Alertmanager ──► Telegram
│  api → /metrics (reads Redis) ◄── scrape 60s (HTTPS+token)  │
└──────────────────────────────────────────────────────────────┘
```

The position monitor publishes a heartbeat + invariant gauges into the Redis
worker-health hash every sweep; the API's `/metrics` exports them generically.
Prometheus scrapes the **public HTTPS endpoint** so one probe verifies
Traefik + TLS + API + Redis + monitor, end to end.

## One-time setup (~5 minutes, then fully automatic on every commit)

### 1. Create the Telegram bot (2 min)
1. Message **@BotFather** on Telegram → `/newbot` → pick a name → copy the
   **bot token** (`1234567:AA...`).
2. Send your new bot any message (e.g. "hi"), then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy
   `message.chat.id` — that integer is your **chat id**.

### 2. Add the Coolify resource (3 min)
1. Coolify → **+ New Resource → Docker Compose Empty**.
2. Paste the **entire contents** of `monitoring/docker-compose.monitoring.yml`
   (it is fully self-contained — configs and alert rules are inlined, no Git
   checkout or bind mounts needed).
3. Add the environment variables:

   | Variable | Value |
   |---|---|
   | `SENTIENT_API_HOST` | `sentient-trader.coolify.sundeepdayalan.in` |
   | `METRICS_AUTH_TOKEN` | same value the **api** service uses |
   | `TELEGRAM_BOT_TOKEN` | from BotFather |
   | `TELEGRAM_CHAT_ID` | from getUpdates (integer) |

4. **Deploy.**

> Because "Docker Compose Empty" is detached from Git, rule changes don't
> auto-deploy: when `docker-compose.monitoring.yml` changes in the repo,
> re-paste it into the resource (30 seconds, rare). The canonical documented
> rules live in `monitoring/prometheus-alerts.yml`; the compose embeds a copy.

### 3. Verify (1 min)
```bash
# On the Coolify host — scrape target must be "up":
docker exec <prometheus-container> wget -qO- 'http://localhost:9090/api/v1/targets' | grep -o '"health":"[a-z]*"'
# Confirm env interpolation rendered the rules correctly ({{ $value }}, not {{ $$value }}):
docker exec <prometheus-container> grep -m1 'value' /etc/prometheus/rules/alerts.yml
# Force a test page to Telegram:
docker exec <alertmanager-container> amtool alert add TestAlert severity=warning --alertmanager.url=http://localhost:9093
```

## What pages you (see `prometheus-alerts.yml`)

| Alert | Meaning | Severity |
|---|---|---|
| `PositionMonitorSilent` | The safety loop (stops/exits/reaper/reconciliation) has no heartbeat — nobody is managing positions | critical |
| `NakedPositions` | A position has no stop-loss and reconciliation hasn't healed it in 15m | critical |
| `ZombieEntryOrders` | Stale unfilled entries the reaper should have cancelled | warning |
| `PositionBookRunaway` | Book size says time-based exit stopped working | warning |
| `WorkerDown` / `WorkerHeartbeatStale` | Any worker (agent/ingestion) dead or wedged | critical/warning |
| `MetricsScrapeFailing` | API up but can't read Redis | warning |

Plus: a failing scrape itself means the public API is unreachable — Prometheus's
built-in `up == 0` covers total-outage detection.

## Opening the Prometheus UI

The UI is bound to the host's loopback only (`127.0.0.1:9090`) — never exposed
to the internet, because Prometheus has no built-in auth. From your machine:

```bash
ssh -L 9090:localhost:9090 opc@<coolify-host-ip>
# then open http://localhost:9090 in your browser
```

## Design notes

- **Separate Coolify resource, not part of the app compose** — independent
  lifecycles; deploying the apps never restarts the watcher.
- **No public ports** — Prometheus/Alertmanager UIs are not exposed. Alerts are
  the product; for ad-hoc queries use `docker exec` or temporarily attach a
  Coolify proxy route.
- **Known blind spot**: if the whole node dies, the watcher dies with it. The
  10-minute fix later: move this same compose file to the near-idle
  `node-2-valkey` box (Coolify → add node-2 as a remote server) and scrape
  across the VCN. Nothing in the config changes except where it runs.
- Prometheus config lives inline in the compose file (`configs.content`,
  needs docker compose ≥ 2.23 — Coolify ships newer) so Coolify env vars
  interpolate into it; the alert **rules** stay in `prometheus-alerts.yml` so
  they version with the code.
