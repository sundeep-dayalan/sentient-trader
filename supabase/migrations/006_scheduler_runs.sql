-- =============================================================================
-- Generic scheduler activity tracking
-- =============================================================================
-- Records scheduler run activity only. Runtime env still controls whether and
-- when each scheduler runs.
--
-- The production schema is sentient_trader. Local development may use
-- sentient_trader_local through SUPABASE_DB_SCHEMA, so this migration creates
-- the table in either schema when the schema exists.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
DECLARE
    target_schema TEXT;
BEGIN
    FOREACH target_schema IN ARRAY ARRAY['sentient_trader', 'sentient_trader_local']
    LOOP
        IF EXISTS (
            SELECT 1
            FROM information_schema.schemata
            WHERE schema_name = target_schema
        ) THEN
            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS %I.scheduler_runs (
                    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
                    scheduler_name   TEXT        NOT NULL,
                    status           TEXT        NOT NULL DEFAULT ''RUNNING''
                                     CHECK (status IN (''RUNNING'', ''SUCCESS'', ''ERROR'')),
                    started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    finished_at      TIMESTAMPTZ,
                    duration_ms      INT4,
                    rows_processed   INT4,
                    worker_name      TEXT,
                    error_message    TEXT,
                    metadata         JSONB       NOT NULL DEFAULT ''{}''::jsonb,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
                )',
                target_schema
            );

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I
                    ON %I.scheduler_runs (scheduler_name, started_at DESC)',
                'idx_' || target_schema || '_scheduler_runs_name_started',
                target_schema
            );

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I
                    ON %I.scheduler_runs (status, started_at DESC)',
                'idx_' || target_schema || '_scheduler_runs_status_started',
                target_schema
            );

            EXECUTE format(
                'ALTER TABLE %I.scheduler_runs ENABLE ROW LEVEL SECURITY',
                target_schema
            );

            EXECUTE format(
                'DROP POLICY IF EXISTS "Authenticated read access on scheduler_runs" ON %I.scheduler_runs',
                target_schema
            );

            EXECUTE format(
                'CREATE POLICY "Authenticated read access on scheduler_runs"
                    ON %I.scheduler_runs
                    FOR SELECT
                    TO authenticated
                    USING (true)',
                target_schema
            );

            EXECUTE format(
                'GRANT SELECT ON %I.scheduler_runs TO authenticated',
                target_schema
            );

            EXECUTE format(
                'GRANT ALL ON %I.scheduler_runs TO service_role',
                target_schema
            );
        END IF;
    END LOOP;
END $$;
