/**
 * Rate Limiter — Upstash Redis
 *
 * Controls how many times each user can call expensive endpoints
 * like /api/simulate (which costs Groq API credits).
 *
 * LIMITS BY TIER:
 * ─────────────────────────────────
 *  Anonymous user  → 1 simulate per day
 *  Social auth     → 2 simulates per day
 *  Super user      → 60 per minute (standard abuse prevention only)
 *
 * Uses a "fixed window" strategy:
 *  - The window resets once per day (24 hours) for normal users.
 *  - Super users get a rolling 1-minute window instead.
 *
 * Usage:
 *   import { checkSimulateLimit } from "@/lib/rate-limit"
 *   const result = await checkSimulateLimit(userId, tier)
 *   if (!result.success) return NextResponse.json({ error: "..." }, { status: 429 })
 */

import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

// ── Create the Redis client ────────────────────────────────────
// Uses the same Upstash Redis instance you already have.
const redis = new Redis({
  url:   process.env.UPSTASH_REDIS_URL!,
  token: process.env.UPSTASH_REDIS_TOKEN!,
});

// ── Rate limiters for each tier ─────────────────────────────────

/**
 * Anonymous users: 1 request per 24 hours.
 * After this, they see the "sign in to continue" modal.
 */
const anonymousLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.fixedWindow(1, "24 h"),
  prefix: "ratelimit:simulate:anon",
});

/**
 * Social-authed users: 2 requests per 24 hours.
 * After this, they see a "limit reached" message.
 */
const socialLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.fixedWindow(2, "24 h"),
  prefix: "ratelimit:simulate:social",
});

/**
 * Super users: 60 requests per minute.
 * This is just standard abuse prevention — effectively unlimited for normal use.
 */
const superLimiter = new Ratelimit({
  redis,
  limiter: Ratelimit.fixedWindow(60, "1 m"),
  prefix: "ratelimit:simulate:super",
});

// ── Tier type ──────────────────────────────────────────────────
export type UserTier = "anonymous" | "social" | "super";

// ── Result type ────────────────────────────────────────────────
export interface RateLimitResult {
  /** Whether the request is allowed */
  success: boolean;
  /** How many requests remain in the current window */
  remaining: number;
  /** Unix timestamp (ms) when the window resets */
  reset: number;
  /** Human-readable error message (only when success=false) */
  errorMessage?: string;
}

// ── Main function ──────────────────────────────────────────────
/**
 * Check if a user is allowed to call /api/simulate.
 *
 * @param userId - The user's Supabase user ID (used as the rate limit key)
 * @param tier   - "anonymous" | "social" | "super"
 * @returns An object with `success`, `remaining`, `reset`, and optional `errorMessage`
 */
export async function checkSimulateLimit(
  userId: string,
  tier: UserTier,
): Promise<RateLimitResult> {
  // Pick the right limiter based on the user's tier
  const limiter =
    tier === "super"
      ? superLimiter
      : tier === "social"
        ? socialLimiter
        : anonymousLimiter;

  const result = await limiter.limit(userId);

  if (!result.success) {
    // Build a friendly error message based on the tier
    const resetDate = new Date(result.reset);
    const minutesUntilReset = Math.ceil((result.reset - Date.now()) / 60_000);

    let errorMessage: string;
    if (tier === "anonymous") {
      errorMessage =
        "You've used your free simulation! Sign in with GitHub, Google, or Magic Link to get more.";
    } else if (tier === "social") {
      errorMessage =
        `You've reached your daily simulation limit (2/day). Resets in ${minutesUntilReset} minutes (${resetDate.toLocaleTimeString()}).`;
    } else {
      errorMessage =
        `Rate limit exceeded. Please wait a moment and try again.`;
    }

    return {
      success: false,
      remaining: result.remaining,
      reset: result.reset,
      errorMessage,
    };
  }

  return {
    success: true,
    remaining: result.remaining,
    reset: result.reset,
  };
}
