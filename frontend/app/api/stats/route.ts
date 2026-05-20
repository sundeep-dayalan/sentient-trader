/**
 * GET /api/stats
 *
 * Returns aggregated dashboard statistics from the trades table.
 *
 * SECURITY:
 * - Public (read-only aggregated data, no PII)
 * - Query limited to 10,000 rows to prevent unbounded scan (F-008)
 * - Cached for 30 seconds to reduce DB load
 */

import { NextResponse } from "next/server";
import { computeDashboardStats } from "@/lib/dashboardStats";
import { createClient } from "@/lib/supabase";
import { TRADE_STATS_SELECT } from "@/lib/trade-selects";

export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = createClient();

  // Add .limit(10000) to prevent unbounded full-table scans (F-008)
  const { data, error } = await supabase
    .from("trades")
    .select(TRADE_STATS_SELECT)
    .limit(10000);

  if (error) {
    return NextResponse.json(
      {
        stats: null,
        fetchedAt: new Date().toISOString(),
        error: error.message,
      },
      { status: 500, headers: { "Cache-Control": "no-store" } }
    );
  }

  return NextResponse.json(
    {
      stats: computeDashboardStats(data ?? []),
      fetchedAt: new Date().toISOString(),
    },
    {
      headers: {
        // Cache for 30 seconds to reduce DB load (F-008)
        "Cache-Control": "public, max-age=30",
      },
    }
  );
}
