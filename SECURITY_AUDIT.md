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

| Route                 | Auth                  | Purpose                                                   |
| --------------------- | --------------------- | --------------------------------------------------------- |
| `GET /health`         | None                  | Basic liveness check                                      |
| `GET /auth/me`        | Optional bearer token | Role flags for the dashboard                              |
| `GET /trades`         | None                  | Paginated trade summaries                                 |
| `GET /trades/{id}`    | None                  | Trade detail plus Decision Core trace                     |
| `GET /stats`          | None                  | Dashboard aggregate stats                                 |
| `GET /portfolio`      | None                  | Alpaca portfolio history                                  |
| `GET /orders`         | None                  | Alpaca account, positions, and orders                     |
| `POST /simulate`      | Supabase user         | Valkey-backed simulation rate limit and stream injection  |
| `GET /agent-config`   | None                  | Public dashboard config fields                            |
| `POST /agent-config`  | Super user            | Update agent config                                       |
| `POST /orders/cancel` | Super user            | Cancel selected Alpaca orders                             |
| `GET /status`         | None                  | Supabase, Alpaca, Redis, Groq, and agent heartbeat status |

### Trust Boundaries

- Browser ↔ FastAPI
- FastAPI ↔ Supabase Auth
- FastAPI ↔ Supabase Postgres service role
- FastAPI / agent / ingestion ↔ private Valkey
- Agent / ingestion / FastAPI ↔ Alpaca APIs
- Agent / FastAPI ↔ Groq API

## 3. Findings

| ID    | Severity | Category              | Location                   | Summary                                                                                                                 |
| ----- | -------- | --------------------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| ST-01 | High     | LLM Prompt Injection  | `backend/agent/analyst.py` | External news text is fed into LLM prompts and can contain adversarial instructions                                     |
| ST-02 | Medium   | CORS / Token Exposure | `backend/api/main.py`      | Keep `CORS_ORIGINS` pinned to known Netlify/local origins before production                                             |
| ST-03 | Low      | DoS Limits            | `backend/api/main.py`      | `/simulate` now bounds headline, summary, source, and URL lengths, but public read endpoints still rely on query limits |

### Resolved hardening (2026-06-10)

| ID    | Severity | Category             | Location                     | Fix                                                                                                                                                                                  |
| ----- | -------- | -------------------- | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ST-04 | Medium   | Quota Bypass / Cost  | `backend/api/main.py`        | `/simulate` per-user "free" quota was bypassable by churning anonymous Supabase users. Added a hard per-IP hourly ceiling (`API_SIMULATE_IP_HOURLY_LIMIT`, default 10) keyed by salted client IP, independent of identity. |
| ST-05 | Low      | Info Disclosure      | `backend/api/main.py`        | Unauthenticated `/status` no longer leaks worker names, the Redis health-key name, or raw exception text — internals are logged server-side and the public payload is generic.       |
| ST-06 | Low      | Prompt-Injection     | `backend/agent/analyst.py`   | `_untrusted_news_block` now neutralizes `<<<`/`>>>` fence runs in headline/source/summary so crafted news cannot forge the closing marker and break out of the untrusted-data block. |
| ST-07 | Low      | IP / Strategy Leak   | `backend/api/main.py`, `frontend/components/SettingsPage.tsx` | `/agent-config` returns the persona system prompts only to super-users; the Settings UI hides the prompts section for non-admins. Per-trade reasoning stays public (it is the product's intended "agent monologue"). |

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
