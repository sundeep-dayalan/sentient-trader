import { NextResponse } from "next/server";
import { computeDashboardStats } from "@/lib/dashboardStats";
import { createClient } from "@/lib/supabase";

export const dynamic = "force-dynamic";

export async function GET() {
  const supabase = createClient();

  const { data, error } = await supabase
    .from("trades")
    .select("trade_action, order_id, sentiment_score");

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
    { headers: { "Cache-Control": "no-store" } }
  );
}
