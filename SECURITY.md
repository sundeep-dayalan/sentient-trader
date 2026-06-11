# Security Policy

## Reporting a Vulnerability

If you find a security issue in Sentient Trader, please email
**sundeep.dayalan@gmail.com** with the details (affected endpoint/module,
reproduction steps, impact). Please do not open a public GitHub issue for
security reports. You should receive an acknowledgement within 72 hours.

## Scope

- `backend/api` — public FastAPI surface (auth, rate limiting, Alpaca proxy)
- `backend/agent` / `backend/ingestion` — internal workers (Redis, Supabase, LLM)
- `frontend` — static React app (Supabase Auth session handling)

This system only ever trades against Alpaca **paper** accounts
(`paper=True` is hardcoded); no real funds are at risk by design.

## Hardening Notes

Architecture, trust boundaries, and the current findings register live in
[SECURITY_AUDIT.md](SECURITY_AUDIT.md). Dependency hygiene: `pip-audit` and
`npm audit` run in CI, and Dependabot opens weekly update PRs for pip, npm,
and GitHub Actions.
