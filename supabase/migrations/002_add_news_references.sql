-- Store source article references returned by Alpaca News.
ALTER TABLE trades
    ADD COLUMN IF NOT EXISTS article_url TEXT,
    ADD COLUMN IF NOT EXISTS article_source TEXT,
    ADD COLUMN IF NOT EXISTS article_id TEXT;

CREATE INDEX IF NOT EXISTS idx_trades_article_id
    ON trades (article_id)
    WHERE article_id IS NOT NULL;
