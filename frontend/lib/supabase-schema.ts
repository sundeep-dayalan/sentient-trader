export const SUPABASE_DB_SCHEMA =
  import.meta.env.VITE_SUPABASE_DB_SCHEMA || "public";

export const SUPABASE_CLIENT_OPTIONS = {
  db: {
    schema: SUPABASE_DB_SCHEMA,
  },
  auth: {
    flowType: "pkce",
    detectSessionInUrl: true,
    persistSession: true,
    autoRefreshToken: true,
  },
} as const;
