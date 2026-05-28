-- Enable limit orders instead of market orders.
-- Limit orders provide better price control and can reduce slippage,
-- especially in volatile or low-liquidity situations.

UPDATE agent_config
SET config = jsonb_set(
    config,
    '{enhanced_trading}',
    COALESCE(config->'enhanced_trading', '{}'::jsonb)
      || '{"use_limit_orders": true}'::jsonb
)
WHERE id = 1;

-- Verify the update
SELECT config->'enhanced_trading'->'use_limit_orders' AS use_limit_orders
FROM agent_config
WHERE id = 1;
