from typing import List, Dict, Any
from pydantic import BaseModel
from .base_agent import AgentOutput, Signal

class VoteResult(BaseModel):
    final_signal: str
    final_confidence: float
    weighted_score: float
    consensus_score: float
    signal_distribution: Dict[str, int]
    dissenting_agents: List[str]

class VotingEngine:
    async def compute_consensus(self, outputs: List[AgentOutput], agents_meta: Dict[str, Dict[str, Any]]) -> VoteResult:
        weights = {out.agent_id: agents_meta.get(out.agent_id, {}).get("weight", 1.0) for out in outputs}
        
        weighted_score = self._compute_weighted_score(outputs, weights)
        final_signal = self._numeric_to_signal(weighted_score)
        
        final_confidence = min(abs(weighted_score), 1.0)
        consensus_score = self._compute_consensus_score(outputs)
        
        distribution = {s.value: 0 for s in Signal}
        for out in outputs:
            distribution[out.signal.value] += 1
            
        final_numeric = self._signal_to_numeric(final_signal)
        dissenting = []
        for out in outputs:
            num = self._signal_to_numeric(out.signal.value)
            if (num > 0 and final_numeric < 0) or (num < 0 and final_numeric > 0):
                dissenting.append(out.agent_name)
                
        return VoteResult(
            final_signal=final_signal,
            final_confidence=final_confidence,
            weighted_score=weighted_score,
            consensus_score=consensus_score,
            signal_distribution=distribution,
            dissenting_agents=dissenting
        )

    def _signal_to_numeric(self, signal: str) -> float:
        mapping = {
            "STRONG_BUY": 1.0,
            "BUY": 0.5,
            "HOLD": 0.0,
            "SELL": -0.5,
            "STRONG_SELL": -1.0
        }
        return mapping.get(signal, 0.0)

    def _numeric_to_signal(self, score: float) -> str:
        if score >= 0.75:
            return "STRONG_BUY"
        elif score >= 0.25:
            return "BUY"
        elif score > -0.25:
            return "HOLD"
        elif score > -0.75:
            return "SELL"
        else:
            return "STRONG_SELL"

    def _compute_weighted_score(self, outputs: List[AgentOutput], weights: Dict[str, float]) -> float:
        total_score = 0.0
        total_weight = 0.0
        
        for out in outputs:
            w = weights.get(out.agent_id, 1.0)
            s = self._signal_to_numeric(out.signal.value) * out.confidence
            total_score += s * w
            total_weight += w
            
        if total_weight == 0:
            return 0.0
        return total_score / total_weight

    def _compute_consensus_score(self, outputs: List[AgentOutput]) -> float:
        if not outputs:
            return 0.0
            
        signals = [out.signal.value for out in outputs]
        buy_votes = sum(1 for s in signals if s in ("BUY", "STRONG_BUY"))
        sell_votes = sum(1 for s in signals if s in ("SELL", "STRONG_SELL"))
        hold_votes = sum(1 for s in signals if s == "HOLD")
        
        max_votes = max(buy_votes, sell_votes, hold_votes)
        return max_votes / len(outputs)

    def _compute_brier_score(self, predicted_confidence: float, actual_outcome: int) -> float:
        return (predicted_confidence - actual_outcome) ** 2
