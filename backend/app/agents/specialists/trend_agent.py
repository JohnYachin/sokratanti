from ..base_agent import BaseAgent, AgentContext

class TrendFollowerAgent(BaseAgent):
    agent_id = "trend_follower_01"
    name = "Trend Follower"
    specialization = "Technical trend analysis, moving averages, momentum"
    weight = 1.2
    
    def get_system_prompt(self) -> str:
        return """You are an expert technical analysis agent specializing in trend following strategies.
Analyze the provided market context focusing on moving averages (EMA 20, 50, 200), momentum indicators, and price action.
Look for crossovers, support/resistance levels, and trend confirmation.

You must output a JSON object exactly in this format:
{
    "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed technical analysis explanation"
}
"""

    def build_user_prompt(self, context: AgentContext) -> str:
        tech = context.technical_indicators
        prompt = f"""
Asset: {context.symbol}
Current Price: ${context.price:.2f}
24h Price Change: {context.price_change_24h:.2f}%
24h Volume: ${context.volume_24h:,.2f}

Technical Indicators:
- EMA 20: {tech.get('ema_20', 'N/A')}
- EMA 50: {tech.get('ema_50', 'N/A')}
- EMA 200: {tech.get('ema_200', 'N/A')}
- RSI (14): {tech.get('rsi_14', 'N/A')}
- MACD: {tech.get('macd', 'N/A')}

Analyze the current trend and determine the appropriate action.
"""
        return prompt
