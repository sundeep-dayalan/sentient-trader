/**
 * GET /api/portfolio?range=D|W|M|3M|6M|Y|5Y
 * --------------------------------
 * Fetches Alpaca paper portfolio history for Recharts.
 *
 * SECURITY:
 * - Public read (paper trading data — safe to show)
 * - Account number still stripped from response
 * - Range param is already allowlisted (safe)
 *
 * Returns: { history: Array<{ timestamp: string, equity: number }> }
 */

import { NextRequest, NextResponse } from "next/server";

const ALPACA_BASE_URL = "https://paper-api.alpaca.markets";

const RANGE_CONFIG = {
  D: { period: "1D", timeframe: "5Min" },
  W: { period: "1W", timeframe: "1H" },
  M: { period: "1M", timeframe: "1D" },
  "3M": { period: "3M", timeframe: "1D" },
  "6M": { period: "6M", timeframe: "1D" },
  Y: { period: "1A", timeframe: "1D" },
  "5Y": { period: "5A", timeframe: "1D" },
} as const;

type RangeKey = keyof typeof RANGE_CONFIG;

function rangeKey(value: string | null): RangeKey {
  return value && value in RANGE_CONFIG ? value as RangeKey : "D";
}

function numberValue(value: unknown) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function stringValue(value: unknown) {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
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
      intraday_reporting: "extended_hours",
    });

    const [data, account] = await Promise.all([
      alpacaFetch(`/v2/account/portfolio/history?${query.toString()}`),
      alpacaFetch("/v2/account"),
    ]);

    // Alpaca returns parallel arrays: timestamps[] and equity[].
    // Recharts needs [{timestamp, equity}] objects, so we zip them here.
    const timestamps = Array.isArray(data.timestamp) ? data.timestamp as number[] : [];
    const equities = Array.isArray(data.equity) ? data.equity as Array<number | null> : [];
    const profitLoss = Array.isArray(data.profit_loss) ? data.profit_loss as Array<number | null> : [];
    const profitLossPct = Array.isArray(data.profit_loss_pct) ? data.profit_loss_pct as Array<number | null> : [];

    const history = timestamps
      .map((ts: number, i: number) => {
        const equity = numberValue(equities[i]);
        return equity === null ? null : {
          timestamp: new Date(ts * 1000).toISOString(),
          equity,
        };
      })
      // Preserve real 0 values. Alpaca uses them to show pre-funding history.
      .filter((p): p is { timestamp: string; equity: number } => p !== null);

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

    const baseValue = numberValue(data.base_value);
    const rawLatestProfitLoss = numberValue(profitLoss.at(-1));
    const rawLatestProfitLossPct = numberValue(profitLossPct.at(-1));
    const rawLatestEquity = numberValue(equities.at(-1));
    const currentEquity = Number.isFinite(liveEquity) && liveEquity > 0
      ? liveEquity
      : history.at(-1)?.equity ?? 0;
    const derivedProfitLoss = baseValue !== null ? currentEquity - baseValue : rawLatestProfitLoss ?? 0;
    const derivedProfitLossPct = baseValue && baseValue > 0 ? derivedProfitLoss / baseValue : rawLatestProfitLossPct ?? 0;
    const liveValueChanged = rawLatestEquity !== null && Math.abs(currentEquity - rawLatestEquity) >= 0.005;

    return NextResponse.json(
      {
        history,
        summary: {
          equity: currentEquity,
          profitLoss: liveValueChanged ? derivedProfitLoss : rawLatestProfitLoss ?? derivedProfitLoss,
          profitLossPct: liveValueChanged ? derivedProfitLossPct : rawLatestProfitLossPct ?? derivedProfitLossPct,
          baseValue,
          baseValueAsOf: typeof data.base_value_asof === "string" ? data.base_value_asof : null,
        },
        account: {
          // NOTE: account_number intentionally stripped (F-005)
          id: stringValue(account.id),
          status: stringValue(account.status),
          currency: stringValue(account.currency),
          createdAt: stringValue(account.created_at),
          paper: true,
        },
        range,
        source: "alpaca",
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
