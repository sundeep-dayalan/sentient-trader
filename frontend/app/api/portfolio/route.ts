/**
 * GET /api/portfolio
 * -------------------
 * Fetches the Alpaca paper trading portfolio history and returns it
 * formatted for Recharts consumption.
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

import { NextResponse } from "next/server";

export async function GET() {
  try {
    const apiKey    = process.env.ALPACA_API_KEY!;
    const secretKey = process.env.ALPACA_SECRET_KEY!;

    const response = await fetch(
      "https://paper-api.alpaca.markets/v2/account/portfolio/history?period=1W&timeframe=1D&extended_hours=false",
      {
        headers: {
          "APCA-API-KEY-ID":     apiKey,
          "APCA-API-SECRET-KEY": secretKey,
        },
        // Cache the response for 60 seconds — Alpaca's history doesn't change
        // every second, so hammering their API on every render is wasteful.
        next: { revalidate: 60 },
      }
    );

    if (!response.ok) {
      throw new Error(`Alpaca API returned HTTP ${response.status}`);
    }

    const data = await response.json();

    // Alpaca returns parallel arrays: timestamps[] (Unix seconds) and equity[] (dollars).
    // Recharts needs [{timestamp, equity}] objects, so we zip them here.
    const history = (data.timestamp as number[]).map((ts: number, i: number) => ({
      timestamp: new Date(ts * 1000).toISOString(),
      equity:    data.equity[i] as number,
    }));

    return NextResponse.json({ history });
  } catch (err) {
    console.error("Portfolio fetch error:", err);
    // Return empty array — the chart will show a "no data" state gracefully
    return NextResponse.json({ history: [] }, { status: 200 });
  }
}
