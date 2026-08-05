import os
import time
import json
import httpx
import asyncio
from typing import Optional, List, Dict, Any
from pydantic import BaseModel
from tenacity import retry, stop_after_attempt, wait_exponential
import redis.asyncio as redis
from supabase import create_client, Client

class CoinData(BaseModel):
    id: str
    symbol: str
    name: str
    current_price: float
    market_cap: float
    total_volume: float
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    price_change_percentage_24h: Optional[float] = None

class OHLCVData(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

class CoinDetail(BaseModel):
    id: str
    symbol: str
    name: str
    description: Dict[str, str]
    links: Dict[str, Any]
    market_data: Dict[str, Any]

class GlobalMarket(BaseModel):
    active_cryptocurrencies: int
    total_market_cap: Dict[str, float]
    total_volume: Dict[str, float]
    market_cap_percentage: Dict[str, float]

class CoinGeckoCollector:
    BASE_URL = 'https://api.coingecko.com/api/v3'
    
    def __init__(self, api_key: str = None, is_pro: bool = False):
        self.api_key = api_key or os.getenv("COINGECKO_API_KEY")
        self.is_pro = is_pro
        self.rate_limit = 500 if is_pro else 50
        self.semaphore = asyncio.Semaphore(self.rate_limit)
        
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        self.redis = redis.from_url(redis_url, decode_responses=True)
        
        supabase_url = os.getenv("SUPABASE_URL", "")
        supabase_key = os.getenv("SUPABASE_KEY", "")
        if supabase_url and supabase_key:
            self.supabase: Client = create_client(supabase_url, supabase_key)
        else:
            self.supabase = None
        
        self.client = httpx.AsyncClient(timeout=30.0)

    def _get_headers(self) -> Dict[str, str]:
        headers = {}
        if self.api_key:
            if self.is_pro:
                headers['x-cg-pro-api-key'] = self.api_key
            else:
                headers['x-cg-demo-api-key'] = self.api_key
        return headers

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
    async def _make_request(self, endpoint: str, params: Dict[str, Any] = None) -> Any:
        async with self.semaphore:
            url = f"{self.BASE_URL}{endpoint}"
            response = await self.client.get(url, params=params, headers=self._get_headers())
            response.raise_for_status()
            return response.json()

    async def get_top_coins(self, limit: int = 200) -> List[CoinData]:
        cache_key = f"cg:top_coins:{limit}"
        cached = await self.redis.get(cache_key)
        if cached:
            return [CoinData(**item) for item in json.loads(cached)]
        
        data = await self._make_request("/coins/markets", params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": limit,
            "page": 1,
            "sparkline": False
        })
        
        coins = [CoinData(**item) for item in data]
        await self.redis.setex(cache_key, 60, json.dumps([c.model_dump() for c in coins]))
        return coins

    async def get_coin_ohlcv(self, coin_id: str, days: int = 30) -> List[OHLCVData]:
        cache_key = f"cg:ohlcv:{coin_id}:{days}"
        cached = await self.redis.get(cache_key)
        if cached:
            return [OHLCVData(**item) for item in json.loads(cached)]
        
        data = await self._make_request(f"/coins/{coin_id}/ohlc", params={
            "vs_currency": "usd",
            "days": days
        })
        
        result = []
        for item in data:
            result.append(OHLCVData(
                timestamp=item[0],
                open=item[1],
                high=item[2],
                low=item[3],
                close=item[4],
                volume=0.0
            ))
            
        await self.redis.setex(cache_key, 300, json.dumps([r.model_dump() for r in result]))
        return result

    async def get_coin_details(self, coin_id: str) -> CoinDetail:
        cache_key = f"cg:details:{coin_id}"
        cached = await self.redis.get(cache_key)
        if cached:
            return CoinDetail(**json.loads(cached))
            
        data = await self._make_request(f"/coins/{coin_id}", params={
            "localization": False,
            "tickers": False,
            "market_data": True,
            "community_data": False,
            "developer_data": False,
            "sparkline": False
        })
        
        detail = CoinDetail(**data)
        await self.redis.setex(cache_key, 600, json.dumps(detail.model_dump()))
        return detail

    async def get_global_market(self) -> GlobalMarket:
        cache_key = "cg:global"
        cached = await self.redis.get(cache_key)
        if cached:
            return GlobalMarket(**json.loads(cached))
            
        data = await self._make_request("/global")
        m_data = data.get("data", {})
        
        global_market = GlobalMarket(
            active_cryptocurrencies=m_data.get("active_cryptocurrencies", 0),
            total_market_cap=m_data.get("total_market_cap", {}),
            total_volume=m_data.get("total_volume", {}),
            market_cap_percentage=m_data.get("market_cap_percentage", {})
        )
        await self.redis.setex(cache_key, 300, json.dumps(global_market.model_dump()))
        return global_market

    async def refresh_market_snapshots(self, coin_ids: List[str]) -> int:
        if not self.supabase:
            return 0
            
        count = 0
        batch_size = 50
        for i in range(0, len(coin_ids), batch_size):
            batch = coin_ids[i:i+batch_size]
            data = await self._make_request("/coins/markets", params={
                "vs_currency": "usd",
                "ids": ",".join(batch)
            })
            
            snapshots = []
            for item in data:
                snapshots.append({
                    "coin_id": item["id"],
                    "symbol": item["symbol"],
                    "price": item["current_price"],
                    "market_cap": item["market_cap"],
                    "volume_24h": item["total_volume"],
                    "timestamp": int(time.time())
                })
            
            if snapshots:
                try:
                    self.supabase.table("market_snapshots").insert(snapshots).execute()
                    count += len(snapshots)
                except Exception as e:
                    print(f"Error saving to Supabase: {e}")
                    
        return count
