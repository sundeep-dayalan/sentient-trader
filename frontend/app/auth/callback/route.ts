/**
 * GET /auth/callback
 *
 * This is the OAuth callback URL that Supabase redirects to
 * after a user signs in with GitHub, Google, or Magic Link.
 *
 * It exchanges the temporary auth code for a real session,
 * then redirects the user back to the dashboard.
 *
 * You must register this URL in the Supabase Dashboard:
 *   Authentication → URL Configuration → Redirect URLs
 *   Add: http://localhost:3005/auth/callback (for local dev)
 *   Add: https://your-production-url.com/auth/callback (for prod)
 */

import { NextRequest, NextResponse } from "next/server";
import { createServerClient } from "@supabase/ssr";

export async function GET(request: NextRequest) {
  const { searchParams, origin } = new URL(request.url);

  // The "code" is the temporary OAuth token from the provider
  const code = searchParams.get("code");

  // Optional: where to redirect after login (defaults to home page)
  const next = searchParams.get("next") ?? "/";

  if (code) {
    // Create a Supabase client that can set cookies on the response
    const response = NextResponse.redirect(`${origin}${next}`);

    const supabase = createServerClient(
      process.env.NEXT_PUBLIC_SUPABASE_URL!,
      process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
      {
        cookies: {
          getAll() {
            return request.cookies.getAll();
          },
          setAll(cookiesToSet) {
            cookiesToSet.forEach(({ name, value, options }) => {
              response.cookies.set(name, value, options);
            });
          },
        },
      },
    );

    // Exchange the code for a session — this sets the auth cookie
    const { error } = await supabase.auth.exchangeCodeForSession(code);

    if (!error) {
      return response;
    }
  }

  // If something went wrong, redirect to home with an error param
  return NextResponse.redirect(`${origin}/?auth_error=callback_failed`);
}
