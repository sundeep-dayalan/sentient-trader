/**
 * GET /api/trades/:id
 *
 * Full trade detail, including the large Decision Core trace.
 * Keep this separate from the feed endpoint so list refreshes do not stream
 * every JSONB trace over the wire.
 */

import { NextResponse } from "next/server";
import { createClient } from "@/lib/supabase";
import { LEGACY_TRADE_DETAIL_SELECT, TRACE_DETAIL_SELECT, TRADE_SUMMARY_SELECT } from "@/lib/trade-selects";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

function reasoningFromTrace(value: unknown): string {
  if (!value || Array.isArray(value) || typeof value !== "object") return "";

  const trace = value as {
    portfolio_manager_decision?: { reasoning?: unknown };
  };
  const reasoning = trace.portfolio_manager_decision?.reasoning;
  return typeof reasoning === "string" ? reasoning : "";
}

export async function GET(
  _req: Request,
  context: { params: Promise<{ id: string }> },
) {
  const { id } = await context.params;

  if (!UUID_RE.test(id)) {
    return NextResponse.json(
      { error: "Invalid trade id." },
      { status: 400 },
    );
  }

  const supabase = createClient();
  const { data: trade, error } = await supabase
    .from("trades")
    .select(TRADE_SUMMARY_SELECT)
    .eq("id", id)
    .single();

  if (error) {
    return NextResponse.json(
      { error: "Trade not found." },
      { status: 404 },
    );
  }

  const { data: traceRow, error: traceError } = await supabase
    .from("trade_decision_traces")
    .select(TRACE_DETAIL_SELECT)
    .eq("trade_id", id)
    .maybeSingle();

  if (traceError) {
    // Compatibility fallback for deployments before migration 009 is applied.
    const { data: legacyTrade, error: legacyError } = await supabase
      .from("trades")
      .select(LEGACY_TRADE_DETAIL_SELECT)
      .eq("id", id)
      .single();

    if (!legacyError) {
      return NextResponse.json(
        { trade: legacyTrade },
        {
          headers: {
            "Cache-Control": "private, max-age=60",
          },
        },
      );
    }

    console.error("Trade trace query error:", traceError.message);
  }

  return NextResponse.json(
    {
      trade: {
        ...trade,
        reasoning: traceRow?.reasoning ?? reasoningFromTrace(traceRow?.decision_trace),
        article_source: traceRow?.article_source ?? null,
        article_id: traceRow?.article_id ?? null,
        decision_trace: traceRow?.decision_trace ?? null,
      },
    },
    {
      headers: {
        "Cache-Control": "private, max-age=60",
      },
    },
  );
}
