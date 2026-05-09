/**
 * GET /api/portfolio?range=D|W|M|Y
 * --------------------------------
 * Fetches Alpaca paper portfolio history and returns it formatted for
 * Recharts consumption.
 *
 * This runs server-side so the ALPACA_SECRET_KEY never reaches the browser.
 * Alpaca's portfolio history API requires the secret key — it must stay
 * server-only (no NEXT_PUBLIC_ prefix).
 *
 * Returns: { history: Array<{ timestamp: string, equity: number }> }
 *
 * On any error (market closed, bad key, network blip), returns an empty
 * history array so the chart degrades gracefully rather than crashing.
 */

import { NextRequest, NextResponse } from "next/server";

const ALPACA_BASE_URL = "https://paper-api.alpaca.markets";

const RANGE_CONFIG = {
  D: { period: "1D", timeframe: "5Min" },
  W: { period: "1W", timeframe: "1H" },
  M: { period: "1M", timeframe: "1D" },
  Y: { period: "1A", timeframe: "1D" },
} as const;

type RangeKey = keyof typeof RANGE_CONFIG;

function rangeKey(value: string | null): RangeKey {
  return value === "D" || value === "W" || value === "M" || value === "Y" ? value : "W";
}

async function alpacaFetch(path: string) {
  const apiKey = process.env.ALPACA_API_KEY;
  const secretKey = process.env.ALPACA_SECRET_KEY;

  if (!apiKey || !secretKey) {
    throw new Error("Missing Alpaca API credentials");
  }

  const response = await fetch(`${ALPACA_BASE_URL}${path}`, {
    headers: {
      "APCA-API-KEY-ID": apiKey,
      "APCA-API-SECRET-KEY": secretKey,
    },
    cache: "no-store",
  });

  if (!response.ok) {
    throw new Error(`Alpaca API returned HTTP ${response.status} for ${path}`);
  }

  return response.json();
}

export async function GET(request: NextRequest) {
  try {
    const range = rangeKey(request.nextUrl.searchParams.get("range"));
    const config = RANGE_CONFIG[range];

    const query = new URLSearchParams({
      period: config.period,
      timeframe: config.timeframe,
      extended_hours: "true",
    });

    const [data, account] = await Promise.all([
      alpacaFetch(`/v2/account/portfolio/history?${query.toString()}`),
      alpacaFetch("/v2/account"),
    ]);

    // Alpaca returns parallel arrays: timestamps[] (Unix seconds) and equity[] (dollars).
    // Recharts needs [{timestamp, equity}] objects, so we zip them here.
    const timestamps = Array.isArray(data.timestamp) ? data.timestamp as number[] : [];
    const equities = Array.isArray(data.equity) ? data.equity as Array<number | null> : [];

    const history = timestamps
      .map((ts: number, i: number) => ({
        timestamp: new Date(ts * 1000).toISOString(),
        equity: Number(equities[i]),
      }))
      // Alpaca returns null/0 for days before the account had any activity — drop them
      .filter((p) => p.equity > 0);

    const liveEquity = Number(account.portfolio_value ?? account.equity);
    const latest = history.at(-1);
    if (Number.isFinite(liveEquity) && liveEquity > 0) {
      const latestAgeMs = latest ? Date.now() - new Date(latest.timestamp).getTime() : Number.POSITIVE_INFINITY;
      const valueChanged = latest ? Math.abs(latest.equity - liveEquity) >= 0.005 : true;
      if (!latest || latestAgeMs > 45_000 || valueChanged) {
        history.push({
          timestamp: new Date().toISOString(),
          equity: liveEquity,
        });
      }
    }

    return NextResponse.json(
      {
        history,
        range,
        fetchedAt: new Date().toISOString(),
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (err) {
    console.error("Portfolio fetch error:", err);
    // Return empty array — the chart will show a "no data" state gracefully
    return NextResponse.json(
      {
        history: [],
        error: err instanceof Error ? err.message : "Unknown portfolio fetch error",
        fetchedAt: new Date().toISOString(),
      },
      { status: 200, headers: { "Cache-Control": "no-store" } }
    );
  }
}
