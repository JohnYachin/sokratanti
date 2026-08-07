-- ============================================================
-- Phase 8: Price Alerts table
-- ============================================================
CREATE TABLE IF NOT EXISTS price_alerts (
    id           BIGSERIAL PRIMARY KEY,
    user_id      BIGINT NOT NULL,
    coin_symbol  TEXT NOT NULL,
    target_price NUMERIC NOT NULL,
    direction    TEXT NOT NULL DEFAULT 'above',   -- 'above' | 'below'
    is_active    BOOLEAN DEFAULT true,
    triggered_at TIMESTAMPTZ,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_alerts_active
    ON price_alerts(is_active, coin_symbol);

CREATE INDEX IF NOT EXISTS idx_price_alerts_user
    ON price_alerts(user_id, is_active);
