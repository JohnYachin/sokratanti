import pandas as pd
import numpy as np
import ta
from pydantic import BaseModel
from typing import Tuple, List, Optional
from app.data.collectors.coingecko import CoinGeckoCollector

class IndicatorResult(BaseModel):
    coin_id: str
    symbol: str
    rsi: float
    macd_line: float
    macd_signal: float
    macd_histogram: float
    bb_upper: float
    bb_mid: float
    bb_lower: float
    ema_20: float
    ema_50: float
    obv: float
    current_price: float

class TechnicalIndicatorsEngine:
    def __init__(self, cg_collector: CoinGeckoCollector):
        self.cg = cg_collector

    async def _fetch_ohlcv(self, coin_id: str, days: int = 30) -> pd.DataFrame:
        data = await self.cg.get_coin_ohlcv(coin_id, days=days)
        if not data:
            return pd.DataFrame()
        
        df = pd.DataFrame([d.model_dump() for d in data])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
        df.set_index('timestamp', inplace=True)
        return df

    def _compute_rsi(self, closes: pd.Series, period: int = 14) -> float:
        if len(closes) < period:
            return 50.0
        try:
            rsi = ta.momentum.RSIIndicator(closes, window=period)
            return float(rsi.rsi().iloc[-1])
        except:
            return 50.0

    def _compute_macd(self, closes: pd.Series) -> Tuple[float, float, float]:
        if len(closes) < 26:
            return (0.0, 0.0, 0.0)
        try:
            macd = ta.trend.MACD(closes)
            return (
                float(macd.macd().iloc[-1]),
                float(macd.macd_signal().iloc[-1]),
                float(macd.macd_diff().iloc[-1])
            )
        except:
            return (0.0, 0.0, 0.0)

    def _compute_bollinger_bands(self, closes: pd.Series, period: int = 20) -> Tuple[float, float, float]:
        if len(closes) < period:
            return (0.0, 0.0, 0.0)
        try:
            bb = ta.volatility.BollingerBands(closes, window=period)
            return (
                float(bb.bollinger_hband().iloc[-1]),
                float(bb.bollinger_mavg().iloc[-1]),
                float(bb.bollinger_lband().iloc[-1])
            )
        except:
            return (0.0, 0.0, 0.0)

    def _compute_ema(self, closes: pd.Series, period: int) -> float:
        if len(closes) < period:
            return float(closes.iloc[-1]) if not closes.empty else 0.0
        try:
            ema = ta.trend.EMAIndicator(closes, window=period)
            return float(ema.ema_indicator().iloc[-1])
        except:
            return float(closes.iloc[-1])

    def _compute_obv(self, closes: pd.Series, volumes: pd.Series) -> float:
        if len(closes) < 2 or volumes.sum() == 0:
            return 0.0
        try:
            obv = ta.volume.OnBalanceVolumeIndicator(closes, volumes)
            return float(obv.on_balance_volume().iloc[-1])
        except:
            return 0.0

    async def compute_for_coin(self, coin_id: str, coin_symbol: str) -> IndicatorResult:
        df = await self._fetch_ohlcv(coin_id, days=60)
        if df.empty:
            raise ValueError(f"No OHLCV data for {coin_id}")
            
        closes = df['close']
        volumes = df['volume']
        current_price = float(closes.iloc[-1])
        
        rsi = self._compute_rsi(closes)
        macd_line, macd_signal, macd_hist = self._compute_macd(closes)
        bb_up, bb_mid, bb_low = self._compute_bollinger_bands(closes)
        ema_20 = self._compute_ema(closes, 20)
        ema_50 = self._compute_ema(closes, 50)
        obv = self._compute_obv(closes, volumes)
        
        return IndicatorResult(
            coin_id=coin_id,
            symbol=coin_symbol,
            rsi=rsi,
            macd_line=macd_line,
            macd_signal=macd_signal,
            macd_histogram=macd_hist,
            bb_upper=bb_up,
            bb_mid=bb_mid,
            bb_lower=bb_low,
            ema_20=ema_20,
            ema_50=ema_50,
            obv=obv,
            current_price=current_price
        )

    async def save_indicators(self, result: IndicatorResult) -> None:
        if self.cg.supabase:
            try:
                self.cg.supabase.table("indicators").insert(result.model_dump()).execute()
            except Exception as e:
                print(f"Error saving indicators: {e}")
