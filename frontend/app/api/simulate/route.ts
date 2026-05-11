/**
 * POST /api/simulate
 * -------------------
 * Injects a single news scenario into the Redis Stream.
 * This is the backend of the "Simulate Market Shock" feature.
 *
 * SECURITY:
 * - Requires authentication (anonymous users get 1/day, social get 2/day)
 * - Rate limited via Upstash Redis
 * - Super users get unlimited (standard abuse prevention only)
 * - Input validation: ticker must be 1-6 uppercase letters, headline max 500 chars
 * - Basic prompt injection blocklist on headline
 *
 * Upstash Redis REST API for XADD:
 *   POST https://<endpoint>
 *   Authorization: Bearer <token>
 *   Body: ["XADD", "<stream>", "*", "field1", "val1", ...]
 */

import { NextRequest, NextResponse } from "next/server";
import { getUser, isAnonymous, isSuperUser } from "@/lib/auth-helpers";
import { checkSimulateLimit, type UserTier } from "@/lib/rate-limit";

// ── Input validation helpers ────────────────────────────────────

/** Only allow 1-6 uppercase letters for ticker symbols */
const TICKER_REGEX = /^[A-Z]{1,6}$/;

/** Maximum headline length (characters) */
const MAX_HEADLINE_LENGTH = 500;

/** Maximum summary length (characters) */
const MAX_SUMMARY_LENGTH = 2000;

/** Maximum source name length (characters) — defense-in-depth */
const MAX_SOURCE_LENGTH = 200;

/** Maximum article URL length (characters) — defense-in-depth */
const MAX_URL_LENGTH = 2048;

/**
 * Basic blocklist for obvious prompt injection attempts.
 * Not a silver bullet, but raises the bar for casual attacks.
 */
const INJECTION_MARKERS = [
  "ignore previous",
  "ignore all previous",
  "ignore above",
  "disregard previous",
  "disregard all",
  "system prompt",
  "you are now",
  "act as",
  "new instructions",
  "override instructions",
];

function containsInjectionMarker(text: string): boolean {
  const lower = text.toLowerCase();
  return INJECTION_MARKERS.some((marker) => lower.includes(marker));
}

export async function POST(req: NextRequest) {
  try {
    // ── Step 1: Check authentication ────────────────────────────
    const user = await getUser();
    if (!user) {
      return NextResponse.json(
        { error: "Authentication required. Please sign in." },
        { status: 401 },
      );
    }

    // ── Step 2: Determine user tier ─────────────────────────────
    let tier: UserTier = "anonymous";
    if (!isAnonymous(user)) {
      tier = isSuperUser(user) ? "super" : "social";
    }

    // ── Step 3: Check rate limit ────────────────────────────────
    const rateLimit = await checkSimulateLimit(user.id, tier);
    if (!rateLimit.success) {
      return NextResponse.json(
        {
          error: rateLimit.errorMessage,
          remaining: rateLimit.remaining,
          resetAt: new Date(rateLimit.reset).toISOString(),
          // Tell the frontend whether the user needs to sign in
          needsAuth: tier === "anonymous",
        },
        { status: 429 },
      );
    }

    // ── Step 4: Parse and validate input ────────────────────────
    const body = await req.json();
    const { ticker, headline, source, summary, article_url } = body as {
      ticker:       string;
      headline:     string;
      source?:      string;
      summary?:     string;
      article_url?: string;
    };

    // Ticker: required, 1-6 uppercase letters
    if (!ticker || !TICKER_REGEX.test(ticker.trim().toUpperCase())) {
      return NextResponse.json(
        { error: "Invalid ticker. Must be 1-6 uppercase letters (e.g., TSLA, AAPL)." },
        { status: 400 },
      );
    }

    // Headline: required, max 500 chars
    if (!headline || headline.trim().length < 5) {
      return NextResponse.json(
        { error: "Headline is required and must be at least 5 characters." },
        { status: 400 },
      );
    }
    if (headline.length > MAX_HEADLINE_LENGTH) {
      return NextResponse.json(
        { error: `Headline must be ${MAX_HEADLINE_LENGTH} characters or fewer.` },
        { status: 400 },
      );
    }

    // Summary: optional, max 2000 chars
    if (summary && summary.length > MAX_SUMMARY_LENGTH) {
      return NextResponse.json(
        { error: `Summary must be ${MAX_SUMMARY_LENGTH} characters or fewer.` },
        { status: 400 },
      );
    }

    // Source: optional, max 200 chars (defense-in-depth: prevent oversized payloads in Redis)
    if (source && source.length > MAX_SOURCE_LENGTH) {
      return NextResponse.json(
        { error: `Source must be ${MAX_SOURCE_LENGTH} characters or fewer.` },
        { status: 400 },
      );
    }

    // Article URL: optional, max 2048 chars
    if (article_url && article_url.length > MAX_URL_LENGTH) {
      return NextResponse.json(
        { error: `Article URL must be ${MAX_URL_LENGTH} characters or fewer.` },
        { status: 400 },
      );
    }

    // Prompt injection check
    if (containsInjectionMarker(headline) || (summary && containsInjectionMarker(summary))) {
      return NextResponse.json(
        { error: "Input contains disallowed phrases. Please rephrase your headline." },
        { status: 400 },
      );
    }

    // ── Step 5: Publish to Redis Stream ─────────────────────────
    const redisUrl   = process.env.UPSTASH_REDIS_URL!;
    const redisToken = process.env.UPSTASH_REDIS_TOKEN!;
    const streamKey  = process.env.REDIS_STREAM_KEY ?? "market-news";

    const sanitizedTicker  = ticker.trim().toUpperCase();
    const sanitizedHeadline = headline.trim();

    const command = [
      "XADD",
      streamKey,
      "*",
      "ticker",        sanitizedTicker,
      "headline",      sanitizedHeadline,
      "source",        source ?? "simulation",
      "published_at",  new Date().toISOString(),
      "is_simulated",  "true",
      ...(summary     ? ["summary",     summary.trim()]     : []),
      ...(article_url ? ["article_url", article_url.trim()] : []),
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
        { status: 502 },
      );
    }

    const result = await response.json();
    return NextResponse.json({
      success: true,
      ticker: sanitizedTicker,
      headline: sanitizedHeadline,
      entry_id: result.result,
      remaining: rateLimit.remaining,
    });

  } catch (err) {
    console.error("Simulate route error:", err);
    return NextResponse.json({ error: "Internal server error" }, { status: 500 });
  }
}
