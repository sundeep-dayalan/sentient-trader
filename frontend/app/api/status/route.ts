/**
 * GET /api/status
 * ---------------
 * Parallel health checks for all Sentient Trader integrations.
 *
 * Direct checks: Alpaca (paper API clock), Supabase (last trade query),
 * Groq (/models), Redis (heartbeat read), Agent (heartbeat freshness).
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

async function checkGroq(): Promise<ServiceStatus> {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) return "unknown";

  try {
    const res = await fetch("https://api.groq.com/openai/v1/models", {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
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
  redis:    ServiceStatus;
  agent:    ServiceStatus;
  lastTradeAt: string | null;
  lastHeartbeatAt: string | null;
}> {
  let supabaseStatus: ServiceStatus = "error";
  let lastTradeAt: string | null = null;
  let lastHeartbeatAt: string | null = null;
  
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
      const heartbeat = parseInt(heartbeatStr, 10);
      const heartbeatAge = Date.now() / 1000 - heartbeat;
      lastHeartbeatAt = Number.isFinite(heartbeat)
        ? new Date(heartbeat * 1000).toISOString()
        : null;
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

  return {
    supabase:    supabaseStatus,
    redis:       redisStatus,
    agent:       agentStatus,
    lastTradeAt,
    lastHeartbeatAt,
  };
}

export async function GET() {
  const [alpaca, groq, pipeline] = await Promise.all([
    checkAlpaca(),
    checkGroq(),
    checkSupabaseAndPipeline(),
  ]);

  return NextResponse.json(
    { alpaca, groq, ...pipeline },
    { headers: { "Cache-Control": "no-store" } }
  );
}
