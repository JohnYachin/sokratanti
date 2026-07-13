"""
Технический анализ: RSI, MACD, Bollinger Bands.
Использует библиотеку `ta` поверх pandas DataFrame.
"""
import logging
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD
from ta.volatility import BollingerBands

logger = logging.getLogger(__name__)


def calculate_indicators(df: pd.DataFrame) -> dict:
    """
    Принимает DataFrame с колонкой 'close'.
    Возвращает словарь с последними значениями индикаторов.
    """
    close = df["close"]

    # RSI (14 периодов)
    rsi_ind = RSIIndicator(close=close, window=14)
    rsi = rsi_ind.rsi().iloc[-1]

    # MACD (12, 26, 9)
    macd_ind = MACD(close=close, window_slow=26, window_fast=12, window_sign=9)
    macd_line = macd_ind.macd().iloc[-1]
    macd_signal_line = macd_ind.macd_signal().iloc[-1]
    macd_diff = macd_ind.macd_diff().iloc[-1]

    # Bollinger Bands (20, 2σ)
    bb_ind = BollingerBands(close=close, window=20, window_dev=2)
    bb_high = bb_ind.bollinger_hband().iloc[-1]
    bb_low = bb_ind.bollinger_lband().iloc[-1]
    bb_pband = bb_ind.bollinger_pband().iloc[-1]  # 0 = нижняя, 1 = верхняя

    return {
        "rsi": float(rsi),
        "macd": float(macd_line),
        "macd_signal": float(macd_signal_line),
        "macd_diff": float(macd_diff),
        "bb_high": float(bb_high),
        "bb_low": float(bb_low),
        "bb_pband": float(bb_pband),
        "price": float(close.iloc[-1]),
    }


# ── Интерпретация ─────────────────────────────────────────────────────────────

def interpret_rsi(rsi: float) -> tuple[str, int]:
    """Возвращает (описание, очки): > 0 = бычий сигнал, < 0 = медвежий."""
    if rsi <= 30:
        return f"RSI {rsi:.1f} — перепродан 🟢 (сигнал покупки)", +2
    elif rsi >= 70:
        return f"RSI {rsi:.1f} — перекуплен 🔴 (сигнал продажи)", -2
    elif rsi <= 45:
        return f"RSI {rsi:.1f} — слабый, склонность к росту", +1
    elif rsi >= 55:
        return f"RSI {rsi:.1f} — сильный, склонность к коррекции", -1
    else:
        return f"RSI {rsi:.1f} — нейтральный", 0


def interpret_macd(diff: float) -> tuple[str, int]:
    if diff > 0:
        return f"MACD бычий (histogram: {diff:+.5f})", +1
    else:
        return f"MACD медвежий (histogram: {diff:+.5f})", -1


def interpret_bb(pband: float) -> tuple[str, int]:
    if pband <= 0.15:
        return "Цена у нижней полосы Боллинджера — возможный отскок", +1
    elif pband >= 0.85:
        return "Цена у верхней полосы Боллинджера — возможный разворот", -1
    else:
        return f"Цена в середине полос Боллинджера (позиция {pband:.2f})", 0
