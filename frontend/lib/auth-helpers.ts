/**
 * Auth helper functions for API routes.
 *
 * These are the "guard" functions that API routes call to check
 * who is making the request and whether they're allowed to do it.
 *
 * THREE-TIER USER MODEL:
 * ──────────────────────
 * 1. Anonymous    — auto-signed-in, can browse + 1 simulate/day
 * 2. Social Auth  — signed in via GitHub/Google/Magic Link, 2 simulates/day
 * 3. Super User   — social auth + email in SUPER_USER_EMAILS env var, unlimited
 *
 * Usage in an API route:
 *   import { getUser, requireAuth, requireSuperUser } from "@/lib/auth-helpers"
 *
 *   // Check who the user is (returns null if not logged in)
 *   const user = await getUser()
 *
 *   // Require any authenticated user (returns 401 if not)
 *   const user = await requireAuth()
 *   if (user instanceof NextResponse) return user  // <-- 401 response
 *
 *   // Require a super user (returns 403 if not)
 *   const user = await requireSuperUser()
 *   if (user instanceof NextResponse) return user  // <-- 403 response
 */

import { NextResponse } from "next/server";
import { createServerClient } from "./supabase-server";
import type { User } from "@supabase/supabase-js";

// ────────────────────────────────────────────────────────────────
// getUser — returns the current user, or null if not logged in
// ────────────────────────────────────────────────────────────────
export async function getUser(): Promise<User | null> {
  try {
    const supabase = await createServerClient();
    const { data: { user } } = await supabase.auth.getUser();
    return user;
  } catch {
    return null;
  }
}

// ────────────────────────────────────────────────────────────────
// isAnonymous — true if the user signed in anonymously
// ────────────────────────────────────────────────────────────────
export function isAnonymous(user: User): boolean {
  return user.is_anonymous === true;
}

// ────────────────────────────────────────────────────────────────
// isSuperUser — true if the user's email is in SUPER_USER_EMAILS
// ────────────────────────────────────────────────────────────────
export function isSuperUser(user: User): boolean {
  const superEmails = process.env.SUPER_USER_EMAILS ?? "";

  // Parse the comma-separated email list, trim whitespace, lowercase
  const allowedEmails = superEmails
    .split(",")
    .map((email) => email.trim().toLowerCase())
    .filter((email) => email.length > 0);

  // If no super users are configured, nobody is a super user
  if (allowedEmails.length === 0) return false;

  const userEmail = user.email?.toLowerCase() ?? "";
  return allowedEmails.includes(userEmail);
}

// ────────────────────────────────────────────────────────────────
// requireAuth — returns the user, or a 401 JSON response
// Use this to protect routes that need ANY logged-in user.
// ────────────────────────────────────────────────────────────────
export async function requireAuth(): Promise<User | NextResponse> {
  const user = await getUser();

  if (!user) {
    return NextResponse.json(
      { error: "Authentication required. Please sign in." },
      { status: 401 },
    );
  }

  return user;
}

// ────────────────────────────────────────────────────────────────
// requireNonAnonymous — returns a social-authed user, or 403
// Use this for routes that anonymous users should NOT access.
// ────────────────────────────────────────────────────────────────
export async function requireNonAnonymous(): Promise<User | NextResponse> {
  const user = await getUser();

  if (!user) {
    return NextResponse.json(
      { error: "Authentication required. Please sign in." },
      { status: 401 },
    );
  }

  if (isAnonymous(user)) {
    return NextResponse.json(
      { error: "Please sign in with GitHub, Google, or Magic Link to access this feature." },
      { status: 403 },
    );
  }

  return user;
}

// ────────────────────────────────────────────────────────────────
// requireSuperUser — returns a super user, or 403
// Use this for admin-only routes like editing agent config.
// ────────────────────────────────────────────────────────────────
export async function requireSuperUser(): Promise<User | NextResponse> {
  const user = await getUser();

  if (!user) {
    return NextResponse.json(
      { error: "Authentication required. Please sign in." },
      { status: 401 },
    );
  }

  if (isAnonymous(user)) {
    return NextResponse.json(
      { error: "Please sign in to access this feature." },
      { status: 403 },
    );
  }

  if (!isSuperUser(user)) {
    return NextResponse.json(
      { error: "Admin access required. Your account does not have permission to modify this." },
      { status: 403 },
    );
  }

  return user;
}
