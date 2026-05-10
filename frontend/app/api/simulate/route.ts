/**
 * POST /api/simulate
 * -------------------
 * Injects a single news scenario into the Redis Stream via the
 * Upstash REST API. This is the backend of the "Simulate Market Shock" feature.
 *
 * Why Redis Stream instead of Kafka?
 *   Upstash deprecated their Kafka product. Redis Streams provide the same
 *   semantics (persistent ordered log, consumer groups) and we already have
 *   the Redis instance provisioned for the headline cache.
 *
 * Upstash Redis REST API for XADD:
 *   POST https://<endpoint>
 *   Authorization: Bearer <token>
 *   Body: ["XADD", "<stream>", "*", "field1", "val1", "field2", "val2", ...]
 *
 * The agent's Redis Stream consumer receives this message identically to
 * a real message from the ingestion service — the full pipeline fires.
 */

import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const { ticker, headline, source, summary, article_url } = body as {
      ticker:      string;
      headline:    string;
      source?:     string;
      summary?:    string;
      article_url?: string;
    };

    if (!ticker || !headline) {
      return NextResponse.json(
        { error: "ticker and headline are required" },
        { status: 400 }
      );
    }

    const redisUrl   = process.env.UPSTASH_REDIS_URL!;
    const redisToken = process.env.UPSTASH_REDIS_TOKEN!;
    const streamKey  = process.env.REDIS_STREAM_KEY ?? "market-news";

    // XADD command as a JSON array — Upstash REST API format.
    // "*" tells Redis to auto-generate the message ID.
    const command = [
      "XADD",
      streamKey,
      "*",
      "ticker",        ticker,
      "headline",      headline,
      "source",        source ?? "simulation",
      "published_at",  new Date().toISOString(),
      "is_simulated",  "true",
      ...(summary     ? ["summary",     summary]     : []),
      ...(article_url ? ["article_url", article_url] : []),
    ];

    const response = await fetch(redisUrl, {
      method:  "POST",
      headers: {
        "Authorization": `Bearer ${redisToken}`,
        "Content-Type":  "application/json",
      },
      body: JSON.stringify(command),
    });

    if (!response.ok) {
      const text = await response.text();
      console.error("Upstash Redis XADD error:", text);
      return NextResponse.json(
        { error: "Failed to publish to Redis stream" },
        { status: 502 }
      );
    }

    const result = await response.json();
    return NextResponse.json({ success: true, ticker, headline, entry_id: result.result });

  } catch (err) {
    console.error("Simulate route error:", err);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
