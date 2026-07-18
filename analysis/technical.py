"""
analysis/technical.py — Технические индикаторы v2.0

Индикаторы:
  Тренд:     EMA 20, 50, 200
  Momentum:  RSI(14), MACD(12,26,9)
  Волатильность: Bollinger Bands(20,2), ATR(14)
  Тренда сила:   ADX(14)
  Объём:         OBV, относительный объём

Все функции корректно обрабатывают нехватку данных и
возвращают None вместо ошибки при недостаточной истории.
"""
import logging
import pandas as pd
import numpy as np
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.volume import OnBalanceVolumeIndicator

logger = logging.getLogger(__name__)


def calculate_indicators(df: pd.DataFrame) -> dict:
    """
    Принимает DataFrame с колонками [open, high, low, close, volume].
    Возвращает словарь с последними значениями всех индикаторов.
    При нехватке данных — соответствующие поля None.
    """
    if df.empty or len(df) < 20:
        logger.warning("Недостаточно данных для индикаторов: %d свечей", len(df))
        return _empty_indicators(df)

    close = df["close"]
    result: dict = {}

    # ── Цена ────────────────────────────────────────────────────────────────
    result["price"]  = float(close.iloc[-1])
    result["open"]   = float(df["open"].iloc[-1])  if "open"   in df.columns else None
    result["high"]   = float(df["high"].iloc[-1])  if "high"   in df.columns else None
    result["low"]    = float(df["low"].iloc[-1])   if "low"    in df.columns else None
    result["volume"] = float(df["volume"].iloc[-1]) if "volume" in df.columns else None

    # ── RSI(14) ─────────────────────────────────────────────────────────────
    try:
        rsi_ind = RSIIndicator(close=close, window=14)
        result["rsi"] = float(rsi_ind.rsi().iloc[-1])
    except Exception:
        result["rsi"] = None

    # ── MACD(12,26,9) ───────────────────────────────────────────────────────
    try:
        macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
        result["macd"]        = float(macd_ind.macd().iloc[-1])
        result["macd_signal"] = float(macd_ind.macd_signal().iloc[-1])
        result["macd_diff"]   = float(macd_ind.macd_diff().iloc[-1])
    except Exception:
        result["macd"] = result["macd_signal"] = result["macd_diff"] = None

    # ── Bollinger Bands(20,2σ) ──────────────────────────────────────────────
    try:
        bb_ind = BollingerBands(close=close, window=20, window_dev=2)
        result["bb_high"]  = float(bb_ind.bollinger_hband().iloc[-1])
        result["bb_low"]   = float(bb_ind.bollinger_lband().iloc[-1])
        result["bb_mid"]   = float(bb_ind.bollinger_mavg().iloc[-1])
        result["bb_pband"] = float(bb_ind.bollinger_pband().iloc[-1])
        result["bb_width"] = float(bb_ind.bollinger_wband().iloc[-1])
    except Exception:
        result["bb_high"] = result["bb_low"] = result["bb_mid"] = None
        result["bb_pband"] = result["bb_width"] = None

    # ── EMA 20, 50, 200 ─────────────────────────────────────────────────────
    for period in [20, 50, 200]:
        key = f"ema{period}"
        if len(df) >= period:
            try:
                ema = EMAIndicator(close=close, window=period)
                result[key] = float(ema.ema_indicator().iloc[-1])
            except Exception:
                result[key] = None
        else:
            result[key] = None

    # ── ATR(14) — волатильность ──────────────────────────────────────────────
    if "high" in df.columns and "low" in df.columns and len(df) >= 14:
        try:
            atr_ind = AverageTrueRange(
                high=df["high"], low=df["low"], close=close, window=14
            )
            result["atr"] = float(atr_ind.average_true_range().iloc[-1])
            # ATR% — ATR как % от цены
            price = result["price"]
            result["atr_pct"] = round(result["atr"] / price * 100, 2) if price else None
        except Exception:
            result["atr"] = result["atr_pct"] = None
    else:
        result["atr"] = result["atr_pct"] = None

    # ── ADX(14) — сила тренда ───────────────────────────────────────────────
    if "high" in df.columns and "low" in df.columns and len(df) >= 14:
        try:
            adx_ind = ADXIndicator(
                high=df["high"], low=df["low"], close=close, window=14
            )
            result["adx"]    = float(adx_ind.adx().iloc[-1])
            result["adx_pos"] = float(adx_ind.adx_pos().iloc[-1])  # +DI
            result["adx_neg"] = float(adx_ind.adx_neg().iloc[-1])  # -DI
        except Exception:
            result["adx"] = result["adx_pos"] = result["adx_neg"] = None
    else:
        result["adx"] = result["adx_pos"] = result["adx_neg"] = None

    # ── OBV — объёмный тренд ────────────────────────────────────────────────
    if "volume" in df.columns and len(df) >= 5:
        try:
            obv_ind = OnBalanceVolumeIndicator(close=close, volume=df["volume"])
            obv_series = obv_ind.on_balance_volume()
            result["obv"] = float(obv_series.iloc[-1])
            # OBV тренд: последние 10 значений
            if len(obv_series) >= 10:
                obv_recent = obv_series.iloc[-10:]
                result["obv_trend"] = "up" if obv_recent.iloc[-1] > obv_recent.iloc[0] else "down"
            else:
                result["obv_trend"] = None
        except Exception:
            result["obv"] = result["obv_trend"] = None
    else:
        result["obv"] = result["obv_trend"] = None

    # ── Относительный объём (RVOL) ───────────────────────────────────────────
    if "volume" in df.columns and len(df) >= 20:
        try:
            avg_vol = float(df["volume"].iloc[-20:-1].mean())
            cur_vol = float(df["volume"].iloc[-1])
            result["rvol"] = round(cur_vol / avg_vol, 2) if avg_vol > 0 else None
        except Exception:
            result["rvol"] = None
    else:
        result["rvol"] = None

    return result


def _empty_indicators(df: pd.DataFrame) -> dict:
    """Возвращает словарь с None для всех индикаторов."""
    price = float(df["close"].iloc[-1]) if not df.empty else None
    return {
        "price": price, "open": None, "high": None, "low": None, "volume": None,
        "rsi": None, "macd": None, "macd_signal": None, "macd_diff": None,
        "bb_high": None, "bb_low": None, "bb_mid": None, "bb_pband": None, "bb_width": None,
        "ema20": None, "ema50": None, "ema200": None,
        "atr": None, "atr_pct": None,
        "adx": None, "adx_pos": None, "adx_neg": None,
        "obv": None, "obv_trend": None, "rvol": None,
    }


# ── Интерпретация ─────────────────────────────────────────────────────────────

def interpret_rsi(rsi: float | None) -> tuple[str, int]:
    """Возвращает (описание, очки от -2 до +2)."""
    if rsi is None:
        return "RSI: нет данных", 0
    if rsi <= 30:
        return f"RSI {rsi:.1f} — перепродан (сигнал покупки)", +2
    elif rsi >= 70:
        return f"RSI {rsi:.1f} — перекуплен (сигнал продажи)", -2
    elif rsi <= 45:
        return f"RSI {rsi:.1f} — слабый, склонность к росту", +1
    elif rsi >= 55:
        return f"RSI {rsi:.1f} — сильный, склонность к коррекции", -1
    else:
        return f"RSI {rsi:.1f} — нейтральный", 0


def interpret_macd(diff: float | None) -> tuple[str, int]:
    """Возвращает (описание, очки -1/+1)."""
    if diff is None:
        return "MACD: нет данных", 0
    if diff > 0:
        return f"MACD бычий (histogram: {diff:+.5f})", +1
    else:
        return f"MACD медвежий (histogram: {diff:+.5f})", -1


def interpret_bb(pband: float | None) -> tuple[str, int]:
    """Возвращает (описание, очки -1/0/+1)."""
    if pband is None:
        return "BB: нет данных", 0
    if pband <= 0.15:
        return "Цена у нижней полосы Боллинджера — возможный отскок", +1
    elif pband >= 0.85:
        return "Цена у верхней полосы Боллинджера — возможный разворот", -1
    else:
        return f"Цена в середине полос Боллинджера (позиция {pband:.2f})", 0


def get_trend_direction(ind: dict) -> str:
    """
    Определяет направление тренда по EMA.
    Возвращает: 'bullish' | 'bearish' | 'neutral' | 'unknown'
    """
    price = ind.get("price")
    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    ema200 = ind.get("ema200")

    if price is None:
        return "unknown"

    scores = []
    if ema20 is not None:
        scores.append(1 if price > ema20 else -1)
    if ema50 is not None:
        scores.append(1 if price > ema50 else -1)
    if ema200 is not None:
        scores.append(1 if price > ema200 else -1)

    if not scores:
        return "unknown"

    total = sum(scores)
    if total >= 2:
        return "bullish"
    elif total <= -2:
        return "bearish"
    else:
        return "neutral"
