# Roadmap

This roadmap is intentionally practical: it focuses on improvements that make
Sentient Trader easier to try, trust, and contribute to.

## Now

- Publish `v0.1.0` with live demo, safety notes, and setup links.
- Enable GitHub Discussions and pin a launch/welcome thread.
- Record a short dashboard demo GIF or MP4.
- Merge or close stale Dependabot PRs so open work reflects human priorities.
- Create starter issues from `docs/LAUNCH_PLAN.md`.

## Next

- Add a no-key replay fixture mode for contributors who want to see traces
  without live Alpaca news.
- Add a devcontainer or Codespaces setup for faster onboarding.
- Add screenshots to `docs/LOCAL_DEMO.md`.
- Split the long README into focused docs while keeping the top-level README
  optimized for first-time visitors.
- Add more calibration queries and example analysis notebooks.

## Later

- Add hosted read-only API examples for decision trace exploration.
- Add a public architecture decision record series.
- Add provider-specific setup guides for Groq, OpenRouter, Railway, Render, and
  self-hosted Docker Compose.
- Add richer frontend states for first local run, missing dependencies, and
  stale worker health.

## Non-Goals

- Live-money trading by default.
- Closed-box recommendations without decision traces.
- Removing deterministic risk gates in favor of pure LLM judgment.
