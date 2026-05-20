-- Run manually after applying migrations 009 and 010 if Supabase still shows
-- old table sizes. Dropping large columns removes future payloads immediately,
-- but PostgreSQL only returns the old on-disk space after a table rewrite.
--
-- This takes an exclusive lock on each table while it runs. With the current
-- table sizes shown in the dashboard, it should be quick, but run it during a
-- quiet window.

VACUUM (FULL, ANALYZE) public.trades;
VACUUM (FULL, ANALYZE) public.trade_decision_traces;

REINDEX TABLE public.trades;
REINDEX TABLE public.trade_decision_traces;
