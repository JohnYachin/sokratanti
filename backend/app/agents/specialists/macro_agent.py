from ..base_agent import BaseAgent, AgentContext

class MacroEconomistAgent(BaseAgent):
    agent_id = "macro_economist_01"
    name = "Macro Economist"
    specialization = "Global macroeconomic factors, fiat liquidity, interest rates, traditional market correlation"
    weight = 1.1
    
    def get_system_prompt(self) -> str:
        return """You are a seasoned macroeconomic analyst. 
You analyze how global economic factors, central bank policies (Fed rates), inflation data, and traditional market correlations impact crypto markets.

You must output a JSON object exactly in this format:
{
    "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed macroeconomic analysis explanation"
}
"""

    def build_user_prompt(self, context: AgentContext) -> str:
        prompt = f"""
Asset: {context.symbol}
Current Price: ${context.price:.2f}

Please consider the broader macroeconomic environment, liquidity conditions, and traditional finance correlations when making your decision for this asset.
"""
        return prompt
