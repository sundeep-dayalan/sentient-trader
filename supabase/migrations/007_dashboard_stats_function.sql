-- =============================================================================
-- Dashboard stats aggregation function
-- =============================================================================
-- The /stats endpoint previously fetched up to 10k trade rows and counted them
-- in Python. PostgREST caps responses at db-max-rows (~1000) and, without an
-- ORDER BY, returns an arbitrary subset — so the dashboard counts jittered on
-- every refresh and were badly understated (e.g. analyzed ~1000 vs 5000+).
--
-- This function computes every dashboard metric in a single atomic aggregation
-- so the result is exact, consistent, and cheap regardless of table size.
--
-- The production schema is sentient_trader. Local/dev may use a sentient_trader%
-- schema through SUPABASE_DB_SCHEMA, so this creates the function in every such
-- schema that currently exists.
-- =============================================================================

DO $$
DECLARE
    target_schema TEXT;
BEGIN
    FOR target_schema IN
        SELECT schema_name
        FROM information_schema.schemata
        WHERE schema_name LIKE 'sentient_trader%'
    LOOP
        -- Only create where a trades table exists in the schema.
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = target_schema AND table_name = 'trades'
        ) THEN
            EXECUTE format(
                $fn$
                CREATE OR REPLACE FUNCTION %I.dashboard_stats()
                RETURNS jsonb
                LANGUAGE sql
                STABLE
                AS $body$
                  SELECT jsonb_build_object(
                    'analyzed', count(*),
                    'executed', count(*) FILTER (
                        WHERE coalesce(nullif(trim(executed_action), ''),
                                       nullif(trim(order_id), '')) IS NOT NULL),
                    'buyOrders', count(*) FILTER (
                        WHERE coalesce(pm_recommendation, trade_action) = 'BUY'),
                    'sellOrders', count(*) FILTER (
                        WHERE coalesce(pm_recommendation, trade_action) = 'SELL'),
                    'riskGated', count(*) FILTER (
                        WHERE (risk_should_trade IS FALSE
                               AND coalesce(nullif(trim(executed_action), ''),
                                            nullif(trim(order_id), '')) IS NULL)
                           OR trade_action = 'HOLD'),
                    'preScreened', count(*) FILTER (WHERE decision_path = 'pre_screen'),
                    'fullDebates', count(*) FILTER (WHERE decision_path = 'full_debate'),
                    'avgSentiment', coalesce(sum(sentiment_score), 0) / nullif(count(*), 0)
                  )
                  FROM %I.trades;
                $body$;
                $fn$,
                target_schema, target_schema
            );

            EXECUTE format(
                'GRANT EXECUTE ON FUNCTION %I.dashboard_stats() TO anon, authenticated, service_role',
                target_schema
            );
        END IF;
    END LOOP;
END $$;
