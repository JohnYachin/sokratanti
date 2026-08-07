-- ============================================================
-- Phase 9: Portfolio tracking table
-- ============================================================
CREATE TABLE IF NOT EXISTS portfolio (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    coin_symbol  TEXT NOT NULL,
    action       TEXT NOT NULL,   -- 'buy' | 'sell'
    amount       NUMERIC NOT NULL,
    price        NUMERIC NOT NULL,
    total_usd    NUMERIC GENERATED ALWAYS AS (amount * price) STORED,
    note         TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_portfolio_user
    ON portfolio(user_id, coin_symbol);

-- View: current holdings per user
CREATE OR REPLACE VIEW portfolio_holdings AS
SELECT
    user_id,
    coin_symbol,
    SUM(CASE WHEN action='buy'  THEN amount ELSE -amount END) AS holding,
    SUM(CASE WHEN action='buy'  THEN total_usd ELSE -total_usd END) AS cost_basis,
    COUNT(*) AS trades
FROM portfolio
GROUP BY user_id, coin_symbol
HAVING SUM(CASE WHEN action='buy' THEN amount ELSE -amount END) > 0;
