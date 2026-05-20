/**
 * Supabase client for SERVER-SIDE code (API routes, middleware).
 *
 * Uses @supabase/ssr to manage auth sessions via cookies.
 * In API routes, we pass the request cookies so the server can
 * read the user's session and validate who they are.
 *
 * Usage (in an API route):
 *   import { createServerClient } from "@/lib/supabase-server"
 *   const supabase = createServerClient(request)
 *   const { data: { user } } = await supabase.auth.getUser()
 */

import { createServerClient as createSSRServerClient } from "@supabase/ssr";
import { cookies } from "next/headers";
import { SUPABASE_DB_SCHEMA } from "@/lib/supabase-schema";

/**
 * Creates a Supabase client that can read the current user's session
 * from the request cookies. Works in Next.js API routes and server components.
 */
export async function createServerClient() {
  const cookieStore = await cookies();

  return createSSRServerClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
    {
      db: {
        schema: SUPABASE_DB_SCHEMA,
      },
      cookies: {
        getAll() {
          return cookieStore.getAll();
        },
        setAll(cookiesToSet) {
          try {
            cookiesToSet.forEach(({ name, value, options }) =>
              cookieStore.set(name, value, options),
            );
          } catch {
            // setAll can fail in server components (read-only context).
            // This is expected — the middleware handles cookie writes instead.
          }
        },
      },
    },
  );
}
