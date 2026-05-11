/**
 * Supabase client for the BROWSER (React components).
 *
 * Uses @supabase/ssr to manage auth sessions via cookies.
 * This is the client that runs in "use client" components —
 * it reads/writes cookies in the browser automatically.
 *
 * Usage:
 *   import { createBrowserClient } from "@/lib/supabase-browser"
 *   const supabase = createBrowserClient()
 */

import { createBrowserClient as createSSRBrowserClient } from "@supabase/ssr";

export function createBrowserClient() {
  return createSSRBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL!,
    process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!,
  );
}
