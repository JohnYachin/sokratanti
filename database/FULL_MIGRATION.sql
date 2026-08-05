-- ============================================================
-- 001_extensions.sql
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";


-- ============================================================
-- 002_core_tables.sql
-- ============================================================

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


-- ============================================================
-- 003_indexes.sql
-- ============================================================

-- market_snapshots
CREATE INDEX idx_market_snapshots_coin_snapshot ON market_snapshots (coin_id, snapshot_at DESC);
CREATE INDEX idx_market_snapshots_snapshot ON market_snapshots (snapshot_at DESC);

-- technical_indicators
CREATE INDEX idx_technical_indicators_coin_computed ON technical_indicators (coin_id, computed_at DESC);

-- agent_executions
CREATE INDEX idx_agent_executions_cycle_id ON agent_executions (cycle_id);
CREATE INDEX idx_agent_executions_agent_created ON agent_executions (agent_id, created_at DESC);
CREATE INDEX idx_agent_executions_coin_created ON agent_executions (coin_id, created_at DESC);

-- voting_cycles
CREATE INDEX idx_voting_cycles_coin_started ON voting_cycles (coin_id, started_at DESC);
CREATE INDEX idx_voting_cycles_status ON voting_cycles (status);

-- signals
CREATE INDEX idx_signals_coin_created ON signals (coin_id, created_at DESC);
CREATE INDEX idx_signals_active_rank ON signals (is_active, rank_position);
CREATE INDEX idx_signals_created ON signals (created_at DESC);

-- trades
CREATE INDEX idx_trades_portfolio_created ON trades (portfolio_id, created_at DESC);

-- agent_memories (HNSW index)
CREATE INDEX idx_agent_memories_embedding ON agent_memories USING hnsw (embedding vector_cosine_ops);

-- profiles
CREATE INDEX idx_profiles_telegram_user_id ON profiles (telegram_user_id);


-- ============================================================
-- 004_rls.sql
-- ============================================================

-- Enable RLS
ALTER TABLE profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolios ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;
ALTER TABLE coins ENABLE ROW LEVEL SECURITY;
ALTER TABLE market_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE voting_cycles ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;

-- profiles: Users can only read/update their own profile
CREATE POLICY "Users can read own profile" ON profiles
    FOR SELECT USING (auth.uid() = id);

CREATE POLICY "Users can update own profile" ON profiles
    FOR UPDATE USING (auth.uid() = id);

-- portfolios: Users can CRUD their own portfolios
CREATE POLICY "Users can read own portfolios" ON portfolios
    FOR SELECT USING (auth.uid() = user_id);

CREATE POLICY "Users can insert own portfolios" ON portfolios
    FOR INSERT WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update own portfolios" ON portfolios
    FOR UPDATE USING (auth.uid() = user_id);

CREATE POLICY "Users can delete own portfolios" ON portfolios
    FOR DELETE USING (auth.uid() = user_id);

-- trades: Users can only read their own trades
CREATE POLICY "Users can read own trades" ON trades
    FOR SELECT USING (EXISTS (
        SELECT 1 FROM portfolios p WHERE p.id = trades.portfolio_id AND p.user_id = auth.uid()
    ));

-- Publicly readable by authenticated users
CREATE POLICY "Authenticated users can read signals" ON signals FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read coins" ON coins FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read market_snapshots" ON market_snapshots FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read voting_cycles" ON voting_cycles FOR SELECT TO authenticated USING (true);

-- Agents and Executions
CREATE POLICY "Authenticated users can read agents" ON agents FOR SELECT TO authenticated USING (true);
CREATE POLICY "Authenticated users can read agent_executions" ON agent_executions FOR SELECT TO authenticated USING (true);

-- Service role bypasses RLS implicitly or we can define them if needed, but in Supabase service_role bypasses RLS by default.


-- ============================================================
-- 005_seed_agents.sql
-- ============================================================

INSERT INTO agents (name, specialization, system_prompt, weight, model_version) VALUES
('Trend Follower', 'Moving averages & momentum', 'You analyze technical moving averages (EMA20, EMA50, EMA200) and momentum indicators to determine the prevailing trend. Look for golden crosses, death crosses, and trend continuation patterns. You must output valid JSON with signal (STRONG_BUY, BUY, HOLD, SELL, STRONG_SELL), confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('Mean Reversion', 'RSI, Bollinger Bands, overbought/oversold', 'You look for assets that have deviated significantly from their historical averages using RSI, Bollinger Bands, and oscillators. Identify overbought or oversold conditions indicating an imminent reversal. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('On-Chain Sleuth', 'Whale wallet tracking, exchange flows', 'You monitor large wallet transactions, exchange inflows/outflows, and network activity to gauge institutional and whale sentiment. High exchange inflows may indicate selling pressure. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.2, 'gpt-4o'),
('Macro Economist', 'DXY, SPX, interest rates, inflation', 'You analyze macroeconomic data, central bank policies, interest rates, DXY, and global liquidity trends to assess risk-on or risk-off environments. Correlate crypto to broader equity markets. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.1, 'gpt-4o'),
('Social Sentiment Analyst', 'Twitter/Reddit sentiment', 'You scan social media platforms, Twitter, Reddit, and news sentiment for euphoria or panic. Use sentiment scoring to find hype cycles or capitulation bottoms. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 0.8, 'gpt-4o'),
('Volatility Trader', 'Options pricing, VIX equivalents, breakouts', 'You evaluate implied volatility, crypto VIX equivalents, and options open interest to predict explosive breakouts or volatility crush. You favor straddle-like conditions or volatility reversion. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 0.9, 'gpt-4o'),
('DeFi Yield Farmer', 'Staking yields, TVL shifts, LP mechanics', 'You track Total Value Locked (TVL), staking yields, liquidity pool depth, and DeFi protocol mechanics. Capital flows into a protocol indicate growth and demand. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('Security Auditor', 'Smart contract vulnerabilities, centralization', 'You review smart contract audits, developer activity, centralization risks, and past exploits. You veto coins with high centralization or critical bugs. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.5, 'gpt-4o'),
('Regulatory Watchdog', 'Regulatory news, ETF approvals', 'You track SEC filings, global crypto regulations, ETF approvals, and legal battles (e.g., Ripple, Binance). Identify compliance risks and regulatory catalysts. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.3, 'gpt-4o'),
('Tokenomics Expert', 'Token unlocks, emission curves, burns', 'You analyze token unlocks, inflation rates, emission schedules, and burn mechanisms. Massive upcoming unlocks warrant bearish outlooks, while deflationary mechanics are bullish. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.2, 'gpt-4o'),
('Contrarian', 'Counter-trend signals vs. extreme retail consensus', 'You fade the public. When greed is at all-time highs, you sell. When fear is paralyzing, you buy. You actively seek to trade against retail consensus. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 0.9, 'gpt-4o'),
('Order Book Sniper', 'Bid/ask depth, liquidity walls', 'You analyze order book depth, bid/ask spread, and spoofing on major exchanges. You look for thick bid walls as support and ask walls as resistance. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 0.8, 'gpt-4o'),
('Correlations Analyst', 'Cross-asset correlations (BTC vs gold)', 'You evaluate correlations between BTC, ETH, Gold, NASDAQ, and altcoins. You identify decoupling events and beta against Bitcoin. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('Derivatives Expert', 'Perp funding rates, open interest, liquidations', 'You monitor perpetual futures funding rates, open interest, and liquidation levels. High positive funding and rising OI suggest a long squeeze is imminent. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.1, 'gpt-4o'),
('Layer 1 Specialist', 'L1 TPS, active addresses, gas usage', 'You track Layer 1 fundamentals: daily active addresses, transaction fees, TPS, and developer growth on chains like Ethereum, Solana, and Avalanche. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.1, 'gpt-4o'),
('Layer 2 Specialist', 'L2 adoption, sequencer revenue, bridge TVL', 'You focus on Layer 2 rollups (Arbitrum, Optimism, Base), evaluating sequencer revenue, bridge TVL, and transaction cost efficiency. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('NFT/Gaming Analyst', 'Gaming tokens, metaverse, DAU trends', 'You analyze Web3 gaming, NFT volumes, metaverse land sales, and gaming DAU (Daily Active Users) to predict gaming token pumps. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 0.7, 'gpt-4o'),
('Institutional Tracker', 'Spot ETF flows, Grayscale, corporate treasuries', 'You monitor institutional buying, spot ETF inflows/outflows, Grayscale holdings, and public company treasuries (e.g., MicroStrategy). You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.3, 'gpt-4o'),
('Miners/Validators Analyst', 'Hash rate, difficulty, miner reserves', 'You evaluate Bitcoin hash rate, mining difficulty, and miner net position changes. Capitulation by miners often precedes market bottoms. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.0, 'gpt-4o'),
('Risk Manager (The Skeptic)', 'Downside protection, stop enforcement', 'You prioritize capital preservation over gains. You analyze black swan probabilities, portfolio heat, and strict stop-loss enforcement. You easily downgrade signals to HOLD or SELL to protect capital. You must output valid JSON with signal, confidence (0.0 to 1.0), and reasoning.', 1.5, 'gpt-4o');


-- ============================================================
-- coins_top50.sql
-- ============================================================

INSERT INTO coins (coingecko_id, symbol, name, rank) VALUES
('bitcoin', 'BTC', 'Bitcoin', 1),
('ethereum', 'ETH', 'Ethereum', 2),
('tether', 'USDT', 'Tether', 3),
('binancecoin', 'BNB', 'BNB', 4),
('solana', 'SOL', 'Solana', 5),
('ripple', 'XRP', 'XRP', 6),
('usd-coin', 'USDC', 'USDC', 7),
('staked-ether', 'STETH', 'Lido Staked Ether', 8),
('dogecoin', 'DOGE', 'Dogecoin', 9),
('tron', 'TRX', 'TRON', 10),
('cardano', 'ADA', 'Cardano', 11),
('avalanche-2', 'AVAX', 'Avalanche', 12),
('chainlink', 'LINK', 'Chainlink', 13),
('shiba-inu', 'SHIB', 'Shiba Inu', 14),
('polkadot', 'DOT', 'Polkadot', 15),
('bitcoin-cash', 'BCH', 'Bitcoin Cash', 16),
('near', 'NEAR', 'NEAR Protocol', 17),
('uniswap', 'UNI', 'Uniswap', 18),
('litecoin', 'LTC', 'Litecoin', 19),
('matic-network', 'MATIC', 'Polygon', 20),
('internet-computer', 'ICP', 'Internet Computer', 21),
('stellar', 'XLM', 'Stellar', 22),
('filecoin', 'FIL', 'Filecoin', 23),
('ethereum-classic', 'ETC', 'Ethereum Classic', 24),
('monero', 'XMR', 'Monero', 25),
('aave', 'AAVE', 'Aave', 26),
('vechain', 'VET', 'VeChain', 27),
('cosmos', 'ATOM', 'Cosmos Hub', 28),
('algorand', 'ALGO', 'Algorand', 29),
('the-graph', 'GRT', 'The Graph', 30),
('fantom', 'FTM', 'Fantom', 31),
('theta-token', 'THETA', 'Theta Network', 32),
('quant-network', 'QNT', 'Quant', 33),
('elrond-erd-2', 'EGLD', 'MultiversX', 34),
('tezos', 'XTZ', 'Tezos', 35),
('flow', 'FLOW', 'Flow', 36),
('hedera-hashgraph', 'HBAR', 'Hedera', 37),
('axie-infinity', 'AXS', 'Axie Infinity', 38),
('the-sandbox', 'SAND', 'The Sandbox', 39),
('decentraland', 'MANA', 'Decentraland', 40),
('chiliz', 'CHZ', 'Chiliz', 41),
('enjincoin', 'ENJ', 'Enjin Coin', 42),
('basic-attention-token', 'BAT', 'Basic Attention Token', 43),
('1inch', '1INCH', '1inch Network', 44),
('compound', 'COMP', 'Compound', 45),
('maker', 'MKR', 'Maker', 46),
('dash', 'DASH', 'Dash', 47),
('zcash', 'ZEC', 'Zcash', 48),
('neo', 'NEO', 'NEO', 49),
('waves', 'WAVES', 'Waves', 50)
ON CONFLICT (coingecko_id) DO NOTHING;


