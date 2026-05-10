-- =============================================================================
-- Migration 005: Allow anon writes on agent_config
-- =============================================================================
-- The Settings UI uses the public anon key (no service role key needed).
-- This policy allows the Next.js API route to UPDATE the singleton config row.
--
-- How to run:
--   Supabase Dashboard → SQL Editor → New Query → paste this → Run
-- =============================================================================

CREATE POLICY "Public update on agent_config"
    ON agent_config FOR UPDATE TO anon USING (true) WITH CHECK (true);
