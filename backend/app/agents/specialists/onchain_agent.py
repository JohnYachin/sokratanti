from ..base_agent import BaseAgent, AgentContext

class OnChainSleuthAgent(BaseAgent):
    agent_id = "onchain_sleuth_01"
    name = "On-Chain Sleuth"
    specialization = "Blockchain data, whale tracking, exchange flows, network activity"
    weight = 1.5
    
    def get_system_prompt(self) -> str:
        return """You are an elite blockchain data analyst. You specialize in interpreting on-chain metrics, exchange inflows/outflows, whale wallet movements, and network fundamentals to predict price action.

You must output a JSON object exactly in this format:
{
    "signal": "STRONG_BUY" | "BUY" | "HOLD" | "SELL" | "STRONG_SELL",
    "confidence": 0.0-1.0,
    "reasoning": "Detailed on-chain analysis explanation"
}
"""

    def build_user_prompt(self, context: AgentContext) -> str:
        onchain = context.on_chain_metrics
        prompt = f"""
Asset: {context.symbol}
Current Price: ${context.price:.2f}

On-Chain Metrics:
- Exchange Net Flow (24h): {onchain.get('exchange_net_flow_24h', 'N/A')}
- Active Addresses (24h): {onchain.get('active_addresses_24h', 'N/A')}
- Whale Transaction Count (>100k): {onchain.get('whale_tx_count', 'N/A')}
- MVRV Ratio: {onchain.get('mvrv_ratio', 'N/A')}

Analyze these on-chain fundamentals and determine the appropriate action.
"""
        return prompt
