import { NextRequest, NextResponse } from "next/server";
import { createClient } from "@/lib/supabase";
import { Trade } from "@/lib/types";

const PAGE_SIZE = 20;

export async function GET(req: NextRequest) {
  const before = req.nextUrl.searchParams.get("before");

  const supabase = createClient();

  // Fetch PAGE_SIZE + 1 so we can tell whether another page exists
  // without a separate COUNT query.
  let query = supabase
    .from("trades")
    .select("*")
    .order("created_at", { ascending: false })
    .limit(PAGE_SIZE + 1);

  if (before) {
    query = query.lt("created_at", before);
  }

  const { data, error } = await query;
  if (error) {
    return NextResponse.json({ error: error.message }, { status: 500 });
  }

  const rows = (data ?? []) as Trade[];
  const hasMore = rows.length > PAGE_SIZE;

  return NextResponse.json({
    trades: hasMore ? rows.slice(0, PAGE_SIZE) : rows,
    hasMore,
  });
}
