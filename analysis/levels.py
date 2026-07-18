"""
analysis/levels.py — Support/Resistance уровни и swing points.

Методы:
  1. Swing High / Low — локальные экстремумы (скользящее окно)
  2. Кластеризация уровней — группируем близкие цены в зоны
  3. Оценка силы зоны — сколько раз цена тестировала уровень

Используется в risk.py для расчёта зон входа, stop-loss и целей.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Минимум свечей для работы
MIN_CANDLES = 30
# Окно для поиска swing points
SWING_WINDOW = 5
# Допуск кластеризации (% от цены)
CLUSTER_TOLERANCE_PCT = 0.02  # 2%


@dataclass
class PriceLevel:
    price: float
    level_type: str     # 'support' | 'resistance' | 'both'
    strength: int       # 1-5 (сколько раз протестирован)
    last_touched: Optional[pd.Timestamp] = None
    touches: list[float] = field(default_factory=list)


@dataclass
class SwingPoints:
    highs: list[tuple[pd.Timestamp, float]]   # [(timestamp, price), ...]
    lows: list[tuple[pd.Timestamp, float]]
    last_high: Optional[float] = None
    last_low: Optional[float] = None
    recent_high: Optional[float] = None  # последний swing high (≤ 20 свечей)
    recent_low: Optional[float] = None   # последний swing low  (≤ 20 свечей)


def find_swing_points(df: pd.DataFrame, window: int = SWING_WINDOW) -> SwingPoints:
    """
    Находит swing highs и swing lows.
    Swing high: high[i] > max(high[i-w:i]) и high[i] > max(high[i+1:i+w+1])
    """
    if len(df) < window * 2 + 1:
        return SwingPoints(highs=[], lows=[])

    highs, lows = [], []
    h = df["high"].values if "high" in df.columns else df["close"].values
    l = df["low"].values  if "low"  in df.columns else df["close"].values
    idx = df.index

    for i in range(window, len(df) - window):
        # Swing high
        if h[i] == max(h[i - window: i + window + 1]):
            highs.append((idx[i], float(h[i])))
        # Swing low
        if l[i] == min(l[i - window: i + window + 1]):
            lows.append((idx[i], float(l[i])))

    last_high = highs[-1][1] if highs else None
    last_low  = lows[-1][1]  if lows  else None

    # Ближайший swing (последние 20 свечей)
    cutoff = df.index[-20] if len(df) >= 20 else df.index[0]
    recent_highs = [p for ts, p in highs if ts >= cutoff]
    recent_lows  = [p for ts, p in lows  if ts >= cutoff]

    return SwingPoints(
        highs=highs,
        lows=lows,
        last_high=last_high,
        last_low=last_low,
        recent_high=max(recent_highs) if recent_highs else last_high,
        recent_low=min(recent_lows)   if recent_lows  else last_low,
    )


def find_key_levels(
    df: pd.DataFrame,
    n_levels: int = 5,
) -> tuple[list[PriceLevel], list[PriceLevel]]:
    """
    Находит ключевые уровни поддержки и сопротивления.

    Returns:
        (supports, resistances) — отсортированы по силе (убывание)
    """
    if len(df) < MIN_CANDLES:
        return [], []

    price_now = float(df["close"].iloc[-1])
    swing = find_swing_points(df)

    # Собираем все уровни из swing points
    candidate_prices: list[float] = []
    for _, p in swing.highs:
        candidate_prices.append(p)
    for _, p in swing.lows:
        candidate_prices.append(p)

    # Добавляем round numbers (психологические уровни)
    # для BTC: 60000, 65000, 70000 и т.д.
    round_step = _get_round_step(price_now)
    lo = price_now * 0.7
    hi = price_now * 1.3
    n = lo
    while n <= hi:
        candidate_prices.append(n)
        n += round_step

    if not candidate_prices:
        return [], []

    # Кластеризуем близкие уровни
    clusters = _cluster_levels(candidate_prices, price_now)

    # Делим на поддержку (ниже цены) и сопротивление (выше)
    supports = []
    resistances = []
    for lvl in clusters:
        if lvl.price < price_now * 0.998:
            lvl.level_type = "support"
            supports.append(lvl)
        elif lvl.price > price_now * 1.002:
            lvl.level_type = "resistance"
            resistances.append(lvl)

    # Сортируем: поддержка — ближайшие к цене сначала, сопротивление — тоже
    supports.sort(key=lambda x: price_now - x.price)
    resistances.sort(key=lambda x: x.price - price_now)

    return supports[:n_levels], resistances[:n_levels]


def _cluster_levels(prices: list[float], ref_price: float) -> list[PriceLevel]:
    """Группирует близкие цены в кластеры."""
    if not prices:
        return []

    sorted_prices = sorted(set(prices))
    clusters: list[list[float]] = []
    current_cluster = [sorted_prices[0]]

    for p in sorted_prices[1:]:
        tolerance = ref_price * CLUSTER_TOLERANCE_PCT
        if p - current_cluster[-1] <= tolerance:
            current_cluster.append(p)
        else:
            clusters.append(current_cluster)
            current_cluster = [p]
    clusters.append(current_cluster)

    result = []
    for cluster in clusters:
        center = float(np.median(cluster))
        strength = min(len(cluster), 5)
        result.append(PriceLevel(
            price=center,
            level_type="unknown",
            strength=strength,
            touches=cluster,
        ))

    return result


def _get_round_step(price: float) -> float:
    """Шаг для психологических уровней исходя из цены."""
    if price >= 10000:
        return 1000.0
    elif price >= 1000:
        return 100.0
    elif price >= 100:
        return 10.0
    elif price >= 10:
        return 1.0
    elif price >= 1:
        return 0.1
    else:
        return 0.01


def nearest_support(supports: list[PriceLevel]) -> Optional[float]:
    """Ближайший уровень поддержки."""
    return supports[0].price if supports else None


def nearest_resistance(resistances: list[PriceLevel]) -> Optional[float]:
    """Ближайший уровень сопротивления."""
    return resistances[0].price if resistances else None
