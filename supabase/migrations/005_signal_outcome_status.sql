-- =============================================================================
-- Signal outcome labeling status
-- =============================================================================
-- Records terminal no-bar/error cases so the outcome labeler does not retry
-- weekends, holidays, invalid tickers, or unsupported symbols every run.
--
-- The production schema is sentient_trader. Local development may use
-- sentient_trader_local through SUPABASE_DB_SCHEMA, so this migration updates
-- either schema when the table exists.
-- =============================================================================

DO $$
DECLARE
    target_schema TEXT;
    target_table TEXT;
    constraint_name TEXT;
BEGIN
    FOREACH target_schema IN ARRAY ARRAY['sentient_trader', 'sentient_trader_local']
    LOOP
        target_table := format('%I.signal_outcomes', target_schema);
        constraint_name := target_schema || '_signal_outcomes_label_status_check';

        IF to_regclass(target_table) IS NOT NULL THEN
            EXECUTE format(
                'ALTER TABLE %s
                    ADD COLUMN IF NOT EXISTS label_status TEXT NOT NULL DEFAULT ''LABELED'',
                    ADD COLUMN IF NOT EXISTS label_error TEXT,
                    ADD COLUMN IF NOT EXISTS label_attempts INT4 NOT NULL DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ',
                target_table
            );

            IF NOT EXISTS (
                SELECT 1
                FROM pg_constraint
                WHERE conname IN (constraint_name, 'signal_outcomes_label_status_check')
                  AND conrelid = to_regclass(target_table)
            ) THEN
                EXECUTE format(
                    'ALTER TABLE %s
                        ADD CONSTRAINT %I
                        CHECK (label_status IN (''LABELED'', ''PARTIAL'', ''NO_BARS'', ''ERROR''))',
                    target_table,
                    constraint_name
                );
            END IF;
        END IF;
    END LOOP;
END $$;
