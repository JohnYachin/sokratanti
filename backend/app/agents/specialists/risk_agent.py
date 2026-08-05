from ..base_agent import BaseAgent, AgentContext

class RiskManagerAgent(BaseAgent):
    agent_id = "risk_manager_01"
    name = "The Skeptic (Risk Manager)"
    specialization = "Downside protection, volatility analysis, stop-loss logic, devil's advocate"
    weight = 1.3
    
    def get_system_prompt(self) -> str:
        return """You are the ultimate skeptic and risk manager. Your sole purpose is to protect capital.
Focus on downside risks, black swan events, over-leverage, and excessive volatility. 
You act as the devil's advocate against euphoric bullishness. You are more likely to signal SELL or HOLD to protect assets.

You must output a JSON object exactly in this format:
{
    "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed risk assessment explanation"
}
"""

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""
Asset: {context.symbol}
Current Price: ${context.price:.2f}
24h Price Change: {context.price_change_24h:.2f}%
24h Volume: ${context.volume_24h:,.2f}
Market Cap: ${context.market_cap:,.2f}

Evaluate the downside risk and potential vulnerabilities in the current market environment for this asset.
"""
        return prompt
