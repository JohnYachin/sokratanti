"""
analysis/trading_rules.py — Торговые правила из классической литературы
и крипто-специфических источников (Investopedia, Binance Academy).

Используется в signals.py для интерпретации индикаторов.
Все числа — конкретные проверенные пороги.
"""

# ── RSI Правила (Wilder, 1978 / Investopedia) ────────────────────────────────
RSI_RULES = {
    # Пороги
    "oversold":   30,   # Традиционная перепроданность → потенциальная покупка
    "overbought": 70,   # Традиционная перекупленность → потенциальная продажа
    "extreme_oversold":   20,  # Экстремальный → выше вероятность разворота
    "extreme_overbought": 80,  # Экстремальный перекуп

    # Зоны в разных рыночных условиях
    "bull_support_zone": (40, 50),  # В бычьем тренде RSI 40–50 = зона поддержки (buy dip)
    "bear_resist_zone":  (50, 60),  # В медвежьем тренде RSI 50–60 = зона сопротивления

    # Подтверждение тренда
    "bull_trend_min": 40,   # В бычьем тренде RSI не опускается ниже 40 при здоровой коррекции
    "midline":        50,   # Пересечение 50 = подтверждение смены тренда
}

# ── MACD Правила (Gerald Appel / Investopedia) ────────────────────────────────
MACD_RULES = {
    # Настройки
    "fast": 12, "slow": 26, "signal": 9,  # Стандарт
    "fast_alt": 8, "slow_alt": 21, "signal_alt": 5,  # Ускоренные для более коротких TF

    # Иерархия силы сигналов (от сильного к слабому)
    # 1. Zero-line crossover (MACD переходит через 0)
    # 2. Дивергенция на ключевом S/R
    # 3. Signal-line crossover (MACD пересекает сигнальную линию)
}

# ── Funding Rate Правила (Binance Academy) ───────────────────────────────────
FUNDING_RULES = {
    # Выплаты каждые 8 часов на Binance Futures
    "interval_hours": 8,

    # Пороги интерпретации (значения в % за 8ч)
    "neutral_max":     0.01,   # -0.01..+0.01% = нейтральный
    "longs_mild":      0.05,   # +0.01..+0.05% = небольшое бычье давление
    "longs_danger":    0.10,   # ≥ +0.10% = лонги перегреты, опасность сквиза
    "longs_extreme":   0.30,   # ≥ +0.30% = экстремальный перегрев
    "shorts_mild":    -0.02,   # -0.02..-0.05% = небольшое медвежье давление
    "shorts_danger":  -0.05,   # ≤ -0.05% = шорты перегреты, риск шорт-сквиза
    "shorts_extreme": -0.10,   # ≤ -0.10% = экстремальный шорт перегрев

    # Стоимость удержания позиции
    # При 0.1%/8ч: 0.3%/день, ~9%/месяц — значительно режет прибыль
    "daily_cost_at_01pct": 0.3,   # % в день
    "monthly_cost_at_01pct": 9.0, # % в месяц

    # Score adjustments (для интеграции в setup_score)
    "score_adj": {
        "extreme_longs":  -15,  # ≥ +0.30% → очень опасно для лонга
        "danger_longs":   -10,  # ≥ +0.10% → опасно
        "mild_longs":      -5,  # +0.05..+0.10% → умеренная осторожность
        "neutral":          0,
        "mild_shorts":     +3,  # -0.02..-0.05% → небольшой позитив
        "danger_shorts":   +8,  # ≤ -0.05% → хороший сетап для лонга
        "extreme_shorts": +12,  # ≤ -0.10% → отличный шорт-сквиз сетап
    },
}

# ── Open Interest Правила (Investopedia) ─────────────────────────────────────
OI_RULES = {
    # Матрица интерпретации OI + цена
    # (price_direction, oi_direction): (interpretation, trade_bias, confidence)
    "matrix": {
        ("up",   "up"):   ("Strong Bull",    "long",  "high"),
        ("up",   "down"): ("Weak Bull",       "caution", "low"),   # short squeeze / profit taking
        ("down", "up"):   ("Strong Bear",    "short", "high"),
        ("down", "down"): ("Bear Exhaustion", "long",  "medium"),  # shorts closing = bottom forming
    },

    # Предупреждение: "hollow rally"
    # Цена растёт + OI падает = сквиз, не реальные покупки → ненадёжен
    "hollow_rally": {"price": "up", "oi": "down", "warning": True},
    "hollow_dump":  {"price": "down", "oi": "down", "warning": True},  # медведи устают
}

# ── Комбинированные стратегии ─────────────────────────────────────────────────
COMBINED_RULES = {
    # Стратегия: Crowded Trade Squeeze
    "squeeze_short": {
        "conditions": [
            "funding_rate >= +0.10%",
            "OI at recent highs",
            "price failing resistance",
            "RSI >= 70",
        ],
        "entry": "Short on break below recent support",
        "stop": "Above recent swing high",
        "target": "2-3% retracement to next support",
    },

    # Стратегия: Trend Conviction Long (самая надёжная)
    "trend_conviction_long": {
        "conditions": [
            "price above key resistance (breakout)",
            "OI increasing simultaneously",
            "volume above 20-period average",
            "MACD line crossing above signal line OR zero line",
            "funding rate normal (0.01–0.05%) — NOT extreme",
        ],
        "entry": "On close of breakout candle",
        "stop": "Below breakout level",
        "min_rr": 2.0,  # минимум R/R 1:2
    },

    # Стратегия: OI Divergence Reversal
    "oi_divergence_short": {
        "conditions": [
            "price makes NEW higher high",
            "OI flat or declining (no confirmation)",
            "RSI bearish divergence",
            "funding rate elevated",
        ],
        "entry": "Short on first bearish candle after new high",
        "stop": "Above the new high",
        "target": "3-5% pullback to first major support",
        "reasoning": "Hollow rally driven by short-covering, not real buyers",
    },
}


def interpret_funding_rate(rate_pct: float) -> tuple[str, int, str]:
    """
    Интерпретирует ставку финансирования.
    
    Args:
        rate_pct: ставка в % (напр. 0.01 = 0.01%)
    
    Returns:
        (signal, score_adj, description_ru)
    """
    rules = FUNDING_RULES["score_adj"]
    thresholds = FUNDING_RULES

    if rate_pct >= thresholds["longs_extreme"]:
        return "extreme_longs",  rules["extreme_longs"], \
               f"🔴 Экстрем! Лонги перегреты {rate_pct:+.3f}% — высокий риск сквиза"
    if rate_pct >= thresholds["longs_danger"]:
        return "danger_longs",   rules["danger_longs"], \
               f"🔴 Лонги перегреты {rate_pct:+.3f}% — опасно для покупки"
    if rate_pct >= thresholds["longs_mild"]:
        return "mild_longs",     rules["mild_longs"], \
               f"🟡 Лонги доминируют {rate_pct:+.3f}% — умеренная осторожность"
    if rate_pct <= thresholds["shorts_extreme"]:
        return "extreme_shorts", rules["extreme_shorts"], \
               f"🟢 Экстрем! Шорты перегреты {rate_pct:+.3f}% — сильный шорт-сквиз риск"
    if rate_pct <= thresholds["shorts_danger"]:
        return "danger_shorts",  rules["danger_shorts"], \
               f"🟢 Шорты перегреты {rate_pct:+.3f}% — хороший момент для лонга"
    if rate_pct <= thresholds["shorts_mild"]:
        return "mild_shorts",    rules["mild_shorts"], \
               f"🟡 Шорты умеренно доминируют {rate_pct:+.3f}%"
    return "neutral", 0, f"⚪ Фандинг нейтральный {rate_pct:+.3f}%"


def interpret_rsi_context(rsi: float, trend: str) -> tuple[str, str]:
    """
    Интерпретирует RSI с учётом рыночного тренда.
    В бычьем тренде 40-50 = buy dip, в медвежьем 50-60 = resistance.
    
    Returns: (signal, description_ru)
    """
    if trend == "bullish":
        if rsi <= RSI_RULES["oversold"]:
            return "strong_buy", f"RSI {rsi:.0f} — перепродан в бычьем тренде → СИЛЬНАЯ ПОКУПКА"
        if RSI_RULES["bull_support_zone"][0] <= rsi <= RSI_RULES["bull_support_zone"][1]:
            return "buy_dip", f"RSI {rsi:.0f} — зона поддержки бычьего тренда → покупка на коррекции"
        if rsi >= RSI_RULES["overbought"]:
            return "take_profit", f"RSI {rsi:.0f} — перекуплен → фиксируй прибыль"
    elif trend == "bearish":
        if rsi >= RSI_RULES["overbought"]:
            return "strong_sell", f"RSI {rsi:.0f} — перекуплен в медвежьем тренде → СИЛЬНАЯ ПРОДАЖА"
        if RSI_RULES["bear_resist_zone"][0] <= rsi <= RSI_RULES["bear_resist_zone"][1]:
            return "sell_bounce", f"RSI {rsi:.0f} — зона сопротивления медвежьего тренда → продажа на отскоке"
        if rsi <= RSI_RULES["oversold"]:
            return "wait_reversal", f"RSI {rsi:.0f} — перепродан, жди разворота (в медвежьем тренде не сразу)"
    else:  # neutral
        if rsi <= RSI_RULES["extreme_oversold"]:
            return "extreme_buy", f"RSI {rsi:.0f} — экстремальная перепроданность"
        if rsi <= RSI_RULES["oversold"]:
            return "buy", f"RSI {rsi:.0f} — перепродан"
        if rsi >= RSI_RULES["extreme_overbought"]:
            return "extreme_sell", f"RSI {rsi:.0f} — экстремальная перекупленность"
        if rsi >= RSI_RULES["overbought"]:
            return "sell", f"RSI {rsi:.0f} — перекуплен"
        if rsi >= RSI_RULES["midline"]:
            return "neutral_bull", f"RSI {rsi:.0f} — нейтрально-бычий"
        return "neutral_bear", f"RSI {rsi:.0f} — нейтрально-медвежий"

    return "neutral", f"RSI {rsi:.0f} — нейтральный"
