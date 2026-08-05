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
