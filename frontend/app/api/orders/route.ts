/**
 * GET /api/orders
 * ----------------
 * Fetches Alpaca paper account, positions, and recent orders server-side.
 *
 * SECURITY:
 * - Public read (paper trading data — safe to show)
 * - Account number still stripped from response
 * - Validates `status` param against allowlist (F-018)
 */

import { NextRequest, NextResponse } from "next/server";

const ALPACA_BASE_URL = "https://paper-api.alpaca.markets";

// Allowed values for the status query param (F-018)
const ALLOWED_STATUSES = ["open", "closed", "all"];

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
    next: { revalidate: 0 },
  });

  if (!response.ok) {
    throw new Error(`Alpaca API returned HTTP ${response.status} for ${path}`);
  }

  return response.json();
}

/**
 * Remove sensitive fields from the Alpaca account object
 * so they're never sent to the browser.
 */
function sanitizeAccount(account: Record<string, unknown>) {
  // Create a copy without the sensitive fields
  const { account_number, ...safe } = account;
  return safe;
}

export async function GET(request: NextRequest) {
  try {
    const limit = Math.min(
      Math.max(Number(request.nextUrl.searchParams.get("limit") ?? 100), 1),
      500
    );

    // Validate status against allowlist (F-018)
    const rawStatus = request.nextUrl.searchParams.get("status") ?? "all";
    const status = ALLOWED_STATUSES.includes(rawStatus) ? rawStatus : "all";

    const query = new URLSearchParams({
      status,
      limit: String(limit),
      direction: "desc",
      nested: "true",
    });

    const [account, positions, orders] = await Promise.all([
      alpacaFetch("/v2/account"),
      alpacaFetch("/v2/positions"),
      alpacaFetch(`/v2/orders?${query.toString()}`),
    ]);

    return NextResponse.json(
      {
        account: sanitizeAccount(account),
        positions,
        orders,
        fetchedAt: new Date().toISOString(),
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (error) {
    console.error("Orders fetch error:", error);
    return NextResponse.json(
      {
        account: null,
        positions: [],
        orders: [],
        fetchedAt: new Date().toISOString(),
        error: error instanceof Error ? error.message : "Unknown orders fetch error",
      },
      { status: 200, headers: { "Cache-Control": "no-store" } }
    );
  }
}
