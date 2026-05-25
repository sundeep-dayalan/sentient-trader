-- =============================================================================
-- LLM Provider Config
-- =============================================================================
--
-- Replaces the old model_override field with a provider-shaped config. Existing
-- deployments keep Groq Always Free behavior until the Settings page switches
-- the provider to OpenRouter.

UPDATE sentient_trader.agent_config
SET
    config = jsonb_set(
        config - 'model_override',
        '{llm_provider}',
        COALESCE(
            config->'llm_provider',
            '{"type":"groq-always-free"}'::jsonb
        ),
        true
    ),
    updated_at = now()
WHERE id = 1;
