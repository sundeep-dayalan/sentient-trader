# Sentient Trader — Security Notes

## 1. Current Architecture

The production boundary is now:

- Browser static React app on Netlify
- FastAPI backend in Oracle Cloud
- Valkey/Redis reachable only from Oracle Cloud services
- Supabase Postgres accessed server-side with the service role key
- Supabase Auth used by the browser for user sessions
- Alpaca and Groq secrets held only by backend services

The browser sends the Supabase access token as an `Authorization: Bearer` header to FastAPI. FastAPI validates the token with Supabase Auth and performs DB, Alpaca, Redis, and admin operations server-side.

## 2. Attack Surface

### FastAPI Routes

| Route | Auth | Purpose |
|---|---|---|
| `GET /health` | None | Basic liveness check |
| `GET /auth/me` | Optional bearer token | Role flags for the dashboard |
| `GET /trades` | None | Paginated trade summaries |
| `GET /trades/{id}` | None | Trade detail plus Decision Core trace |
| `GET /stats` | None | Dashboard aggregate stats |
| `GET /portfolio` | None | Alpaca portfolio history |
| `GET /orders` | None | Alpaca account, positions, and orders |
| `POST /simulate` | Supabase user | Valkey-backed simulation rate limit and stream injection |
| `GET /agent-config` | None | Public dashboard config fields |
| `POST /agent-config` | Super user | Update agent config |
| `POST /orders/cancel` | Super user | Cancel selected Alpaca orders |
| `GET /status` | None | Supabase, Alpaca, Redis, Groq, and agent heartbeat status |

### Trust Boundaries

- Browser ↔ FastAPI
- FastAPI ↔ Supabase Auth
- FastAPI ↔ Supabase Postgres service role
- FastAPI / agent / ingestion ↔ private Valkey
- Agent / ingestion / FastAPI ↔ Alpaca APIs
- Agent / FastAPI ↔ Groq API

## 3. Findings

| ID | Severity | Category | Location | Summary |
|---|---|---|---|---|
| ST-01 | High | LLM Prompt Injection | `backend/agent/analyst.py` | External news text is fed into LLM prompts and can contain adversarial instructions |
| ST-02 | Medium | CORS / Token Exposure | `backend/api/main.py` | Keep `CORS_ORIGINS` pinned to known Netlify/local origins before production |
| ST-03 | Low | DoS Limits | `backend/api/main.py` | `/simulate` now bounds headline, summary, source, and URL lengths, but public read endpoints still rely on query limits |

## 4. Resolved By Migration

- The old frontend API-route CSRF risk is removed because the backend no longer relies on browser cookies for API authorization.
- The old OAuth callback route is removed; PKCE handling is done in the React app without a `next` redirect parameter.
- Netlify no longer needs Supabase service role, Alpaca, Groq, or Valkey credentials.
- Frontend direct DB/API secrets are removed; the browser only receives `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, `VITE_SUPABASE_DB_SCHEMA`, and `VITE_BACKEND_API_URL`.

## 5. Recommendations Before Production

- Set `CORS_ORIGINS` exactly, for example `https://your-netlify-site.netlify.app,http://localhost:3000`; avoid `*` in production.
- Put FastAPI behind HTTPS and, ideally, a lightweight WAF or reverse proxy rate limit.
- Keep Valkey private to the Oracle subnet and do not expose port `6379` publicly.
- Continue hardening prompt-injection defenses by bracketing article text as untrusted data and adding a pre-analysis sanitizer or classifier.
- Confirm `SUPER_USER_EMAILS` contains only trusted admin emails before enabling config writes or order cancellation.
