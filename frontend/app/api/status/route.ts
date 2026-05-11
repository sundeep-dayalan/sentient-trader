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
import { Redis } from "@upstash/redis";

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
  let supabaseStatus: ServiceStatus = "error";
  let lastTradeAt: string | null = null;
  
  try {
    const supabase = createClient();
    const { data, error } = await supabase
      .from("trades")
      .select("created_at")
      .order("created_at", { ascending: false })
      .limit(1)
      .single();

    if (error && error.code !== "PGRST116") {
      supabaseStatus = "error";
    } else {
      supabaseStatus = "ok";
      lastTradeAt = data?.created_at ?? null;
    }
  } catch {
    supabaseStatus = "error";
  }

  let redisStatus: ServiceStatus = "error";
  let agentStatus: ServiceStatus = "unknown";
  
  try {
    const redis = new Redis({
      url: process.env.UPSTASH_REDIS_URL!,
      token: process.env.UPSTASH_REDIS_TOKEN!,
    });
    
    // Check Redis connectivity and Agent heartbeat simultaneously
    const heartbeatStr = await redis.get<string>("agent:heartbeat");
    redisStatus = "ok";
    
    if (heartbeatStr) {
      const heartbeatAge = Date.now() / 1000 - parseInt(heartbeatStr, 10);
      if (heartbeatAge < 30) {
        agentStatus = "ok";
      } else if (heartbeatAge < 120) {
        agentStatus = "stale";
      } else {
        agentStatus = "error";
      }
    } else {
      // Missing heartbeat -> agent is not running
      agentStatus = "error";
    }
  } catch {
    redisStatus = "error";
    agentStatus = "unknown";
  }

  // Groq doesn't have a direct health endpoint here, so we infer it from the 
  // last trade if the agent is running, or just assume ok if agent is ok
  let groqStatus: ServiceStatus = "unknown";
  if (agentStatus === "ok") {
    groqStatus = "ok";
  } else if (lastTradeAt) {
    const ageH = (Date.now() - new Date(lastTradeAt).getTime()) / (1000 * 60 * 60);
    groqStatus = ageH < 2 ? "ok" : ageH < 48 ? "stale" : "error";
  } else {
    groqStatus = "unknown";
  }

  return {
    supabase:    supabaseStatus,
    groq:        groqStatus,
    redis:       redisStatus,
    agent:       agentStatus,
    lastTradeAt,
  };
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
