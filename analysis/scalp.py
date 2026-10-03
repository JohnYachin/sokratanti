"""
analysis/scalp.py — Модуль скальпинга для Binance Futures (плечо x10).

Ориентирован на внутридневную волатильность:
  - Таймфреймы: 5m (основной) + 15m (подтверждение)
  - Индикаторы: RSI(14), EMA(9), EMA(21), Bollinger Bands(20,2), ATR(14)
  - Время в сделке: 10–45 минут
  - Чёткие уровни: Вход по рынку прямо сейчас, узкий стоп 0.8–1.2%, быстрые цели TP1 и TP2
"""
from __future__ import annotations

import logging
import datetime as dt
from typing import Optional

import pandas as pd
import numpy as np

from market_data.binance_futures import get_klines, get_futures_ticker

logger = logging.getLogger(__name__)

SCALP_COINS = ["btc", "eth", "sol", "doge", "bnb"]


def _calc_rsi(series: pd.Series, period: int = 14) -> Optional[float]:
    if len(series) < period + 1:
        return None
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else None


def _calc_ema(series: pd.Series, period: int) -> Optional[float]:
    if len(series) < period:
        return None
    ema = series.ewm(span=period, adjust=False).mean()
    val = ema.iloc[-1]
    return float(val) if not np.isnan(val) else None


def _calc_bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple[float, float, float]:
    if len(series) < period:
        p = float(series.iloc[-1])
        return p, p, p
    sma = series.rolling(period).mean().iloc[-1]
    std = series.rolling(period).std().iloc[-1]
    upper = float(sma + num_std * std)
    lower = float(sma - num_std * std)
    return upper, float(sma), lower


def _calc_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float(df["close"].iloc[-1] * 0.008)
    high = df["high"]
    low = df["low"]
    close = df["close"]
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if not np.isnan(atr) else float(df["close"].iloc[-1] * 0.008)


def analyze_scalp(coin: str) -> dict:
    """
    Анализирует монету для скальпинга на 5m/15m.
    Возвращает торговый план с мгновенным входом по рынку.
    """
    coin = coin.lower()
    ticker = get_futures_ticker(coin)
    cur_price = ticker["last_price"] if ticker["last_price"] > 0 else ticker["mark_price"]

    df_5m = get_klines(coin, interval="5m", limit=60)
    df_15m = get_klines(coin, interval="15m", limit=60)

    if df_5m.empty:
        return {
            "coin": coin,
            "status": "NO_DATA",
            "setup": False,
            "message": f"Нет 5m данных по {coin.upper()}",
            "price": cur_price,
        }

    close_5m = df_5m["close"]
    rsi_5m = _calc_rsi(close_5m, 14) or 50.0
    ema9_5m = _calc_ema(close_5m, 9) or cur_price
    ema21_5m = _calc_ema(close_5m, 21) or cur_price
    bb_upper, bb_mid, bb_lower = _calc_bollinger(close_5m, 20, 2.0)
    atr_5m = _calc_atr(df_5m, 14)

    # 15m тренд
    rsi_15m = 50.0
    ema21_15m = cur_price
    if not df_15m.empty:
        close_15m = df_15m["close"]
        rsi_15m = _calc_rsi(close_15m, 14) or 50.0
        ema21_15m = _calc_ema(close_15m, 21) or cur_price

    trend_15m = "bullish" if cur_price > ema21_15m else "bearish"

    # Оценка LONG скальпа
    long_score = 0
    long_reasons = []

    if rsi_5m <= 32:
        long_score += 35
        long_reasons.append(f"5m RSI перепродан ({rsi_5m:.0f})")
    elif rsi_5m <= 42:
        long_score += 20
        long_reasons.append(f"5m RSI в зоне отката ({rsi_5m:.0f})")

    if cur_price <= bb_lower * 1.003:
        long_score += 25
        long_reasons.append("Касание нижней границы Bollinger")
    elif cur_price < bb_mid:
        long_score += 10

    if ema9_5m > ema21_5m:
        long_score += 20
        long_reasons.append("EMA9 > EMA21 (бычий моментум 5m)")

    if trend_15m == "bullish":
        long_score += 20
        long_reasons.append("15m тренд бычий")

    # Оценка SHORT скальпа
    short_score = 0
    short_reasons = []

    if rsi_5m >= 68:
        short_score += 35
        short_reasons.append(f"5m RSI перекуплен ({rsi_5m:.0f})")
    elif rsi_5m >= 58:
        short_score += 20
        short_reasons.append(f"5m RSI высокий ({rsi_5m:.0f})")

    if cur_price >= bb_upper * 0.997:
        short_score += 25
        short_reasons.append("Касание верхней границы Bollinger")
    elif cur_price > bb_mid:
        short_score += 10

    if ema9_5m < ema21_5m:
        short_score += 20
        short_reasons.append("EMA9 < EMA21 (медвежий моментум 5m)")

    if trend_15m == "bearish":
        short_score += 20
        short_reasons.append("15m тренд медвежий")

    # Определение направления
    if long_score >= 50 and long_score > short_score:
        direction = "LONG"
        score = long_score
        reasons = long_reasons
        stop_dist = max(atr_5m * 1.5, cur_price * 0.008)  # минимум 0.8%
        sl = cur_price - stop_dist
        tp1 = cur_price + stop_dist * 1.5
        tp2 = cur_price + stop_dist * 2.5
        limit_low = cur_price - atr_5m * 0.5
        limit_high = cur_price
    elif short_score >= 50 and short_score > long_score:
        direction = "SHORT"
        score = short_score
        reasons = short_reasons
        stop_dist = max(atr_5m * 1.5, cur_price * 0.008)
        sl = cur_price + stop_dist
        tp1 = cur_price - stop_dist * 1.5
        tp2 = cur_price - stop_dist * 2.5
        limit_low = cur_price
        limit_high = cur_price + atr_5m * 0.5
    else:
        direction = "NEUTRAL"
        score = max(long_score, short_score)
        reasons = ["Флэт на 5m, жди выхода из диапазона"]
        sl = tp1 = tp2 = limit_low = limit_high = cur_price

    # Проценты для плеча x10
    sl_pct = abs(cur_price - sl) / cur_price * 100 if cur_price else 0
    tp1_pct = abs(tp1 - cur_price) / cur_price * 100 if cur_price else 0
    tp2_pct = abs(tp2 - cur_price) / cur_price * 100 if cur_price else 0

    return {
        "coin": coin,
        "price": cur_price,
        "direction": direction,
        "score": score,
        "setup": direction in ("LONG", "SHORT") and score >= 50,
        "market_entry": cur_price,
        "limit_low": limit_low,
        "limit_high": limit_high,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "sl_pct": sl_pct,
        "tp1_pct": tp1_pct,
        "tp2_pct": tp2_pct,
        "sl_10x": sl_pct * 10,
        "tp1_10x": tp1_pct * 10,
        "tp2_10x": tp2_pct * 10,
        "rr": (tp1_pct / sl_pct) if sl_pct > 0 else 0,
        "rsi_5m": rsi_5m,
        "rsi_15m": rsi_15m,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "reasons": reasons,
        "atr_pct": (atr_5m / cur_price * 100) if cur_price else 0,
    }


def scan_all_scalps(coins: list[str] = None) -> list[dict]:
    """Сканирует монеты на наличие скальпинг-точек входа."""
    target_coins = coins or SCALP_COINS
    results = []
    for c in target_coins:
        try:
            res = analyze_scalp(c)
            results.append(res)
        except Exception as e:
            logger.error("scan_all_scalps error %s: %s", c, e)
    # Сортируем: сначала готовые сетапы с максимальным скором
    results.sort(key=lambda x: (1 if x.get("setup") else 0, x.get("score", 0)), reverse=True)
    return results
