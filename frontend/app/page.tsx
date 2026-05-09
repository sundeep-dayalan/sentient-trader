/**
 * Dashboard Page — server component entry point.
 *
 * Runs on the server at request time to fetch the 20 most recent trades
 * from Supabase. This pre-populates the dashboard on first load so the
 * page doesn't flash empty while the client hydrates.
 *
 * After hydration, DashboardClient takes over and maintains the live
 * Supabase Realtime subscription for instant updates.
 */

import { createClient }    from "@/lib/supabase";
import { Trade }           from "@/lib/types";
import DashboardClient     from "./DashboardClient";

async function getInitialTrades(): Promise<Trade[]> {
  const supabase = createClient();

  const { data, error } = await supabase
    .from("trades")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(20);

  if (error) {
    console.error("Failed to fetch initial trades:", error.message);
    return [];
  }

  return data ?? [];
}

export default async function Page() {
  const initialTrades = await getInitialTrades();
  return <DashboardClient initialTrades={initialTrades} />;
}
