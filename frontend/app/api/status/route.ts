/**
 * GET /api/status
 * ---------------
 * Parallel health checks for all Sentient Trader integrations.
 *
 * Direct checks: Alpaca (paper API clock), Supabase (last trade query)
 * Inferred checks: Groq, Redis, Agent — all inferred from last trade recency.
 *   If the agent is producing trades, the full pipeline (ingestion → Redis →
 *   LangGraph → Groq → Alpaca) must be working end-to-end.
 */

import { NextResponse }  from "next/server";
import { createClient }  from "@/lib/supabase";

type ServiceStatus = "ok" | "stale" | "error" | "unknown";

async function checkAlpaca(): Promise<ServiceStatus> {
  try {
    const res = await fetch("https://paper-api.alpaca.markets/v2/clock", {
      headers: {
        "APCA-API-KEY-ID":     process.env.ALPACA_API_KEY!,
        "APCA-API-SECRET-KEY": process.env.ALPACA_SECRET_KEY!,
      },
      next: { revalidate: 0 },
    });
    return res.ok ? "ok" : "error";
  } catch {
    return "error";
  }
}

async function checkSupabaseAndPipeline(): Promise<{
  supabase: ServiceStatus;
  groq:     ServiceStatus;
  redis:    ServiceStatus;
  agent:    ServiceStatus;
  lastTradeAt: string | null;
}> {
  try {
    const supabase = createClient();
    const { data, error } = await supabase
      .from("trades")
      .select("created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .single();

    if (error && error.code !== "PGRST116") {
      // PGRST116 = no rows, which is ok (fresh deployment)
      return { supabase: "error", groq: "unknown", redis: "unknown", agent: "unknown", lastTradeAt: null };
    }

    const lastTradeAt = data?.created_at ?? null;

    let pipelineStatus: ServiceStatus = "unknown";
    if (lastTradeAt) {
      const ageMs = Date.now() - new Date(lastTradeAt).getTime();
      const ageH  = ageMs / (1000 * 60 * 60);
      if (ageH < 2)  pipelineStatus = "ok";
      else if (ageH < 48) pipelineStatus = "stale";
      else pipelineStatus = "error";
    }

    return {
      supabase:    "ok",
      groq:        pipelineStatus,
      redis:       pipelineStatus,
      agent:       pipelineStatus,
      lastTradeAt,
    };
  } catch {
    return { supabase: "error", groq: "unknown", redis: "unknown", agent: "unknown", lastTradeAt: null };
  }
}

export async function GET() {
  const [alpaca, pipeline] = await Promise.all([
    checkAlpaca(),
    checkSupabaseAndPipeline(),
  ]);

  return NextResponse.json(
    { alpaca, ...pipeline },
    { headers: { "Cache-Control": "no-store" } }
  );
}
