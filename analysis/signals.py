"""
analysis/signals.py — Движок торговых сигналов v2.0

Система двухэтапная:
  Этап A — Hard Filters: данные валидны, источники доступны
  Этап B — Setup Score (0-100): качество торговой установки

Статусы сигнала:
  STRONG_SETUP  — score ≥ 75, все фильтры пройдены
  BUY_ZONE      — score ≥ 55, цена вблизи поддержки
  WATCH         — score 40-55, ждём подтверждения
  NO_EDGE       — нет чёткого преимущества
  AVOID         — явные красные флаги

Источники данных:
  - Binance REST (1d + 4h) — основной
  - CoinGecko (1d)         — fallback
"""
import logging
from analysis.technical import (
    calculate_indicators, interpret_rsi, interpret_macd, interpret_bb,
    get_trend_direction,
)

logger = logging.getLogger(__name__)


def _get_ohlcv(coin: str, interval: str = "1d", limit: int = 200):
    """Загружает свечи: Binance → CoinGecko (только 1d)."""
    try:
        from market_data.binance_rest import get_klines
        df = get_klines(coin, interval=interval, limit=limit)
        if not df.empty and len(df) >= 30:
            return df
    except Exception as e:
        logger.warning("Binance %s %s: %s", coin, interval, e)

    if interval == "1d":
        try:
            from data.coingecko import get_ohlcv
            return get_ohlcv(coin, days=90)
        except Exception as e:
            logger.error("CoinGecko fallback: %s", e)
    return None


def _get_news_score(coin: str) -> tuple[float, str, bool]:
    """
    Возвращает (score -1..+1, описание, has_critical_event).
    RSS → CryptoPanic → нейтрально.
    """
    try:
        from news.rss import get_news_for_coin
        from news.normalizer import calc_news_score
        items = get_news_for_coin(coin, limit=15)
        if items:
            score = calc_news_score(items, symbol=coin.upper())
            critical = [i for i in items if i.is_critical]
            if critical:
                note = f"⚠️ {critical[0].title[:55]}…"
                return score, note, True
            note = f"Новости RSS: {len(items)} статей (тональность {score:+.2f})"
            return score, note, False
    except Exception as e:
        logger.warning("RSS error: %s", e)

    try:
        from data.cryptopanic import calc_vote_sentiment
        cp = calc_vote_sentiment(coin)
        score = float(cp.get("score", 0.0))
        if cp.get("bullish_count", 0) + cp.get("bearish_count", 0) > 0:
            note = f"CryptoPanic: +{cp['bullish_count']}👍 -{cp['bearish_count']}👎"
            return score, note, False
    except Exception:
        pass

    return 0.0, "Новости: нет данных", False


def _calc_setup_score(ind_1d: dict, ind_4h: dict | None, fg_score: int,
                      news_raw: float, trend: str) -> int:
    """
    Рассчитывает setup score 0-100.
    Не привязан к линейной формуле — каждый компонент взвешен.
    """
    score = 0

    # Тренд (20 pts)
    if trend == "bullish":
        score += 20
    elif trend == "neutral":
        score += 10

    # RSI качество (15 pts)
    rsi = ind_1d.get("rsi")
    if rsi is not None:
        if 25 <= rsi <= 45:       # перепродан или слабый — хорошо для входа
            score += 15
        elif 45 < rsi <= 55:
            score += 8
        elif rsi < 25:            # экстремально перепродан
            score += 12

    # BB позиция (10 pts)
    pband = ind_1d.get("bb_pband")
    if pband is not None:
        if pband <= 0.20:
            score += 10
        elif pband <= 0.40:
            score += 5

    # MACD (10 pts)
    macd_diff = ind_1d.get("macd_diff")
    if macd_diff is not None and macd_diff > 0:
        score += 10

    # ADX — сила тренда (5 pts)
    adx = ind_1d.get("adx")
    if adx is not None and adx >= 20:
        score += 5

    # 4h подтверждение (15 pts)
    if ind_4h:
        rsi_4h = ind_4h.get("rsi")
        macd_4h = ind_4h.get("macd_diff")
        if rsi_4h is not None and rsi_4h <= 50:
            score += 8
        if macd_4h is not None and macd_4h > 0:
            score += 7

    # Fear & Greed (10 pts)
    if fg_score >= 1:       # страх (хорошо для покупки)
        score += 10
    elif fg_score == 0:
        score += 5

    # Новости (15 pts)
    if news_raw > 0.3:
        score += 15
    elif news_raw > 0.1:
        score += 8
    elif news_raw < -0.3:
        score -= 10

    return max(0, min(100, score))


def _signal_status(setup_score: int, has_critical: bool) -> tuple[str, str, str]:
    """Возвращает (status, emoji, label_ru)."""
    if has_critical:
        return "EVENT_RISK", "🚨", "СОБЫТИЕ-РИСК"
    if setup_score >= 75:
        return "STRONG_SETUP", "🔥", "СИЛЬНЫЙ СЕТАП"
    if setup_score >= 55:
        return "BUY_ZONE", "🟢", "ЗОНА ПОКУПКИ"
    if setup_score >= 40:
        return "WATCH", "👀", "НАБЛЮДЕНИЕ"
    return "NO_EDGE", "🟡", "НЕТ ПРЕИМУЩЕСТВА"


def generate_signal(coin: str, include_sentiment: bool = False) -> dict:
    """
    Генерирует торговый сигнал v2.0 с двухэтапной оценкой.

    Returns dict с ключами:
      signal, emoji, score, setup_score, status, label_ru,
      reasons, indicators, trading_plan, has_4h, sentiment_score
    """
    reasons = []
    ind_1d = {}
    ind_4h = {}

    # ── 1d данные (обязательно) ──────────────────────────────────────────────
    df_1d = _get_ohlcv(coin, interval="1d", limit=200)
    if df_1d is None or df_1d.empty:
        return _no_data_result(coin, "Нет исторических данных (1d)")

    ind_1d = calculate_indicators(df_1d)

    # Hard filter: минимум данных
    if ind_1d.get("rsi") is None:
        return _no_data_result(coin, "Недостаточно данных для расчёта RSI")

    # ── 4h данные (опционально) ──────────────────────────────────────────────
    df_4h = _get_ohlcv(coin, interval="4h", limit=200)
    if df_4h is not None and not df_4h.empty:
        ind_4h = calculate_indicators(df_4h)

    # ── Технический анализ 1d ────────────────────────────────────────────────
    rsi_reason, rsi_score = interpret_rsi(ind_1d["rsi"])
    reasons.append(f"[1d] {rsi_reason}")

    macd_reason, macd_score = interpret_macd(ind_1d.get("macd_diff"))
    reasons.append(f"[1d] {macd_reason}")

    bb_reason, bb_score = interpret_bb(ind_1d.get("bb_pband"))
    reasons.append(f"[1d] {bb_reason}")

    # ── Тренд по EMA ────────────────────────────────────────────────────────
    trend = get_trend_direction(ind_1d)
    trend_labels = {"bullish": "🟢 восходящий", "bearish": "🔴 нисходящий",
                    "neutral": "🟡 боковой", "unknown": "❓ не определён"}
    reasons.append(f"Тренд (EMA): {trend_labels.get(trend, trend)}")

    # ── ADX — сила тренда ────────────────────────────────────────────────────
    adx = ind_1d.get("adx")
    if adx is not None:
        if adx >= 25:
            reasons.append(f"ADX {adx:.1f} — сильный тренд")
        elif adx >= 20:
            reasons.append(f"ADX {adx:.1f} — умеренный тренд")
        else:
            reasons.append(f"ADX {adx:.1f} — тренд слабый (боковик)")

    # ── 4h подтверждение ─────────────────────────────────────────────────────
    if ind_4h:
        rsi_4h = ind_4h.get("rsi")
        macd_4h_diff = ind_4h.get("macd_diff")
        if rsi_4h is not None:
            if rsi_4h <= 40:
                reasons.append(f"[4h] RSI {rsi_4h:.1f} — подтверждает перепроданность")
            elif rsi_4h >= 60:
                reasons.append(f"[4h] RSI {rsi_4h:.1f} — предупреждение (4h перекуплен)")
            else:
                reasons.append(f"[4h] RSI {rsi_4h:.1f} — нейтральный")
        if macd_4h_diff is not None:
            macd_4h_r, _ = interpret_macd(macd_4h_diff)
            reasons.append(f"[4h] {macd_4h_r}")

    # ── Fear & Greed ─────────────────────────────────────────────────────────
    fg_score = 0
    try:
        from data.feargreed import get_fear_greed
        fg = get_fear_greed()
        fg_score = fg["score"]
        reasons.append(f"Fear & Greed: {fg['value']}/100 {fg['label_ru']}")
    except Exception as e:
        logger.warning("Fear & Greed: %s", e)

    # ── Новости ──────────────────────────────────────────────────────────────
    news_raw, news_reason, has_critical = _get_news_score(coin)
    if news_reason:
        reasons.append(news_reason)

    # ── Setup Score ──────────────────────────────────────────────────────────
    setup_score = _calc_setup_score(ind_1d, ind_4h or None, fg_score, news_raw, trend)

    # Hard override: критическое событие
    if has_critical:
        setup_score = max(setup_score - 30, 0)

    status, emoji, label_ru = _signal_status(setup_score, has_critical)

    # Backward compat: старый signal BUY/SELL/HOLD
    old_score = rsi_score + macd_score + bb_score + fg_score
    if old_score >= 2:
        signal = "BUY"
    elif old_score <= -2:
        signal = "SELL"
    else:
        signal = "HOLD"

    # ── Торговый план (только для BUY_ZONE и выше) ──────────────────────────
    trading_plan = None
    if status in ("BUY_ZONE", "STRONG_SETUP", "WATCH"):
        try:
            from analysis.levels import find_swing_points, find_key_levels
            from analysis.risk import calculate_trading_plan

            price = ind_1d.get("price", float(df_1d["close"].iloc[-1]))
            swing = find_swing_points(df_1d)
            supports, resistances = find_key_levels(df_1d)

            trading_plan = calculate_trading_plan(
                price=price,
                indicators=ind_1d,
                supports=supports,
                resistances=resistances,
                swing_low=swing.recent_low,
            )
        except Exception as e:
            logger.error("Trading plan error для %s: %s", coin, e)

    # ── AI Sentiment (опционально) ───────────────────────────────────────────
    sentiment_score = 0.0
    if include_sentiment:
        try:
            from analysis.sentiment import analyze_sentiment
            sent = analyze_sentiment(coin)
            sentiment_score = sent["score"]
            mood = "бычье" if sentiment_score > 0 else "медвежье" if sentiment_score < 0 else "нейтральное"
            reasons.append(f"AI Sentiment: {mood} ({sentiment_score:+.2f})")
        except Exception as e:
            logger.error("Sentiment error: %s", e)

    # Объединяем индикаторы обоих TF
    indicators_combined = {**ind_1d}
    if ind_4h:
        indicators_combined["rsi_4h"] = ind_4h.get("rsi")
        indicators_combined["macd_diff_4h"] = ind_4h.get("macd_diff")

    return {
        "signal":        signal,
        "emoji":         emoji,
        "score":         old_score,
        "setup_score":   setup_score,
        "status":        status,
        "label_ru":      label_ru,
        "reasons":       reasons,
        "indicators":    indicators_combined,
        "trading_plan":  trading_plan,
        "has_4h":        bool(ind_4h),
        "sentiment_score": sentiment_score,
        "trend":         trend,
    }


def _no_data_result(coin: str, reason: str) -> dict:
    return {
        "signal": "HOLD", "emoji": "⚠️",
        "score": 0, "setup_score": 0,
        "status": "DATA_INSUFFICIENT", "label_ru": "НЕТ ДАННЫХ",
        "reasons": [reason],
        "indicators": {}, "trading_plan": None,
        "has_4h": False, "sentiment_score": 0.0, "trend": "unknown",
    }
