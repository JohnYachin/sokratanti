from ..base_agent import BaseAgent, AgentContext

class SocialSentimentAgent(BaseAgent):
    agent_id = "social_sentiment_01"
    name = "Social Sentiment Analyzer"
    specialization = "News and social media sentiment analysis, crowd psychology"
    weight = 1.0
    
    def get_system_prompt(self) -> str:
        return """You are a behavioral finance expert and social sentiment analysis agent.
Evaluate market psychology, greed/fear indexes, news sentiment, and social media trends to determine if the market is overhyped or overly pessimistic.

You must output a JSON object exactly in this format:
{
    "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed sentiment analysis explanation"
}
"""

    def build_user_prompt(self, context: AgentContext) -> str:
        news_str = "\n".join([f"- {n}" for n in context.recent_news]) if context.recent_news else "No recent news available."
        prompt = f"""
Asset: {context.symbol}
Current Price: ${context.price:.2f}

Recent News & Social Highlights:
{news_str}

Analyze the sentiment and determine the appropriate action.
"""
        return prompt
