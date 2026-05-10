/**
 * GET  /api/agent-config  — read current config from Supabase
 * POST /api/agent-config  — persist a config update to the Supabase agent_config table
 *
 * Uses the public anon key — RLS allows SELECT + UPDATE on agent_config for anon.
 * The Python agent reads from the same row at startup via config.reload_from_supabase().
 */

import { NextResponse } from "next/server";
import { createClient } from "@supabase/supabase-js";

interface ModelTier {
  id:      string;
  label:   string;
  reqDay:  string;
  tpm:     string;
  quality: "high" | "mid" | "fallback";
}

const MODEL_CASCADE: ModelTier[] = [
  { id: "openai/gpt-oss-120b",     label: "GPT-OSS 120B",         reqDay: "1K",    tpm: "8K",  quality: "high"     },
  { id: "llama-3.3-70b-versatile", label: "Llama 3.3 70B",        reqDay: "1K",    tpm: "12K", quality: "mid"      },
  { id: "llama-3.1-8b-instant",    label: "Llama 3.1 8B Instant", reqDay: "14.4K", tpm: "6K",  quality: "fallback" },
];

function supabase() {
  return createClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}

export async function GET() {
  const { data, error } = await supabase()
    .from("agent_config")
    .select("config")
    .eq("id", 1)
    .single();

  if (error || !data?.config) {
    return NextResponse.json({ error: error?.message ?? "Config row not found — run migration 004" }, { status: 500 });
  }

  const row = data.config as Record<string, unknown>;

  return NextResponse.json({
    thresholds: {
      buy_sentiment:  row.buy_sentiment_threshold  as number,
      sell_sentiment: row.sell_sentiment_threshold as number,
      confidence:     row.confidence_threshold     as number,
    },
    execution: {
      order_qty: row.order_qty as number,
    },
    model: {
      cascade:  MODEL_CASCADE,
      override: (row.model_override as string | null) ?? null,
    },
    prompts: {
      momentum:  row.momentum_system_prompt  as string,
      value:     row.value_system_prompt     as string,
      risk:      row.risk_system_prompt      as string,
      synthesis: row.synthesis_system_prompt as string,
    },
    consumer: {
      stream_key:    process.env.REDIS_STREAM_KEY ?? "market-news",
      batch_size:    10,
      poll_interval: 1.0,
      error_retry:   5.0,
    },
  }, { headers: { "Cache-Control": "no-store" } });
}

export async function POST(request: Request) {
  let body: Record<string, unknown>;
  try {
    body = await request.json() as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const thresholds = body.thresholds as Record<string, number> | undefined;
  const execution  = body.execution  as Record<string, number> | undefined;
  const model      = body.model      as Record<string, unknown> | undefined;
  const prompts    = body.prompts    as Record<string, string>  | undefined;

  const patch: Record<string, unknown> = {};
  if (thresholds) {
    if (thresholds.buy_sentiment  !== undefined) patch.buy_sentiment_threshold  = thresholds.buy_sentiment;
    if (thresholds.sell_sentiment !== undefined) patch.sell_sentiment_threshold = thresholds.sell_sentiment;
    if (thresholds.confidence     !== undefined) patch.confidence_threshold     = thresholds.confidence;
  }
  if (execution?.order_qty !== undefined) patch.order_qty = execution.order_qty;
  if (model)   patch.model_override         = model.override ?? null;
  if (prompts) {
    if (prompts.momentum)  patch.momentum_system_prompt  = prompts.momentum;
    if (prompts.value)     patch.value_system_prompt     = prompts.value;
    if (prompts.risk)      patch.risk_system_prompt      = prompts.risk;
    if (prompts.synthesis) patch.synthesis_system_prompt = prompts.synthesis;
  }

  const sb = supabase();

  // Deep-merge with existing row so untouched fields are preserved
  const { data: existing } = await sb.from("agent_config").select("config").eq("id", 1).single();
  const merged = { ...(existing?.config as Record<string, unknown> ?? {}), ...patch };

  const { data: updated, error } = await sb
    .from("agent_config")
    .update({ config: merged, updated_at: new Date().toISOString() })
    .eq("id", 1)
    .select();

  if (error) return NextResponse.json({ error: error.message }, { status: 500 });
  if (!updated || updated.length === 0) return NextResponse.json({ error: "Write blocked by RLS — check agent_config policies in Supabase" }, { status: 500 });
  return NextResponse.json({ ok: true });
}
