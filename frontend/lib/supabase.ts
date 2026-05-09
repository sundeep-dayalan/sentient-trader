/**
 * Supabase client factory.
 *
 * We export a factory function (not a singleton) because Next.js App Router
 * renders some components on the server and some on the client — each
 * environment needs its own client instance.
 *
 * Usage:
 *   import { createClient } from "@/lib/supabase"
 *   const supabase = createClient()
 */

import { createClient as createSupabaseClient } from "@supabase/supabase-js";

export function createClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL!;
  const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!;
  return createSupabaseClient(url, key);
}
