"""
Replay Fixtures
===============
Code-owned, deterministic inputs for ``REPLAY_MODE``, the no-key local demo
path described in ``docs/LOCAL_DEMO.md``.

A fixture supplies everything the pipeline would normally fetch from an
external provider for one headline:

  - the normalized news fields written to the Redis stream (always with
    ``is_simulated="true"``),
  - the market/account/position context ``fetch_context`` would pull from
    Alpaca, and
  - the four committee payloads the LLM router would otherwise generate.

Nothing here bypasses the agent graph. Replay only replaces provider *inputs*;
the pre-screen, committee, risk gate, price confirmation, execution, and
persistence nodes all run exactly as they do on live news. In particular the
existing simulated-signal block in ``assess_risk`` is what stops a replay BUY
or SELL from ever reaching the broker, and it is still the only thing doing so.

Determinism rules for anything added here:

  - no random values, no wall-clock reads, no network, no counters;
  - ``published_at`` is the single field stamped at injection time, so the
    freshness gate accepts a fixture whenever it is seeded;
  - a fixture is resolved only from its own exact headline and its reserved
    ``sentient-replay/`` source marker, so a contributor-authored headline
    never picks up a canned committee answer.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from schemas import NewsMessage

# Reserved source namespace. Ingestion never emits this prefix, so a fixture
# cannot be confused with a real article and a real article cannot be confused
# with a fixture.
REPLAY_SOURCE_PREFIX = "sentient-replay/"
REPLAY_IDENTITY_FIELD = "_sentient_replay_fixture"

# Committee stages a fixture answers for. ``momentum`` and ``value`` share the
# PersonaAnalysis schema, so they are told apart by the code-owned prompt
# markers below rather than by the response model alone.
REPLAY_STAGES = ("momentum", "value", "risk", "synthesis")

# Substrings of the two persona prompts that analyst.py builds in code (not
# from Supabase-editable system prompts). test_replay_mode.py asserts both are
# still present in the real prompts so a prompt edit cannot silently break
# stage resolution.
VALUE_PROMPT_MARKER = "As the Value Investor,"
MOMENTUM_PROMPT_MARKER = "from your momentum trading perspective"


class ReplayFixtureError(RuntimeError):
    """Raised when replay cannot resolve a fixture or a committee stage.

    The persona nodes already catch provider exceptions and fall back to a
    neutral, low-conviction opinion, so raising here fails closed to HOLD
    instead of inventing an answer for an unknown headline.
    """


@dataclass(frozen=True)
class ReplayFixture:
    """One versioned, self-contained replay case."""

    case: str
    ticker: str
    headline: str
    summary: str
    article_id: str
    source: str
    market_context: dict[str, Any]
    all_positions: list[dict[str, Any]] = field(default_factory=list)
    committee: dict[str, dict[str, Any]] = field(default_factory=dict)

    def stream_fields(self, *, published_at: Optional[str] = None) -> dict[str, str]:
        """Redis stream payload for this fixture, shaped like real ingestion."""
        return {
            "ticker": self.ticker,
            "headline": self.headline,
            "summary": self.summary,
            "source": self.source,
            "article_id": self.article_id,
            "published_at": published_at or _now_iso(),
            "is_simulated": "true",
        }

    def context(self) -> dict[str, Any]:
        """Deep copy of the canned market context, safe for graph mutation."""
        return copy.deepcopy(self.market_context)

    def positions(self) -> list[dict[str, Any]]:
        """Deep copy of the canned position list used by concentration checks."""
        return copy.deepcopy(self.all_positions)

    def payload(self, stage: str) -> dict[str, Any]:
        """Deep copy of the canned committee payload for one stage."""
        try:
            return copy.deepcopy(self.committee[stage])
        except KeyError as exc:
            raise ReplayFixtureError(
                f"replay fixture {self.case} has no payload for stage {stage}"
            ) from exc

    @property
    def prompt_marker(self) -> str:
        """The exact headline line analyst.py writes into every persona prompt.

        Matching on headline *and* reserved source together is what keeps a
        contributor-authored headline from resolving to a fixture.
        """
        return f'HEADLINE: "{self.headline}" — {self.source}'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _account(
    *,
    buying_power: float,
    cash: float,
    equity: float,
) -> dict[str, Any]:
    """Canned Alpaca account snapshot, shaped like AlpacaTrader.get_account_context."""
    return {
        "status": "ACTIVE",
        "currency": "USD",
        "trading_blocked": False,
        "transfers_blocked": False,
        "account_blocked": False,
        "shorting_enabled": False,
        "pattern_day_trader": False,
        "buying_power": buying_power,
        "regt_buying_power": buying_power,
        "daytrading_buying_power": buying_power,
        "cash": cash,
        "portfolio_value": equity,
        "equity": equity,
        "last_equity": equity,
        "maintenance_margin": 0.0,
        "daytrade_count": 0,
    }


def _flat_position(ticker: str) -> dict[str, Any]:
    """Canned flat position, shaped like AlpacaTrader.get_position_context."""
    return {
        "symbol": ticker,
        "qty": 0.0,
        "side": "flat",
        "market_value": 0.0,
        "cost_basis": 0.0,
        "avg_entry_price": None,
        "current_price": None,
        "unrealized_pl": 0.0,
        "unrealized_plpc": 0.0,
        "unrealized_intraday_pl": 0.0,
        "unrealized_intraday_plpc": 0.0,
    }


def _long_position(
    ticker: str,
    *,
    qty: float,
    avg_entry_price: float,
    current_price: float,
) -> dict[str, Any]:
    market_value = round(qty * current_price, 2)
    cost_basis = round(qty * avg_entry_price, 2)
    unrealized_pl = round(market_value - cost_basis, 2)
    return {
        "symbol": ticker,
        "qty": qty,
        "side": "long",
        "market_value": market_value,
        "cost_basis": cost_basis,
        "avg_entry_price": avg_entry_price,
        "current_price": current_price,
        "unrealized_pl": unrealized_pl,
        "unrealized_plpc": round(unrealized_pl / cost_basis, 4),
        "unrealized_intraday_pl": unrealized_pl,
        "unrealized_intraday_plpc": round(unrealized_pl / cost_basis, 4),
    }


# ── Fixtures ─────────────────────────────────────────────────────────────────
#
# Three cases, one per gate outcome the demo should show:
#   goog-partnership  bullish committee, BUY recommendation, blocked as simulated
#   lulu-guidance     bearish committee, SELL recommendation, blocked as simulated
#   len-leadership    mixed committee, HOLD recommendation, no order to block
#
# The headlines are the demo headlines from docs/LOCAL_DEMO.md, adjusted only
# far enough to clear the deterministic article-quality floor so the first two
# reach the committee rather than the pre-screen HOLD.

_GOOG = ReplayFixture(
    case="goog-partnership",
    ticker="GOOG",
    headline="GOOG expands enterprise AI partnership with major cloud customers",
    summary=(
        "Alphabet said its enterprise AI unit signed multi-year partnership "
        "agreements with three large cloud customers, adding committed capacity "
        "that management expects to convert into recognized revenue over the "
        "next four quarters."
    ),
    article_id="sentient-replay-goog-partnership-v1",
    source=f"{REPLAY_SOURCE_PREFIX}goog-partnership-v1",
    market_context={
        "price": 187.42,
        "day_change_pct": 1.24,
        "account": _account(buying_power=125_000.0, cash=61_500.0, equity=104_800.0),
        "position": _flat_position("GOOG"),
    },
    all_positions=[],
    committee={
        "momentum": {
            "stance": "BULLISH",
            "conviction": 0.82,
            "analysis": (
                "Committed multi-year capacity is a concrete catalyst rather "
                "than a directional read on the tape, and the stock is holding "
                "a modest gain into the print. The setup favors continuation "
                "while the catalyst is still fresh."
            ),
            "headline_take": (
                "Signed capacity commitments give the current uptrend something "
                "specific to price."
            ),
        },
        "value": {
            "stance": "BULLISH",
            "conviction": 0.78,
            "analysis": (
                "Contracted capacity that converts to recognized revenue over "
                "four quarters is the kind of visibility a fundamental case can "
                "underwrite. The supplied text gives no pricing or margin "
                "detail, so the thesis is durable but not precise."
            ),
            "headline_take": (
                "Revenue visibility improves, though the article withholds "
                "pricing and margin terms."
            ),
        },
        "risk": {
            "risk_level": "LOW",
            "risk_score": 0.28,
            "confidence_cap": 0.88,
            "disqualifying_conditions": [],
            "analysis": (
                "The supplied text names no regulatory, financing, or "
                "counterparty condition that would block execution. The only "
                "stated gap is the absence of contract economics."
            ),
            "headline_take": (
                "No article-specific disqualifying condition, so the cap stays "
                "just below the ceiling."
            ),
        },
        "synthesis": {
            "sentiment": 0.86,
            "confidence": 0.87,
            "reasoning": (
                "Both directional seats read the committed capacity the same "
                "way and risk found nothing disqualifying, so the committee "
                "agrees on a bullish, executable call."
            ),
            "action": "BUY",
        },
    },
)

_LULU = ReplayFixture(
    case="lulu-guidance",
    ticker="LULU",
    headline="LULU cuts guidance after weaker store traffic in North America",
    summary=(
        "Lululemon lowered its full-year revenue outlook, citing softer store "
        "traffic in North America through the quarter alongside a slower start "
        "to the holiday season. Management did not restate its margin target."
    ),
    article_id="sentient-replay-lulu-guidance-v1",
    source=f"{REPLAY_SOURCE_PREFIX}lulu-guidance-v1",
    market_context={
        "price": 268.15,
        "day_change_pct": -3.42,
        "account": _account(buying_power=125_000.0, cash=61_500.0, equity=104_800.0),
        "position": _long_position(
            "LULU", qty=4.0, avg_entry_price=291.10, current_price=268.15
        ),
    },
    all_positions=[
        _long_position("LULU", qty=4.0, avg_entry_price=291.10, current_price=268.15)
    ],
    committee={
        "momentum": {
            "stance": "BEARISH",
            "conviction": 0.80,
            "analysis": (
                "A guidance cut on stated traffic weakness is a hard catalyst "
                "against an existing long, and the tape is already confirming "
                "it with a sharp intraday decline."
            ),
            "headline_take": "A guidance cut with the tape already confirming it.",
        },
        "value": {
            "stance": "BEARISH",
            "conviction": 0.76,
            "analysis": (
                "Lower revenue guidance with no restated margin target removes "
                "the support the existing position was underwritten on. Trimming "
                "the position is consistent with the stated facts."
            ),
            "headline_take": (
                "The revenue case weakened and margin was left unaddressed."
            ),
        },
        "risk": {
            "risk_level": "MEDIUM",
            "risk_score": 0.44,
            "confidence_cap": 0.86,
            "disqualifying_conditions": [],
            "analysis": (
                "Reducing an existing long is de-risking, so the execution risk "
                "is mostly timing. The article gives no quantified magnitude for "
                "the cut, which caps how far confidence should run."
            ),
            "headline_take": (
                "De-risking trade with an unquantified cut, so cap confidence "
                "modestly."
            ),
        },
        "synthesis": {
            "sentiment": -0.85,
            "confidence": 0.84,
            "reasoning": (
                "Both directional seats read the guidance cut as a genuine "
                "deterioration against a position already held, and risk treats "
                "the reduction as de-risking rather than new exposure."
            ),
            "action": "SELL",
        },
    },
)

_LEN = ReplayFixture(
    case="len-leadership",
    ticker="LEN",
    headline="LEN appoints new operating chief as housing demand remains mixed",
    summary=(
        "Lennar named a new chief operating officer effective next quarter. The "
        "company repeated its earlier commentary that regional housing demand "
        "remains mixed and gave no new order or pricing figures."
    ),
    article_id="sentient-replay-len-leadership-v1",
    source=f"{REPLAY_SOURCE_PREFIX}len-leadership-v1",
    market_context={
        "price": 142.68,
        "day_change_pct": 0.18,
        "account": _account(buying_power=125_000.0, cash=61_500.0, equity=104_800.0),
        "position": _flat_position("LEN"),
    },
    all_positions=[],
    committee={
        "momentum": {
            "stance": "NEUTRAL",
            "conviction": 0.55,
            "analysis": (
                "A leadership appointment with repeated demand commentary gives "
                "no directional edge, and the tape is flat. There is nothing "
                "here to trade against."
            ),
            "headline_take": "A personnel change is not a directional catalyst.",
        },
        "value": {
            "stance": "NEUTRAL",
            "conviction": 0.60,
            "analysis": (
                "No order figures, pricing, or backlog detail were supplied, so "
                "the fundamental picture is unchanged. Repeated commentary is "
                "not new information."
            ),
            "headline_take": "Nothing in the text changes the fundamental case.",
        },
        "risk": {
            "risk_level": "MEDIUM",
            "risk_score": 0.40,
            "confidence_cap": 0.70,
            "disqualifying_conditions": [],
            "analysis": (
                "The main risk is over-reading an operational appointment as a "
                "demand signal. Absent figures, confidence should stay capped "
                "well below the execution threshold."
            ),
            "headline_take": "Thin evidence, so cap confidence below execution.",
        },
        "synthesis": {
            "sentiment": 0.12,
            "confidence": 0.44,
            "reasoning": (
                "The committee found no directional catalyst and no supplied "
                "figures, so HOLD is the accountable call rather than a "
                "low-conviction trade."
            ),
            "action": "HOLD",
        },
    },
)

REPLAY_FIXTURES: tuple[ReplayFixture, ...] = (_GOOG, _LULU, _LEN)

# Deterministic context for a replay run that receives a headline outside the
# fixture set (for example a contributor using the dashboard Signal Injector
# while REPLAY_MODE is on). It carries no price, so the execution plan blocks
# on "live price unavailable" and the committee still fails closed to HOLD.
UNKNOWN_REPLAY_CONTEXT: dict[str, Any] = {
    "price": None,
    "day_change_pct": None,
    "account": None,
    "position": None,
    "replay": {"fixture": None, "reason": "headline is not a replay fixture"},
}


def fixture_for_news(news: NewsMessage) -> Optional[ReplayFixture]:
    """Return the fixture this stream message came from, or None."""
    if news.is_simulated is not True or news.article_url is not None:
        return None
    source = (news.source or "").strip()
    if not source.startswith(REPLAY_SOURCE_PREFIX):
        return None
    headline = (news.headline or "").strip()
    ticker = (news.ticker or "").strip().upper()
    for fixture in REPLAY_FIXTURES:
        if (
            fixture.source == source
            and fixture.headline == headline
            and fixture.ticker == ticker
            and fixture.article_id == (news.article_id or "").strip()
            and fixture.summary == (news.summary or "").strip()
        ):
            return fixture
    return None


def fixture_for_case(case: Any) -> Optional[ReplayFixture]:
    """Resolve a code-owned replay identity, never user-controlled prompt text."""
    if not isinstance(case, str):
        return None
    return next((fixture for fixture in REPLAY_FIXTURES if fixture.case == case), None)


def resolve_stage(response_model: type, prompt: str) -> str:
    """Map one router call to a committee stage.

    ``RiskAssessment`` and ``SynthesisResult`` identify their stage by
    themselves. The two ``PersonaAnalysis`` seats are told apart by the
    code-owned prompt markers, checked value-first because the value prompt
    quotes the momentum opinion back to the model.
    """
    name = getattr(response_model, "__name__", "")
    if name == "RiskAssessment":
        return "risk"
    if name == "SynthesisResult":
        return "synthesis"
    if name == "PersonaAnalysis":
        if VALUE_PROMPT_MARKER in prompt:
            return "value"
        if MOMENTUM_PROMPT_MARKER in prompt:
            return "momentum"
        raise ReplayFixtureError(
            "replay could not tell the momentum seat from the value seat"
        )
    raise ReplayFixtureError(f"replay has no canned output for {name or response_model}")
