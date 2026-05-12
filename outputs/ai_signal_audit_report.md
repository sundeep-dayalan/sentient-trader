# AI Signal Committee Audit

Source: `Supabase Snippet AI Trade Signals Storage.csv`
Rows analyzed: **100**. Date range: **2026-05-11 20:20:45.227289+00** to **2026-05-12 01:38:18.772728+00**.

## Executive summary
- Final PM actions: {'HOLD': 73, 'SELL': 25, 'BUY': 2}.
- Executed/submitted according to trace: **1 / 100**. Directional PM recommendations blocked by risk gate: **26**.
- Persona stance mix: Momentum {'BULLISH': 51, 'BEARISH': 43, 'NEUTRAL': 6}; Value {'NEUTRAL': 62, 'BEARISH': 26, 'BULLISH': 12}; Risk {'BEARISH': 80, 'NEUTRAL': 20}.
- Main finding: the committee is conservative and often coherent, but the risk manager is structurally bearish, the value analyst frequently makes unsourced fundamental claims, and the risk gate threshold is so high that nearly every directional signal becomes non-executable.

## Flag counts
- **risk_manager_generic_macro_or_regulatory_risk**: 85
- **value_analyst_used_technical_or_unsourced_market_claims**: 38
- **pm_directional_but_risk_gate_blocked**: 26
- **pm_action_differs_from_weighted_committee_SELL**: 16
- **high_confidence_hold**: 2
- **broad_watchlist_or_trending_headline**: 1
- **historical_return_article_not_trade_news**: 1

## Highest-priority signal reviews
| row | ticker | pm_action | confidence | weighted_implied_action | severity_score | flags | headline |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2 | FTAI | HOLD | 0.65 | SELL | 6 | historical_return_article_not_trade_news; value_analyst_used_technical_or_unsourced_mar... | Here's How Much $100 Invested In FTAI Aviation 10 Years Ago Would Be Worth Today |
| 1 | PLUG | HOLD | 0.42 | HOLD | 5 | broad_watchlist_or_trending_headline; value_analyst_used_technical_or_unsourced_market_... | Plug Power, AST SpaceMobile, Quantum Computing Inc., Rigetti, And GameStop: Why These 5... |
| 10 | STRR | HOLD | 0.78 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; risk_manager_generic_macro_or_regulator... | Star Equity Hldgs Q1 Adj. EPS $(0.99) Misses $(0.67) Estimate, Sales $50.061M Beat $17.... |
| 90 | PACS | BUY | 0.74 | BUY | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | PACS Group Q1 EPS $0.50 Beats $0.42 Estimate, Sales $1.420B Beat $1.363B Estimate |
| 9 | SMCI | SELL | 0.72 | SELL | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | Super Micro Computer Reiterates Its Fiscal Year 2026 Business Outlook As Previously Sta... |
| 34 | TPG | SELL | 0.65 | SELL | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | Millennium Management Cuts Share Stake In TPG To 4.3% As Of March 31 From A Stake Of 6.... |
| 47 | UP | SELL | 0.65 | SELL | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | Wheels Up Experience Q1 EPS $(2.29) Up From $(2.84) YoY, Sales $168.922M Down From $177... |
| 60 | HIMS | HOLD | 0.65 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; value_analyst_used_technical_or_unsourc... | Why Hims & Hers Health Are Falling Monday |
| 70 | STEM | HOLD | 0.65 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; value_analyst_used_technical_or_unsourc... | UBS Maintains Neutral on Stem, Lowers Price Target to $10.5 |
| 72 | STE | HOLD | 0.65 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; value_analyst_used_technical_or_unsourc... | Steris Q4 Adj. EPS $2.83 Misses $2.85 Estimate, Sales $1.588B Miss $1.595B Estimate |
| 96 | CLSK | SELL | 0.64 | SELL | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | Cleanspark Q2 EPS $(1.52) Misses $(0.50) Estimate, Sales $136.408M Miss $145.351M Estimate |
| 71 | NPWR | SELL | 0.62 | SELL | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | NET Power Q1 EPS $(0.12) Misses $(0.07) Estimate |
| 3 | MARA | SELL | 0.58 | HOLD | 4 | pm_directional_but_risk_gate_blocked; value_analyst_used_technical_or_unsourced_market_... | UPDATE: Marathon Digital Holdings Q1 Adj. EPS $(0.61) Beats $(2.15) Estimate, Sales $17... |
| 31 | COOK | HOLD | 0.58 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; value_analyst_used_technical_or_unsourc... | Traeger Affirms FY2026 Sales Guidance of $465.000M-$485.000M vs $472.684M Est |
| 15 | ITRG | HOLD | 0.56 | SELL | 4 | pm_action_differs_from_weighted_committee_SELL; value_analyst_used_technical_or_unsourc... | Integra Resources Q1 Adj. EPS $0.07 Misses $0.09 Estimate, Sales $61.724M Beat $59.880M... |

## Directional recommendations blocked by risk gate
| row | ticker | pm_action | sentiment | confidence | risk_gate_reason | headline |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | MARA | SELL | -0.32 | 0.58 | Directional sentiment did not clear the configured threshold. | UPDATE: Marathon Digital Holdings Q1 Adj. EPS $(0.61) Beats $(2.15) Estimate, Sales $17... |
| 8 | SATL | SELL | -0.80 | 0.85 | Confidence did not clear the configured threshold. | Satellogic Q1 EPS $(0.84) Misses $(0.08) Estimate, Sales $6.107M Beat $5.273M Estimate |
| 9 | SMCI | SELL | -0.58 | 0.72 | Directional sentiment did not clear the configured threshold. | Super Micro Computer Reiterates Its Fiscal Year 2026 Business Outlook As Previously Sta... |
| 13 | EXOD | SELL | -0.80 | 0.78 | Confidence did not clear the configured threshold. | Exodus Movement Q1 EPS $(1.08) Misses $(0.08) Estimate, Sales $22.747M Miss $24.000M Es... |
| 17 | SKYX | SELL | -0.90 | 0.85 | Confidence did not clear the configured threshold. | CORRECTION: SKYX Platforms Q1 EPS $(0.07), Inline, Sales $22.000M Beat $21.767M Estimate |
| 18 | MVST | SELL | -0.80 | 0.78 | Confidence did not clear the configured threshold. | Microvast Stock Crashes After Q1 Earnings — What You Need To Know |
| 20 | PRSO | SELL | -0.60 | 0.65 | Directional sentiment did not clear the configured threshold. | CORRECTION: Peraso Q1 Adj. EPS $(0.20) Misses $(0.18) Estimate, Sales $963.000K Miss $9... |
| 30 | BZFD | SELL | -0.87 | 0.78 | Confidence did not clear the configured threshold. | BuzzFeed Q1 EPS $(0.40) Misses $(0.27) Estimate, Sales $31.572M Miss $35.079M Estimate |
| 32 | TSX:KNT | SELL | -0.60 | 0.70 | Directional sentiment did not clear the configured threshold. | K92 Mining Q1 EPS $0.48 Misses $0.57 Estimate, Sales $236.280M Miss $314.688M Estimate |
| 33 | TSX:EIF | BUY | 0.33 | 0.78 | Directional sentiment did not clear the configured threshold. | Exchange Income Q1 Adj. EPS $0.61 Beats $0.37 Estimate, Sales $866.583M Beat $818.893M ... |
| 34 | TPG | SELL | -0.70 | 0.65 | Directional sentiment did not clear the configured threshold. | Millennium Management Cuts Share Stake In TPG To 4.3% As Of March 31 From A Stake Of 6.... |
| 39 | BZFD | SELL | -0.50 | 0.65 | Directional sentiment did not clear the configured threshold. | BuzzFeed To Accept $120M Majority Investment From Byron Allen Affiliate To Buy 40M Shar... |
| 45 | MVST | SELL | -0.90 | 0.88 | Confidence did not clear the configured threshold. | Microvast Holdings Q1 Adj. EPS $(0.04) Misses $0.01 Estimate, Sales $60.600M Miss $99.0... |
| 47 | UP | SELL | -0.50 | 0.65 | Directional sentiment did not clear the configured threshold. | Wheels Up Experience Q1 EPS $(2.29) Up From $(2.84) YoY, Sales $168.922M Down From $177... |
| 61 | SCYX | SELL | -0.70 | 0.78 | Directional sentiment did not clear the configured threshold. | SCYNEXIS Q1 EPS $(0.42) Misses $(0.12) Estimate |
| 65 | PLBY | SELL | -0.70 | 0.78 | Directional sentiment did not clear the configured threshold. | Playboy Q1 Adj. EPS $(0.00) Misses $0.01 Estimate, Sales $30.236M Miss $30.615M Estimate |
| 68 | BKKT | SELL | -0.73 | 0.77 | Directional sentiment did not clear the configured threshold. | Bakkt Hldgs Q1 EPS $(0.41) Misses $(0.10) Estimate, Sales $243.593M Miss $310.887M Esti... |
| 71 | NPWR | SELL | -0.70 | 0.62 | Directional sentiment did not clear the configured threshold. | NET Power Q1 EPS $(0.12) Misses $(0.07) Estimate |
| 73 | EVI | SELL | -0.70 | 0.78 | Directional sentiment did not clear the configured threshold. | EVI Industries Q3 EPS $0.05 Misses $0.16 Estimate, Sales $101.134M Miss $111.300M Estimate |
| 75 | IHRT | SELL | -0.77 | 0.80 | Directional sentiment did not clear the configured threshold. | iHeartMedia Q1 EPS $(0.61) Misses $(0.45) Estimate |
| 78 | MGX | SELL | -0.70 | 0.78 | Directional sentiment did not clear the configured threshold. | Metagenomi Q1 EPS $(0.61) Misses $(0.55) Estimate, Sales $1.248M Miss $5.925M Estimate |
| 83 | GPRO | SELL | -0.73 | 0.84 | Directional sentiment did not clear the configured threshold. | GoPro Launches Strategic Review, Exploring Potential Sale Or Merger Following Defense E... |
| 84 | GPRO | SELL | -0.50 | 0.65 | Directional sentiment did not clear the configured threshold. | GoPro Q1 Adj. EPS $(0.35) Misses $(0.04) Estimate, Sales $99.065M Beat $69.916M Estimate |
| 90 | PACS | BUY | 0.53 | 0.74 | Directional sentiment did not clear the configured threshold. | PACS Group Q1 EPS $0.50 Beats $0.42 Estimate, Sales $1.420B Beat $1.363B Estimate |
| 94 | PSIX | SELL | -0.88 | 0.87 | Confidence did not clear the configured threshold. | Power Solutions Intl Q1 Adj. EPS $0.36 Misses $0.74 Estimate, Sales $128.592M Miss $160... |

## Recommended fixes
- **Separate recommendation from execution:** Rename trade_action in UI/reporting to PM recommendation unless risk_should_trade=true. Add executed_action/order_status fields so SELL/BUY does not imply an Alpaca order was placed.
- **Tune risk gate:** Current confidence threshold 0.90 blocks 99/100 rows. Consider paper-trading thresholds around confidence >=0.75 and abs(sentiment)>=0.70 plus position sizing caps, or keep 0.90 but treat most rows as analysis-only.
- **Constrain risk manager:** Risk persona never went bullish and was bearish 80/100. Force it to output risk level and disqualifying condition, not directional stance, or allow BULLISH when risks are low.
- **Ground value analyst:** Value used balance-sheet/moat/cash claims in most rows without supplied fundamentals. Feed actual fundamentals or require "not enough data" instead of invented details.
- **Filter weak Benzinga article types:** Drop/low-weight broad watchlist and historical-return headlines. They are not actionable catalysts.
- **Execution logging bug:** BIOX passed the risk gate and `execution.submitted=true`, but both `execution.order_id` and the CSV `order_id` are null while `client_order_id` exists. Capture Alpaca response IDs/status so submitted orders are auditable.
- **Add post-signal performance labels:** Store 15m/1h/end-of-day returns after each signal. Without outcomes, committee quality can only be judged structurally, not statistically.