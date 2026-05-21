/**
 * GET /api/status
 * ---------------
 * Parallel health checks for all Sentient Trader integrations.
 *
 * Direct browser access is only to this route. External service checks happen
 * server-side here, and LLM/provider health is reported by the backend agent
 * rather than requiring Groq secrets in the frontend deployment.
 */

import { NextResponse }  from "next/server";
import { createClient }  from "@/lib/supabase";
import { getRedis }    from "@/lib/redis";

export const runtime = "nodejs";

type ServiceStatus = "ok" | "stale" | "error" | "unknown";
type ServiceKey = "alpaca" | "supabase" | "groq" | "redis" | "agent";
type StatusDetails = Partial<Record<ServiceKey, string>>;

interface AgentState {
  phase?: string;
  detail?: string | null;
  updated_at?: number;
  groq?: ServiceStatus;
  groq_detail?: string;
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

function parseAgentState(raw: string | null): AgentState | null {
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AgentState;
  } catch {
    return null;
  }
}

function statusErrorDetail(prefix: string, error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return `${prefix}: ${message.slice(0, 240)}`;
}

async function checkSupabaseAndPipeline(): Promise<{
  supabase: ServiceStatus;
  groq:     ServiceStatus;
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
  } catch (error) {
    supabaseStatus = "error";
    details.supabase = statusErrorDetail("Could not query Supabase trades table", error);
  }

  let redisStatus: ServiceStatus = "error";
  let agentStatus: ServiceStatus = "unknown";
  let groqStatus: ServiceStatus = "unknown";
  
  try {
    const redis = await getRedis();
    
    // Check Redis connectivity and Agent heartbeat simultaneously
    const [heartbeatStr, agentStateRaw] = await Promise.all([
      redis.get("agent:heartbeat"),
      redis.get("agent:state"),
    ]);
    const agentState = parseAgentState(agentStateRaw);
    redisStatus = "ok";
    details.redis = "Redis reachable.";
    groqStatus = agentState?.groq ?? "unknown";
    details.groq = agentState?.groq_detail
      ?? "Groq is not checked by the frontend. Backend agent has not published Groq provider status yet.";
    
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
  } catch (error) {
    redisStatus = "error";
    agentStatus = "unknown";
    groqStatus = "unknown";
    details.redis = statusErrorDetail("Could not read Redis heartbeat", error);
    details.agent = "Agent status depends on Redis heartbeat, which could not be read.";
    details.groq = "Groq status depends on backend agent state, which could not be read from Redis.";
  }

  return {
    supabase:    supabaseStatus,
    groq:        groqStatus,
    redis:       redisStatus,
    agent:       agentStatus,
    lastTradeAt,
    lastHeartbeatAt,
    details,
  };
}

export async function GET() {
  const [alpaca, pipeline] = await Promise.all([
    checkAlpaca(),
    checkSupabaseAndPipeline(),
  ]);

  return NextResponse.json(
    {
      alpaca: alpaca.status,
      ...pipeline,
      checkedAt: new Date().toISOString(),
      details: {
        ...pipeline.details,
        alpaca: alpaca.detail,
      },
    },
    { headers: { "Cache-Control": "no-store" } }
  );
}
