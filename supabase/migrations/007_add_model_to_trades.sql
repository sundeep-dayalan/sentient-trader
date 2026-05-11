-- Add 'model' column to track which LLM performed the final synthesis.
-- This allows the frontend to display which model was used.

ALTER TABLE trades
ADD COLUMN IF NOT EXISTS model text;
