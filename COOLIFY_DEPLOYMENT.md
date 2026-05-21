# Coolify Deployment

Use Dockerfiles, not Nixpacks. This repo intentionally has one Dockerfile per runtime so Coolify does not have to guess Python entrypoints or expose ports for background workers.

## Recommended Coolify Setup

Create three Coolify resources from the same GitHub repository.

### Service A: `sentient-trader-agent`

- Build pack: Dockerfile
- Base directory / build context: `backend/agent`
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

SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_DB_SCHEMA=sentient_trader

ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
GROQ_API_KEY=...
```

### Service B: `sentient-trader-ingestion`

- Build pack: Dockerfile
- Base directory / build context: `backend/ingestion`
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

ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
```

### Service C: `sentient-trader-api`

- Build pack: Dockerfile
- Base directory / build context: `backend/api`
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
