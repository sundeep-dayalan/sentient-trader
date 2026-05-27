-- Enable the price-move gate feature in agent_config.
-- This prevents the agent from chasing trades where the stock has
-- already moved significantly since the headline was first analyzed.
--
-- max_price_move_pct = 0.03 means block if price moved > 3% since
-- the initial fetch_context snapshot.

UPDATE agent_config
SET config = jsonb_set(
    config,
    '{enhanced_trading}',
    COALESCE(config->'enhanced_trading', '{}'::jsonb)
      || '{"price_move_gate": true, "max_price_move_pct": 0.03}'::jsonb
)
WHERE id = 1;

-- Verify the update
SELECT config->'enhanced_trading'->'price_move_gate' AS price_move_gate,
       config->'enhanced_trading'->'max_price_move_pct' AS max_price_move_pct
FROM agent_config
WHERE id = 1;
