/**
 * Next.js Middleware — Supabase Session Refresh
 *
 * This runs on EVERY request before it reaches the page or API route.
 * Its only job is to refresh the Supabase auth session cookie so it
 * doesn't expire while the user is actively using the app.
 *
 * It does NOT block any routes — all pages are accessible.
 * Auth checks happen in individual API routes using the auth-helpers.
 */

import { createServerClient } from "@supabase/ssr";
import { NextResponse, type NextRequest } from "next/server";

export async function middleware(request: NextRequest) {
  // ── Intercept stray OAuth codes ────────────────────────────────
  // If Supabase falls back to the Site URL (instead of /auth/callback),
  // the ?code= param lands on the root page. Catch it here and forward
  // to the callback route so it gets exchanged for a real session.
  const code = request.nextUrl.searchParams.get("code");
  if (code && !request.nextUrl.pathname.startsWith("/auth/callback")) {
    const callbackUrl = request.nextUrl.clone();
    callbackUrl.pathname = "/auth/callback";
    return NextResponse.redirect(callbackUrl);
  }

  // Start with a "pass-through" response — let the request continue
  let supabaseResponse = NextResponse.next({ request });

  // Create a Supabase client that can read/write cookies on this request
  const supabase = createServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      cookies: {
        getAll() {
          return request.cookies.getAll();
        },
        setAll(cookiesToSet) {
          // Step 1: set cookies on the request (so downstream code sees them)
          cookiesToSet.forEach(({ name, value }) => {
            request.cookies.set(name, value);
          });

          // Step 2: create a new response with the updated request
          supabaseResponse = NextResponse.next({ request });

          // Step 3: set cookies on the response (so the browser saves them)
          cookiesToSet.forEach(({ name, value, options }) => {
            supabaseResponse.cookies.set(name, value, options);
          });
        },
      },
    },
  );

  // This call refreshes the session if it's about to expire.
  // IMPORTANT: do NOT remove this line — without it, sessions will expire.
  await supabase.auth.getUser();

  return supabaseResponse;
}

// Only run middleware on pages and API routes — skip static files
export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp)$).*)",
  ],
};
