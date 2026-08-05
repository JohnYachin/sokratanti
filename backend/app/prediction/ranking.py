from pydantic import BaseModel
from typing import List
import numpy as np

from app.prediction.indicators import IndicatorResult

class CouncilResult(BaseModel):
    approval_rate: float
    average_confidence: float
    sentiment_score: float

class Signal(BaseModel):
    coin_id: str
    symbol: str
    action: str  # BUY, SELL, HOLD
    confidence: float
    target_price: float

class RankedCoin(BaseModel):
    coin_id: str
    symbol: str
    name: str
    rank: int
    composite_score: float
    signal: str
    confidence: float
    price: float

class RankingEngine:
    def __init__(self, db_client):
        self.db = db_client

    def _compute_composite_score(self, indicators: IndicatorResult, council_result: CouncilResult) -> float:
        w_council = 0.4
        w_rsi = 0.2
        w_macd = 0.2
        w_sentiment = 0.2
        
        council_score = council_result.approval_rate * council_result.average_confidence
        
        if 30 <= indicators.rsi <= 70:
            rsi_score = 1.0 - abs(indicators.rsi - 50) / 50
        else:
            rsi_score = 0.0
            
        macd_score = 1.0 if indicators.macd_histogram > 0 else 0.0
        
        sentiment_score = (council_result.sentiment_score + 1) / 2
        
        return (council_score * w_council) + (rsi_score * w_rsi) + (macd_score * w_macd) + (sentiment_score * w_sentiment)

    def _normalize_score(self, raw_scores: List[float]) -> List[float]:
        if not raw_scores:
            return []
        min_val = min(raw_scores)
        max_val = max(raw_scores)
        if max_val == min_val:
            return [0.5 for _ in raw_scores]
        return [(s - min_val) / (max_val - min_val) for s in raw_scores]

    async def rank_coins(self, coin_ids: List[str]) -> List[RankedCoin]:
        raw_results = []
        for cid in coin_ids:
            raw_results.append({
                "coin_id": cid,
                "symbol": cid.upper(),
                "name": cid.capitalize(),
                "score": np.random.random(),
                "price": 100.0,
                "confidence": np.random.random()
            })
            
        raw_results.sort(key=lambda x: x["score"], reverse=True)
        
        ranked = []
        for i, res in enumerate(raw_results):
            ranked.append(RankedCoin(
                coin_id=res["coin_id"],
                symbol=res["symbol"],
                name=res["name"],
                rank=i + 1,
                composite_score=res["score"],
                signal="BUY" if res["score"] > 0.6 else "SELL" if res["score"] < 0.4 else "HOLD",
                confidence=res["confidence"],
                price=res["price"]
            ))
            
        return ranked

    async def get_top_signals(self, limit: int = 10) -> List[Signal]:
        return []
