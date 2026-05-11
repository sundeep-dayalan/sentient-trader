-- =============================================================================
-- Migration 006: Fix agent_config RLS — Remove dangerous public write policy
-- =============================================================================
-- Migration 005 granted UPDATE to `anon` with no restrictions (USING true).
-- This allowed ANY unauthenticated user to modify the live trading config.
--
-- This migration:
-- 1. DROPS the old "Public update on agent_config" policy
-- 2. Creates a new policy that only allows authenticated users to UPDATE
--    (The API route further restricts this to super users only)
--
-- How to run:
--   Supabase Dashboard → SQL Editor → New Query → paste this → Run
-- =============================================================================

-- Step 1: Remove the dangerous open-write policy from migration 005
DROP POLICY IF EXISTS "Public update on agent_config" ON agent_config;

-- Step 2: Only authenticated (non-anonymous) users can UPDATE agent_config.
-- The Next.js API route further restricts this to super users via code.
CREATE POLICY "Authenticated update on agent_config"
    ON agent_config
    FOR UPDATE
    TO authenticated
    USING (auth.uid() IS NOT NULL)
    WITH CHECK (auth.uid() IS NOT NULL);
