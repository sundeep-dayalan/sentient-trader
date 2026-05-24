# Coolify Deployment

Use Dockerfiles, not Nixpacks. This repo intentionally has one Dockerfile per runtime so Coolify does not have to guess Python entrypoints or expose ports for background workers.

## Recommended Coolify Setup

Create three Coolify resources from the same GitHub repository.

### Service A: `sentient-trader-agent`

- Build pack: Dockerfile
- Base directory / build context: `backend`
- Dockerfile: `backend/agent/Dockerfile`
- Domain: none
- Exposed port: none
- Public access: off
- Command: Dockerfile default, `python main.py`

Environment:

```env
REDIS_HOST=<valkey-private-host>
REDIS_PORT=6379
REDIS_DB=0
REDIS_STREAM_KEY=market-news
REDIS_USERNAME=
REDIS_PASSWORD=
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

SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_DB_SCHEMA=sentient_trader

ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
GROQ_API_KEY=...
```

### Service B: `sentient-trader-ingestion`

- Build pack: Dockerfile
- Base directory / build context: `backend`
- Dockerfile: `backend/ingestion/Dockerfile`
- Domain: none
- Exposed port: none
- Public access: off
- Command: Dockerfile default, `python main.py`

Environment:

```env
REDIS_HOST=<valkey-private-host>
REDIS_PORT=6379
REDIS_DB=0
REDIS_STREAM_KEY=market-news
REDIS_USERNAME=
REDIS_PASSWORD=
WORKER_HEALTH_KEY=sentient:workers:health
INGESTION_WORKER_NAME=ingestion

SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_DB_SCHEMA=sentient_trader

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

Optional ticker nicknames can live in Redis, not code. Example: `HSET sentient:ticker:alias-overrides GOOG '["google"]'` and restart ingestion, or wait for the next ticker-directory refresh.

### Service C: `sentient-trader-api`

- Build pack: Dockerfile
- Base directory / build context: `backend`
- Dockerfile: `backend/api/Dockerfile`
- Domain: `https://sentient-trader.coolify.sundeepdayalan.in`
- Exposed/container port: `8000`
- Public access: on
- Command: Dockerfile default, `uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}`

Environment:

```env
PORT=8000
CORS_ORIGINS=https://apps.sundeepdayalan.in

REDIS_HOST=<valkey-private-host>
REDIS_PORT=6379
REDIS_DB=0
REDIS_STREAM_KEY=market-news
REDIS_USERNAME=
REDIS_PASSWORD=
WORKER_HEALTH_KEY=sentient:workers:health
AGENT_WORKER_NAME=agent

SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_DB_SCHEMA=sentient_trader
SUPER_USER_EMAILS=you@example.com

ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets
GROQ_API_KEY=...
```

## Docker Compose Alternative

If you deploy as one Coolify Docker Compose resource, use `docker-compose.coolify.yml`. It runs the same three services. Only `api` declares `expose: 8000`; it does not bind host port `8000`, so Coolify/Traefik can route the public domain without host port conflicts.

Attach a domain only to the `api` service. Do not attach domains to `agent` or `ingestion`.

## Redis Host Note

Inside Docker, `REDIS_HOST=127.0.0.1` means "inside this container." Use that only if Valkey is running in the same network namespace. In most Coolify deployments, use the private Valkey hostname/IP or the Compose service name, for example:

```env
REDIS_HOST=valkey
```

or:

```env
REDIS_HOST=<private-oracle-ip>
```
