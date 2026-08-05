-- Trigger function for updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- profiles table
CREATE TABLE profiles (
    id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    email TEXT NOT NULL,
    username TEXT UNIQUE,
    risk_tolerance TEXT CHECK (risk_tolerance IN ('low','moderate','high','degen')) DEFAULT 'moderate',
    default_currency TEXT DEFAULT 'USD',
    telegram_user_id BIGINT UNIQUE,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TRIGGER update_profiles_updated_at BEFORE UPDATE ON profiles FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- portfolios table
CREATE TABLE portfolios (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID REFERENCES profiles(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    description TEXT,
    total_value NUMERIC(18,8) DEFAULT 0,
    currency TEXT DEFAULT 'USDT',
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TRIGGER update_portfolios_updated_at BEFORE UPDATE ON portfolios FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- coins table
CREATE TABLE coins (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    coingecko_id TEXT UNIQUE NOT NULL,
    symbol TEXT NOT NULL,
    name TEXT NOT NULL,
    rank INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TRIGGER update_coins_updated_at BEFORE UPDATE ON coins FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- market_snapshots table (Partitioned by RANGE)
CREATE TABLE market_snapshots (
    id UUID,
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    price NUMERIC(18,8),
    price_change_24h NUMERIC(10,4),
    volume_24h NUMERIC(24,2),
    market_cap NUMERIC(24,2),
    high_24h NUMERIC(18,8),
    low_24h NUMERIC(18,8),
    snapshot_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, snapshot_at)
) PARTITION BY RANGE (snapshot_at);

-- Create a default partition for market_snapshots
CREATE TABLE market_snapshots_default PARTITION OF market_snapshots DEFAULT;

-- technical_indicators table
CREATE TABLE technical_indicators (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    rsi_14 NUMERIC(8,4),
    macd_line NUMERIC(18,8),
    macd_signal NUMERIC(18,8),
    macd_histogram NUMERIC(18,8),
    bb_upper NUMERIC(18,8),
    bb_middle NUMERIC(18,8),
    bb_lower NUMERIC(18,8),
    ema_20 NUMERIC(18,8),
    ema_50 NUMERIC(18,8),
    ema_200 NUMERIC(18,8),
    volume_sma_20 NUMERIC(24,2),
    obv NUMERIC(24,2),
    computed_at TIMESTAMPTZ NOT NULL
);

-- agents table
CREATE TABLE agents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT UNIQUE NOT NULL,
    specialization TEXT NOT NULL,
    model_version TEXT DEFAULT 'gpt-4o',
    system_prompt TEXT,
    weight NUMERIC(4,3) DEFAULT 1.000,
    is_active BOOLEAN DEFAULT true,
    total_predictions INTEGER DEFAULT 0,
    correct_predictions INTEGER DEFAULT 0,
    brier_score NUMERIC(6,4),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TRIGGER update_agents_updated_at BEFORE UPDATE ON agents FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- voting_cycles table
CREATE TABLE voting_cycles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    agents_responded INTEGER,
    final_signal TEXT,
    final_confidence NUMERIC(4,3),
    consensus_score NUMERIC(4,3),
    result_json JSONB,
    status TEXT DEFAULT 'pending'
);

-- agent_executions table
CREATE TABLE agent_executions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    cycle_id UUID REFERENCES voting_cycles(id) ON DELETE CASCADE,
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    signal TEXT CHECK (signal IN ('STRONG_BUY','BUY','HOLD','SELL','STRONG_SELL')),
    confidence NUMERIC(4,3) CHECK (confidence >= 0 AND confidence <= 1),
    reasoning TEXT,
    raw_output JSONB,
    execution_time_ms INTEGER,
    status TEXT CHECK (status IN ('success','timeout','error')) DEFAULT 'success',
    error_message TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- signals table
CREATE TABLE signals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    cycle_id UUID REFERENCES voting_cycles(id) ON DELETE CASCADE,
    signal TEXT NOT NULL,
    confidence NUMERIC(4,3),
    score NUMERIC(8,4),
    rank_position INTEGER,
    price_at_signal NUMERIC(18,8),
    valid_until TIMESTAMPTZ,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- trades table
CREATE TABLE trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    portfolio_id UUID REFERENCES portfolios(id) ON DELETE CASCADE,
    coin_id UUID REFERENCES coins(id) ON DELETE CASCADE,
    signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    side TEXT CHECK (side IN ('BUY','SELL')),
    quantity NUMERIC(18,8),
    price NUMERIC(18,8),
    total_value NUMERIC(24,8),
    status TEXT CHECK (status IN ('PENDING','EXECUTED','FAILED','CANCELLED')),
    executed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TRIGGER update_trades_updated_at BEFORE UPDATE ON trades FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- agent_memories table
CREATE TABLE agent_memories (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    agent_id UUID REFERENCES agents(id) ON DELETE CASCADE,
    session_id UUID,
    content TEXT NOT NULL,
    embedding vector(1536),
    metadata JSONB DEFAULT '{}'::jsonb,
    importance_score NUMERIC(4,3) DEFAULT 0.5,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- architecture_event_log table
CREATE TABLE architecture_event_log (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    service_source TEXT,
    event_type TEXT,
    payload JSONB,
    status TEXT DEFAULT 'pending',
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
