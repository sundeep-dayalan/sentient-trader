# Release Checklist

Use this when cutting public GitHub releases. The first recommended release is
`v0.1.0`.

## Preflight

- Confirm CI is green on `main`.
- Review open Dependabot PRs and close/merge anything stale.
- Confirm the live demo opens and at least one decision replay loads.
- Confirm the README quick links work.
- Confirm `.env.example` does not contain real secrets.
- Confirm the license, contribution guide, security policy, and issue templates
  are present.

## Suggested `v0.1.0` Release Notes

Title:

```text
Sentient Trader v0.1.0 - public paper-trading demo
```

Body:

```markdown
Sentient Trader is an autonomous AI paper-trading system where a sequential
four-agent LLM committee debates market headlines, applies deterministic risk
gates, and persists a complete decision trace for every signal.

Highlights:
- Live no-login dashboard with signal feed, PnL, system status, and pipeline replay
- Four-agent LangGraph debate with structured Pydantic outputs
- Redis/Valkey Streams pipeline with at-least-once delivery and pending rescue
- Supabase Postgres audit trail with JSONB decision traces
- FastAPI backend with auth forwarding, rate limits, and paper-account APIs
- Simulated signal injector for safe end-to-end demos
- CI for backend tests, API tests, frontend typecheck, and dependency audit

Safety:
- Paper trading only
- Simulated signals cannot submit Alpaca orders
- No financial advice

Try it:
- Live dashboard: https://apps.sundeepdayalan.in/sentient-trader
- Setup: see README.md
- Security policy: SECURITY.md
```

## Publish

1. Create tag `v0.1.0` from `main`.
2. Generate release notes using GitHub's release UI.
3. Paste the suggested release body above and adjust for current changes.
4. Attach a short demo GIF or MP4 if available.
5. Publish the release, then share the release URL in the launch channels from
   `docs/LAUNCH_PLAN.md`.

## After Publishing

- Pin a GitHub Discussion announcement for the release.
- Update the repository homepage to the live demo URL.
- Add the release link to social posts and community submissions.
- Watch issues, discussions, and traffic sources for the first 48 hours.
