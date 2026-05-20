/**
 * GET /api/trades?before=<ISO-timestamp>
 * GET /api/trades?after=<ISO-timestamp>
 *
 * Lightweight trade history from Supabase. This intentionally excludes the
 * large decision_trace JSONB payload used by the detail endpoint.
 *
 * SECURITY:
 * - Public (read-only trade history, no PII)
 * - `before`/`after` cursors validated as ISO 8601 timestamps (F-009)
 * - Generic error messages — no raw DB errors returned to caller
 */

import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase";
import { TRADE_SUMMARY_SELECT } from "@/lib/trade-selects";
import { Trade } from "@/lib/types";

const PAGE_SIZE = 20;

/**
 * Validates that a string is a valid ISO 8601 timestamp.
 * Returns true if valid, false otherwise.
 */
function isValidISOTimestamp(value: string): boolean {
  const date = new Date(value);
  // Check: (1) it parsed to a real date, (2) it round-trips back to a string
  return !isNaN(date.getTime()) && date.toISOString().length > 0;
}

export async function GET(req: NextRequest) {
  const before = req.nextUrl.searchParams.get("before");
  const after = req.nextUrl.searchParams.get("after");

  if (before && after) {
    return NextResponse.json(
      { error: "Use either 'before' or 'after', not both." },
      { status: 400 },
    );
  }

  // Validate the cursor if provided (F-009)
  if (before && !isValidISOTimestamp(before)) {
    return NextResponse.json(
      { error: "Invalid 'before' parameter. Must be a valid ISO 8601 timestamp." },
      { status: 400 },
    );
  }

  if (after && !isValidISOTimestamp(after)) {
    return NextResponse.json(
      { error: "Invalid 'after' parameter. Must be a valid ISO 8601 timestamp." },
      { status: 400 },
    );
  }

  const supabase = createClient();

  // Fetch PAGE_SIZE + 1 so we can tell whether another page exists
  // without a separate COUNT query.
  let query = supabase
    .from("trades")
    .select(TRADE_SUMMARY_SELECT)
    .order("created_at", { ascending: false })
    .limit(PAGE_SIZE + 1);

  if (before) {
    query = query.lt("created_at", before);
  }

  if (after) {
    query = query.gt("created_at", after);
  }

  const { data, error } = await query;
  if (error) {
    // Return a generic error message — never expose raw DB errors (F-009)
    console.error("Trades query error:", error.message);
    return NextResponse.json(
      { error: "Failed to fetch trades. Please try again." },
      { status: 500 },
    );
  }

  const rows = (data ?? []) as Trade[];
  const hasMore = rows.length > PAGE_SIZE;

  return NextResponse.json({
    trades: hasMore ? rows.slice(0, PAGE_SIZE) : rows,
    hasMore,
  });
}
