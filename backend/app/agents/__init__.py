from .base_agent import BaseAgent, AgentContext, AgentOutput, Signal
from .council import AICouncil, CouncilResult
from .voting_engine import VotingEngine, VoteResult

# Specialists
from .specialists.trend_agent import TrendFollowerAgent
from .specialists.sentiment_agent import SocialSentimentAgent
from .specialists.onchain_agent import OnChainSleuthAgent
from .specialists.risk_agent import RiskManagerAgent
from .specialists.macro_agent import MacroEconomistAgent

# Remaining 15 thin subclasses to reach 20 agents total
class BreakoutAgent(BaseAgent):
    agent_id = "breakout_01"
    name = "Breakout Specialist"
    specialization = "Volume and price breakouts"
    weight = 1.0
    def get_system_prompt(self): return "You identify explosive breakout setups. Output JSON with signal, confidence, reasoning."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} for breakouts."

class MeanReversionAgent(BaseAgent):
    agent_id = "mean_reversion_01"
    name = "Mean Reversion Agent"
    specialization = "Overbought/oversold conditions"
    weight = 1.0
    def get_system_prompt(self): return "You look for rubber-band effects and mean reversion. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} for mean reversion."

class MomentumAgent(BaseAgent):
    agent_id = "momentum_01"
    name = "Momentum Trader"
    specialization = "Strong directional momentum"
    weight = 1.1
    def get_system_prompt(self): return "You follow the strength of the move. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} momentum."

class ArbitrageAgent(BaseAgent):
    agent_id = "arbitrage_01"
    name = "Arbitrage Spotter"
    specialization = "Cross-exchange inefficiencies"
    weight = 0.8
    def get_system_prompt(self): return "You find arbitrage ops. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} spreads."

class FundamentalsAgent(BaseAgent):
    agent_id = "fundamentals_01"
    name = "Protocol Fundamentalist"
    specialization = "Tokenomics and dev activity"
    weight = 1.0
    def get_system_prompt(self): return "You evaluate intrinsic value. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} fundamentals."

class OptionsAgent(BaseAgent):
    agent_id = "options_01"
    name = "Derivatives Analyst"
    specialization = "Options flow, max pain, funding rates"
    weight = 1.2
    def get_system_prompt(self): return "You analyze derivatives markets. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} derivatives."

class LiquidityAgent(BaseAgent):
    agent_id = "liquidity_01"
    name = "Liquidity Sniper"
    specialization = "Order book depth and slippage"
    weight = 1.0
    def get_system_prompt(self): return "You evaluate order book health. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} liquidity."

class YieldAgent(BaseAgent):
    agent_id = "yield_01"
    name = "Yield Farmer"
    specialization = "Staking APY and DeFi yields"
    weight = 0.9
    def get_system_prompt(self): return "You chase the best yields. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} yield ops."

class WhaleWatcherAgent(BaseAgent):
    agent_id = "whale_watcher_01"
    name = "Whale Watcher"
    specialization = "Large holder movements"
    weight = 1.1
    def get_system_prompt(self): return "You track smart money. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} whale flows."

class RetailSentimentAgent(BaseAgent):
    agent_id = "retail_sentiment_01"
    name = "Retail Herd Tracker"
    specialization = "Retail FOMO/Panic"
    weight = 0.9
    def get_system_prompt(self): return "You analyze dumb money sentiment. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} retail interest."

class RegulatoryAgent(BaseAgent):
    agent_id = "regulatory_01"
    name = "Compliance Officer"
    specialization = "SEC news, ETF approvals, bans"
    weight = 1.2
    def get_system_prompt(self): return "You analyze legal/regulatory risk. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} regulatory threats."

class MarketMakerAgent(BaseAgent):
    agent_id = "market_maker_01"
    name = "Market Maker"
    specialization = "Bid/ask spread capture, volatility"
    weight = 1.0
    def get_system_prompt(self): return "You think like an algorithmic market maker. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} for MM strategies."

class FibonacciAgent(BaseAgent):
    agent_id = "fibonacci_01"
    name = "Fibonacci Wizard"
    specialization = "Fib retracements and extensions"
    weight = 0.9
    def get_system_prompt(self): return "You use Fibonacci levels to find targets. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} with Fibs."

class PatternAgent(BaseAgent):
    agent_id = "pattern_01"
    name = "Chart Pattern Recognizer"
    specialization = "Head & shoulders, flags, triangles"
    weight = 1.0
    def get_system_prompt(self): return "You spot classic chart patterns. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} patterns."

class AIWhispererAgent(BaseAgent):
    agent_id = "ai_whisperer_01"
    name = "AI Meta Agent"
    specialization = "Crypto AI narrative tokens"
    weight = 1.0
    def get_system_prompt(self): return "You specialize in AI token narratives. Output JSON."
    def build_user_prompt(self, ctx): return f"Analyze {ctx.symbol} AI narrative strength."


ALL_AGENTS = [
    TrendFollowerAgent,
    SocialSentimentAgent,
    OnChainSleuthAgent,
    RiskManagerAgent,
    MacroEconomistAgent,
    BreakoutAgent,
    MeanReversionAgent,
    MomentumAgent,
    ArbitrageAgent,
    FundamentalsAgent,
    OptionsAgent,
    LiquidityAgent,
    YieldAgent,
    WhaleWatcherAgent,
    RetailSentimentAgent,
    RegulatoryAgent,
    MarketMakerAgent,
    FibonacciAgent,
    PatternAgent,
    AIWhispererAgent
]

__all__ = [
    "BaseAgent", "AgentContext", "AgentOutput", "Signal",
    "AICouncil", "CouncilResult",
    "VotingEngine", "VoteResult",
    "TrendFollowerAgent", "SocialSentimentAgent", "OnChainSleuthAgent",
    "RiskManagerAgent", "MacroEconomistAgent",
    "ALL_AGENTS"
]
