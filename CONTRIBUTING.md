# Contributing to Sentient Trader

Thanks for taking a look at Sentient Trader. The project sits at the overlap of
multi-agent AI, market-data pipelines, paper trading, and observability, so good
contributions can be code, tests, docs, demo material, or careful analysis.

## Before You Start

- This is an educational paper-trading system, not financial advice.
- Use Alpaca paper-trading credentials only. Do not connect live-trading keys.
- Never commit real API keys, Supabase service-role keys, JWT secrets, `.env`
  files, database dumps, account identifiers, or private LangSmith traces.
- Security reports should go to the private process in `SECURITY.md`, not a
  public issue.

## Good First Contributions

Great starter areas:

- Improve setup docs for a specific operating system or cloud host.
- Add tests around an existing risk gate, rate limit, or replay behavior.
- Improve frontend loading, empty, and error states.
- Add example queries for calibration, reliability, or PnL analysis.
- Improve demo data, screenshots, or launch documentation.

Look for issues labeled `good first issue`, `help wanted`, or `documentation`.

## Local Setup

The full system needs Python 3.11+, Node.js 20+, Redis or Valkey, Supabase,
Alpaca paper credentials, and one LLM provider key. The README has the complete
walkthrough.

Useful checks:

```bash
cd backend/agent
python -m pytest -q

cd ../api
python -m pytest -q

cd ../../frontend
npm ci
npx tsc --noEmit
```

## Pull Request Checklist

Before opening a PR:

- Keep the change focused and explain the user-visible effect.
- Add or update tests for behavior changes.
- Update README/docs when setup, env vars, endpoints, or workflows change.
- Run the relevant checks locally when possible.
- Include screenshots or a short screen recording for UI changes.
- Call out any migration, deployment, or secret-handling impact.

## Project Boundaries

Contributions should preserve these safety constraints:

- Simulated signals must not submit Alpaca orders.
- Live trading support is out of scope unless it is explicitly isolated,
  disabled by default, and reviewed as a separate design.
- Risk gates and execution safeguards should favor false negatives over unsafe
  order submission.
- Public API changes must preserve authentication, rate limiting, and output
  sanitization.

## Commit Style

Short conventional-style subjects are preferred:

- `feat: add replay fixture loader`
- `fix: handle redis reconnect during pending rescue`
- `docs: clarify supabase setup`
- `test: cover simulated sell gate`

## Community

Use GitHub Discussions for questions, architecture ideas, showcases, and setup
help. Use Issues for actionable bugs and scoped work.
