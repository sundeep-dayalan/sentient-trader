/**
 * Hardcoded simulation scenarios for the recruiter demo.
 *
 * These are based on real historical headline patterns that caused large
 * single-day stock moves. They're chosen specifically to produce high-
 * confidence BUY signals from the AI — guaranteeing the dashboard lights
 * up even when the real market is closed on a weekend.
 */

import { SimulationScenario } from "./types";

export const SIMULATION_SCENARIOS: SimulationScenario[] = [
  {
    ticker: "NVDA",
    headline:
      "NVIDIA reports record quarterly revenue of $26 billion, crushing analyst estimates by 22%",
    source: "Reuters",
  },
  {
    ticker: "AAPL",
    headline:
      "Apple unveils breakthrough M4 neural engine chip with 3x AI performance gains over previous generation",
    source: "Bloomberg",
  },
  {
    ticker: "TSLA",
    headline:
      "Tesla delivers 500,000 vehicles in Q4, smashing analyst forecasts by 30% as demand surges",
    source: "CNBC",
  },
  {
    ticker: "AMZN",
    headline:
      "Amazon AWS revenue surges 37% year-over-year as enterprise cloud adoption accelerates",
    source: "Wall Street Journal",
  },
  {
    ticker: "MSFT",
    headline:
      "Microsoft Copilot AI reaches 1 million enterprise subscribers, beating guidance by wide margin",
    source: "Financial Times",
  },
];
