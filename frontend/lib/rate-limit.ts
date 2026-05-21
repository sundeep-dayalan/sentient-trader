/**
 * Rate Limiter — Valkey / Redis
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
 * Uses a fixed-window strategy:
 *  - The window resets once per day (24 hours) for normal users.
 *  - Super users get a 1-minute abuse-prevention window instead.
 *
 * Usage:
 *   import { checkSimulateLimit } from "@/lib/rate-limit"
 *   const result = await checkSimulateLimit(userId, tier)
 *   if (!result.success) return NextResponse.json({ error: "..." }, { status: 429 })
 */

import { getRedis } from "@/lib/redis";

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

interface RateLimitConfig {
  limit: number;
  windowMs: number;
  prefix: string;
}

const LIMITS: Record<UserTier, RateLimitConfig> = {
  anonymous: {
    limit: 1,
    windowMs: 24 * 60 * 60 * 1000,
    prefix: "ratelimit:simulate:anon",
  },
  social: {
    limit: 2,
    windowMs: 24 * 60 * 60 * 1000,
    prefix: "ratelimit:simulate:social",
  },
  super: {
    limit: 60,
    windowMs: 60 * 1000,
    prefix: "ratelimit:simulate:super",
  },
};

const FIXED_WINDOW_SCRIPT = `
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("PEXPIRE", KEYS[1], ARGV[1])
end
return current
`;

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
  const config = LIMITS[tier];
  const now = Date.now();
  const windowStart = Math.floor(now / config.windowMs) * config.windowMs;
  const reset = windowStart + config.windowMs;
  const ttlMs = Math.max(reset - now, 1);
  const key = `${config.prefix}:${windowStart}:${userId}`;
  const redis = await getRedis();
  const rawCount = await redis.eval(FIXED_WINDOW_SCRIPT, {
    keys: [key],
    arguments: [String(ttlMs)],
  });
  const count = typeof rawCount === "number" ? rawCount : Number(rawCount);
  const remaining = Math.max(config.limit - count, 0);
  const success = count <= config.limit;

  if (!success) {
    // Build a friendly error message based on the tier
    const resetDate = new Date(reset);
    const minutesUntilReset = Math.ceil((reset - Date.now()) / 60_000);

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
      remaining,
      reset,
      errorMessage,
    };
  }

  return {
    success: true,
    remaining,
    reset,
  };
}
