export const SUPABASE_DB_SCHEMA =
  process.env.NEXT_PUBLIC_SUPABASE_DB_SCHEMA || "public";

export const SUPABASE_CLIENT_OPTIONS = {
  db: {
    schema: SUPABASE_DB_SCHEMA,
  },
} as const;
