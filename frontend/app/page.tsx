/**
 * Dashboard Page — server component entry point.
 *
 * Runs on the server at request time to fetch the 20 most recent trades
 * from Supabase. This pre-populates the dashboard on first load so the
 * page doesn't flash empty while the client hydrates.
 *
 * After hydration, DashboardClient listens for Realtime inserts on the slim
 * trades table and fetches full traces only when a signal is opened.
 */

import { computeDashboardStats } from "@/lib/dashboardStats";
import { createClient }    from "@/lib/supabase";
import { TRADE_STATS_SELECT, TRADE_SUMMARY_SELECT } from "@/lib/trade-selects";
import { DashboardStats, Trade } from "@/lib/types";
import DashboardClient     from "./DashboardClient";

// Always fetch fresh data — never serve a stale cached snapshot
export const dynamic = "force-dynamic";

async function getInitialTrades(): Promise<Trade[]> {
  const supabase = createClient();

  const { data, error } = await supabase
    .from("trades")
    .select(TRADE_SUMMARY_SELECT)
    .order("created_at", { ascending: false })
    .limit(20);

  if (error) {
    console.error("Failed to fetch initial trades:", error.message);
    return [];
  }

  return data ?? [];
}

async function getInitialStats(): Promise<DashboardStats | null> {
  const supabase = createClient();

  const { data, error } = await supabase
    .from("trades")
    .select(TRADE_STATS_SELECT)
    .limit(10000);

  if (error) {
    console.error("Failed to fetch dashboard stats:", error.message);
    return null;
  }

  return computeDashboardStats(data ?? []);
}

export default async function Page() {
  const [initialTrades, initialStats] = await Promise.all([
    getInitialTrades(),
    getInitialStats(),
  ]);

  return <DashboardClient initialTrades={initialTrades} initialStats={initialStats} />;
}
