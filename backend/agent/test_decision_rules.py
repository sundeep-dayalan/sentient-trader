import unittest

from decision_rules import ArticleQuality, build_execution_plan, committee_metrics
from schemas import PersonaOpinion, TradeAnalysis


class DecisionRulesTests(unittest.TestCase):
    def high_quality(self) -> ArticleQuality:
        return ArticleQuality(
            score=0.85,
            grade="HIGH",
            category="hard catalyst",
            reasons=["Concrete catalyst."],
            flags=[],
            has_summary=True,
        )

    def test_risk_manager_does_not_count_as_directional_dissenter(self) -> None:
        analysis = TradeAnalysis(
            committee=[
                PersonaOpinion(
                    name="Momentum Trader",
                    stance="BULLISH",
                    conviction=0.8,
                    view="Momentum confirms the catalyst.",
                    reasoning="The supplied news supports upside.",
                ),
                PersonaOpinion(
                    name="Value Investor",
                    stance="BULLISH",
                    conviction=0.7,
                    view="Fundamentals support the reaction.",
                    reasoning="The supplied news supports upside.",
                ),
                PersonaOpinion(
                    name="Risk Manager",
                    stance="NEUTRAL",
                    conviction=0.9,
                    view="No article-specific disqualifier.",
                    reasoning="Execution risk is generic.",
                    risk_level="LOW",
                    risk_confidence_cap=0.95,
                    disqualifying_conditions=[],
                ),
            ],
            sentiment=0.85,
            confidence=0.9,
            reasoning="Both directional analysts agree and risk is low.",
            action="BUY",
        )

        metrics = committee_metrics(analysis, self.high_quality(), {"day_change_pct": 1})

        self.assertEqual(metrics["agreement"], 1.0)
        self.assertEqual(metrics["calibrated_confidence"], 0.9)
        self.assertNotIn("split committee", metrics["cap_reasons"])

    def test_risk_disqualifier_caps_execution_confidence(self) -> None:
        analysis = TradeAnalysis(
            committee=[
                PersonaOpinion(
                    name="Momentum Trader",
                    stance="BULLISH",
                    conviction=0.9,
                    view="Momentum confirms the catalyst.",
                    reasoning="The supplied news supports upside.",
                ),
                PersonaOpinion(
                    name="Value Investor",
                    stance="BULLISH",
                    conviction=0.85,
                    view="Fundamentals support the reaction.",
                    reasoning="The supplied news supports upside.",
                ),
                PersonaOpinion(
                    name="Risk Manager",
                    stance="NEUTRAL",
                    conviction=0.95,
                    view="A disqualifying account constraint exists.",
                    reasoning="The trade should not execute while the account is blocked.",
                    risk_level="CRITICAL",
                    risk_confidence_cap=0.44,
                    disqualifying_conditions=["Alpaca account is trading blocked."],
                ),
            ],
            sentiment=0.9,
            confidence=0.95,
            reasoning="Directional signal is strong but risk blocks execution.",
            action="BUY",
        )

        metrics = committee_metrics(analysis, self.high_quality(), {"day_change_pct": 1})

        self.assertEqual(metrics["calibrated_confidence"], 0.44)
        self.assertIn("critical risk level", metrics["cap_reasons"])
        self.assertIn("risk disqualifying condition", metrics["cap_reasons"])
        self.assertEqual(
            metrics["risk_disqualifying_conditions"],
            ["Alpaca account is trading blocked."],
        )

    def test_sell_plan_blocks_flat_accounts(self) -> None:
        plan = build_execution_plan(
            action="SELL",
            order_qty=1,
            market_context={
                "price": 10,
                "account": {"buying_power": 1000},
                "position": {"qty": 0},
            },
        )

        self.assertEqual(plan["quantity"], 0)
        self.assertEqual(plan["position_intent"], "no_long_position")
        self.assertIn(
            "No long position to reduce; short sells are disabled by policy.",
            plan["blocked_reasons"],
        )


if __name__ == "__main__":
    unittest.main()
