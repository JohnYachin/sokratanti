from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from pydantic import BaseModel

router = APIRouter(prefix="/coins", tags=["coins"])

class CoinBasicInfo(BaseModel):
    id: str
    symbol: str
    name: str
    current_price: float

class CoinDetailInfo(CoinBasicInfo):
    market_cap: float
    volume_24h: float
    indicators: dict
    latest_signal: str

@router.get("", response_model=List[CoinBasicInfo])
async def list_coins(limit: int = Query(100)):
    return []

@router.get("/search", response_model=List[CoinBasicInfo])
async def search_coins(query: str):
    return []

@router.get("/{id}", response_model=CoinDetailInfo)
async def get_coin_detail(id: str):
    raise HTTPException(status_code=404, detail="Coin not found")

@router.get("/{id}/ohlcv")
async def get_coin_ohlcv(id: str, days: int = Query(30)):
    return []
