/**
 * POST /api/orders/cancel
 * -----------------------
 * Cancels one or more Alpaca paper orders server-side.
 *
 * SECURITY:
 * - Requires authenticated super user (SEC-03 fix: prevents IDOR)
 * - Caps orderIds array at 50 to prevent abuse
 *
 * Body: { orderIds: string[] }
 */

import { NextRequest, NextResponse } from "next/server";
import { requireSuperUser } from "@/lib/auth-helpers";

const ALPACA_BASE_URL = "https://paper-api.alpaca.markets";

// Maximum number of orders that can be cancelled in one request
const MAX_ORDER_IDS = 50;

interface CancelResult {
  id: string;
  ok: boolean;
  status: number;
  message: string;
}

function headers() {
  const apiKey = process.env.ALPACA_API_KEY;
  const secretKey = process.env.ALPACA_SECRET_KEY;

  if (!apiKey || !secretKey) {
    throw new Error("Missing Alpaca API credentials");
  }

  return {
    "APCA-API-KEY-ID": apiKey,
    "APCA-API-SECRET-KEY": secretKey,
  };
}

async function cancelOrder(orderId: string): Promise<CancelResult> {
  const response = await fetch(`${ALPACA_BASE_URL}/v2/orders/${encodeURIComponent(orderId)}`, {
    method: "DELETE",
    headers: headers(),
  });

  if (response.status === 204) {
    return { id: orderId, ok: true, status: 204, message: "Cancel request accepted" };
  }

  const body = await response.json().catch(() => ({}));
  const message = typeof body?.message === "string"
    ? body.message
    : `Alpaca returned HTTP ${response.status}`;

  return { id: orderId, ok: false, status: response.status, message };
}

export async function POST(request: NextRequest) {
  // ── Auth check: requires a super user (admin) ─────────────────
  const authResult = await requireSuperUser();
  if (authResult instanceof NextResponse) return authResult;

  try {
    const body = await request.json().catch(() => ({}));
    const orderIds: string[] = Array.isArray(body.orderIds)
      ? Array.from(new Set(body.orderIds.filter((id: unknown) => typeof id === "string" && id.length > 0)))
      : [];

    if (orderIds.length === 0) {
      return NextResponse.json(
        { results: [], error: "No order ids provided" },
        { status: 400 }
      );
    }

    // Cap the number of orders to prevent abuse
    if (orderIds.length > MAX_ORDER_IDS) {
      return NextResponse.json(
        { results: [], error: `Too many order IDs. Maximum is ${MAX_ORDER_IDS}.` },
        { status: 400 }
      );
    }

    const results = await Promise.all(orderIds.map(cancelOrder));

    return NextResponse.json(
      {
        results,
        canceled: results.filter(result => result.ok).length,
        failed: results.filter(result => !result.ok).length,
      },
      { headers: { "Cache-Control": "no-store" } }
    );
  } catch (error) {
    console.error("Cancel orders error:", error);
    return NextResponse.json(
      {
        results: [],
        canceled: 0,
        failed: 0,
        error: error instanceof Error ? error.message : "Unknown cancel orders error",
      },
      { status: 500, headers: { "Cache-Control": "no-store" } }
    );
  }
}
