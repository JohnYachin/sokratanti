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
