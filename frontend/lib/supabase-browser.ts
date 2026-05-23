/**
 * Supabase client for the BROWSER (React components).
 *
 * Uses Supabase's browser client to manage auth sessions in the browser.
 * This is the client that runs in browser components —
 * it reads/writes cookies in the browser automatically.
 *
 * Usage:
 *   import { createBrowserClient } from "@/lib/supabase-browser"
 *   const supabase = createBrowserClient()
 */

import { createClient } from '@supabase/supabase-js';
import { SUPABASE_CLIENT_OPTIONS } from '@/lib/supabase-schema';

let browserClient: ReturnType<typeof createClient> | null = null;

export function createBrowserClient() {
  if (!browserClient) {
    browserClient = createClient(
      import.meta.env.VITE_SUPABASE_URL,
      import.meta.env.VITE_SUPABASE_ANON_KEY,
      SUPABASE_CLIENT_OPTIONS,
    );
  }
  return browserClient;
}
