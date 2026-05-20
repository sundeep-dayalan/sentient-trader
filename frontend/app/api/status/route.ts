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
type ServiceKey = "alpaca" | "supabase" | "groq" | "redis" | "agent";
type StatusDetails = Partial<Record<ServiceKey, string>>;

interface AgentState {
  phase?: string;
  detail?: string | null;
  updated_at?: number;
}

async function checkAlpaca(): Promise<{ status: ServiceStatus; detail: string }> {
  try {
    const res = await fetch("https://paper-api.alpaca.markets/v2/clock", {
      headers: {
        "APCA-API-KEY-ID":     process.env.ALPACA_API_KEY!,
        "APCA-API-SECRET-KEY": process.env.ALPACA_SECRET_KEY!,
      },
      next: { revalidate: 0 },
    });
    return {
      status: res.ok ? "ok" : "error",
      detail: res.ok ? "Paper trading clock reachable." : `Alpaca returned HTTP ${res.status}.`,
    };
  } catch {
    return { status: "error", detail: "Could not reach Alpaca paper API." };
  }
}

async function checkGroq(): Promise<{ status: ServiceStatus; detail: string }> {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) {
    return {
      status: "unknown",
      detail: "GROQ_API_KEY is not configured in this frontend deployment.",
    };
  }

  try {
    const res = await fetch("https://api.groq.com/openai/v1/models", {
      headers: {
        Authorization: `Bearer ${apiKey}`,
        Accept: "application/json",
      },
      next: { revalidate: 0 },
    });
    return {
      status: res.ok ? "ok" : "error",
      detail: res.ok ? "Groq models endpoint reachable." : `Groq returned HTTP ${res.status}.`,
    };
  } catch {
    return { status: "error", detail: "Could not reach Groq models endpoint." };
  }
}

function parseAgentState(raw: string | null): AgentState | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AgentState;
  } catch {
    return null;
  }
}

async function checkSupabaseAndPipeline(): Promise<{
  supabase: ServiceStatus;
  redis:    ServiceStatus;
  agent:    ServiceStatus;
  lastTradeAt: string | null;
  lastHeartbeatAt: string | null;
  details: StatusDetails;
}> {
  let supabaseStatus: ServiceStatus = "error";
  let lastTradeAt: string | null = null;
  let lastHeartbeatAt: string | null = null;
  const details: StatusDetails = {};
  
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
      details.supabase = error.message;
    } else {
      supabaseStatus = "ok";
      lastTradeAt = data?.created_at ?? null;
      details.supabase = "Trades table reachable.";
    }
  } catch {
    supabaseStatus = "error";
    details.supabase = "Could not query Supabase trades table.";
  }

  let redisStatus: ServiceStatus = "error";
  let agentStatus: ServiceStatus = "unknown";
  
  try {
    const redis = new Redis({
      url: process.env.UPSTASH_REDIS_URL!,
      token: process.env.UPSTASH_REDIS_TOKEN!,
    });
    
    // Check Redis connectivity and Agent heartbeat simultaneously
    const [heartbeatStr, agentStateRaw] = await Promise.all([
      redis.get<string>("agent:heartbeat"),
      redis.get<string>("agent:state"),
    ]);
    const agentState = parseAgentState(agentStateRaw);
    redisStatus = "ok";
    details.redis = "Redis reachable.";
    
    if (heartbeatStr) {
      const heartbeat = parseInt(heartbeatStr, 10);
      const heartbeatAge = Date.now() / 1000 - heartbeat;
      lastHeartbeatAt = Number.isFinite(heartbeat)
        ? new Date(heartbeat * 1000).toISOString()
        : null;
      const phase = agentState?.phase;
      const phaseDetail = agentState?.detail ? `: ${agentState.detail}` : "";
      if (heartbeatAge < 60 && phase === "polling") {
        agentStatus = "ok";
        details.agent = "Worker heartbeat is fresh and polling Redis stream.";
      } else if (heartbeatAge < 180) {
        agentStatus = "stale";
        details.agent = phase
          ? `Worker heartbeat is fresh but phase is ${phase}${phaseDetail}.`
          : "Worker heartbeat is fresh, but no phase state was published.";
      } else {
        agentStatus = "error";
        details.agent = `Worker heartbeat is stale by ${Math.floor(heartbeatAge / 60)} minutes.`;
      }
    } else {
      // Missing heartbeat -> agent is not running
      agentStatus = "error";
      details.agent = "No agent heartbeat found in Redis.";
    }
  } catch {
    redisStatus = "error";
    agentStatus = "unknown";
    details.redis = "Could not read Redis heartbeat.";
    details.agent = "Agent status depends on Redis heartbeat, which could not be read.";
  }

  return {
    supabase:    supabaseStatus,
    redis:       redisStatus,
    agent:       agentStatus,
    lastTradeAt,
    lastHeartbeatAt,
    details,
  };
}

export async function GET() {
  const [alpaca, groq, pipeline] = await Promise.all([
    checkAlpaca(),
    checkGroq(),
    checkSupabaseAndPipeline(),
  ]);

  return NextResponse.json(
    {
      alpaca: alpaca.status,
      groq: groq.status,
      ...pipeline,
      checkedAt: new Date().toISOString(),
      details: {
        ...pipeline.details,
        alpaca: alpaca.detail,
        groq: groq.detail,
      },
    },
    { headers: { "Cache-Control": "no-store" } }
  );
}
