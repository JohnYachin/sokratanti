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
  - Binance FUTURES (fapi.binance.com) — основной (те же свечи что в Futures Trading)
  - Binance Spot (api.binance.com)     — fallback если монета не на фьючерсах
  - CoinGecko (1d)                     — последний fallback
"""
import logging
from analysis.technical import (
    calculate_indicators, interpret_rsi, interpret_macd, interpret_bb,
    get_trend_direction,
)

logger = logging.getLogger(__name__)


def _get_ohlcv(coin: str, interval: str = "1d", limit: int = 200):
    """
    Загружает свечи:
      1. Binance FUTURES (fapi) — основной, те же свечи что в Binance Futures
      2. Binance Spot            — fallback если монета не на фьючерсах
      3. CoinGecko (только 1d)  — последний резерв
    """
    # ── Приоритет 1: Futures klines (fapi.binance.com) ────────────────────────
    try:
        from market_data.binance_futures import get_klines as fut_klines
        df = fut_klines(coin, interval=interval, limit=limit)
        if not df.empty and len(df) >= 30:
            return df
    except Exception as e:
        logger.debug("Futures klines %s %s: %s", coin, interval, e)

    # ── Приоритет 2: Spot klines (api.binance.com) ────────────────────────────
    try:
        from market_data.binance_rest import get_klines as spot_klines
        df = spot_klines(coin, interval=interval, limit=limit)
        if not df.empty and len(df) >= 30:
            logger.debug("Using Spot klines for %s %s (Futures unavailable)", coin, interval)
            return df
    except Exception as e:
        logger.warning("Spot klines %s %s: %s", coin, interval, e)

    # CoinGecko убран — только Binance данные
    logger.warning("Нет данных OHLCV для %s %s — Binance недоступен", coin, interval)
    import pandas as pd
    return pd.DataFrame()


def _get_news_score(coin: str) -> tuple[float, str, bool]:
    """
    Возвращает (score -1..+1, описание, has_critical_event).
    Только RSS — без CryptoPanic.
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

    return 0.0, "Новости: нет данных", False


def _calc_short_score(ind_1d: dict, ind_4h: dict | None,
                      fg_score: int, news_raw: float, trend: str) -> int:
    """
    Рассчитывает медвежий SHORT score 0-100.
    Зеркало _calc_setup_score — ищет условия для SHORT позиции.
    """
    score = 0

    # Медвежий тренд (20 pts)
    if trend == "bearish":
        score += 20
    elif trend == "neutral":
        score += 8

    # RSI перекупленность (15 pts)
    rsi = ind_1d.get("rsi")
    if rsi is not None:
        if 70 <= rsi <= 80:
            score += 15  # перекуплен — шорт
        elif 80 < rsi:
            score += 12  # экстремально перекуплен
        elif 60 <= rsi < 70:
            score += 6

    # BB верхняя полоса (10 pts)
    pband = ind_1d.get("bb_pband")
    if pband is not None:
        if pband >= 0.85:
            score += 10
        elif pband >= 0.70:
            score += 5

    # MACD отрицательный (10 pts)
    macd_diff = ind_1d.get("macd_diff")
    if macd_diff is not None and macd_diff < 0:
        score += 10

    # ADX — сильный тренд (5 pts)
    adx = ind_1d.get("adx")
    if adx is not None and adx >= 20:
        score += 5

    # Цена ниже EMA — подтверждение медвежьего (8 pts)
    price  = ind_1d.get("price")
    ema20  = ind_1d.get("ema20")
    ema50  = ind_1d.get("ema50")
    ema200 = ind_1d.get("ema200")
    if price and ema20 and ema50 and price < ema20 and ema20 < ema50:
        score += 5
    if price and ema200 and price < ema200:
        score += 3

    # 4h подтверждение (15 pts)
    if ind_4h:
        rsi_4h   = ind_4h.get("rsi")
        macd_4h  = ind_4h.get("macd_diff")
        if rsi_4h is not None and rsi_4h >= 60:
            score += 8
        if macd_4h is not None and macd_4h < 0:
            score += 7

    # Fear & Greed: жадность → шорт (10 pts)
    if fg_score <= -1:   # extreme greed
        score += 10
    elif fg_score == 0:
        score += 4

    # Негативные новости (12 pts)
    if news_raw < -0.3:
        score += 12
    elif news_raw < -0.1:
        score += 6
    elif news_raw > 0.3:
        score -= 8

    # Объём: RVOL (10 pts) — высокий объём подтверждает продажу
    rvol = ind_1d.get("rvol")
    if rvol is not None:
        if rvol >= 2.0:     # высокий объём на падении — подтверждает шорт
            score += 10
        elif rvol >= 1.5:
            score += 5
        elif rvol < 0.7:    # объём уходит — возможно нет согласия
            score -= 4
    # OBV trend для шорта: OBV падает = подтверждение
    obv_trend = ind_1d.get("obv_trend")
    if obv_trend == "down" and trend == "bearish":
        score += 3

    return max(0, min(100, score))


def _calc_setup_score(ind_1d: dict, ind_4h: dict | None, fg_score: int,

                      news_raw: float, trend: str,
                      learned_weights: dict | None = None) -> int:
    """
    Рассчитывает setup score 0-100.
    Если learned_weights переданы (из optimizer.get_learned_weights),
    применяет поправки к базовым весам каждого индикатора.

    Поправка = ±7 баллов в зависимости от исторической надёжности индикатора.
    """
    score = 0
    w = learned_weights or {}

    # Тренд (20 pts)
    if trend == "bullish":
        score += 20
    elif trend == "neutral":
        score += 10

    # RSI качество (15 pts + learned adj)
    rsi = ind_1d.get("rsi")
    if rsi is not None:
        if 25 <= rsi <= 45:
            base = 15
            adj = w.get("rsi_oversold", 0.0) if rsi <= 35 else w.get("rsi_moderate", 0.0)
            score += max(0, base + adj)
        elif 45 < rsi <= 55:
            score += 8
        elif rsi < 25:
            score += 12

    # BB позиция (10 pts + learned adj)
    pband = ind_1d.get("bb_pband")
    if pband is not None:
        if pband <= 0.20:
            score += max(0, 10 + w.get("bb_low", 0.0))
        elif pband <= 0.40:
            score += 5

    # MACD (10 pts + learned adj)
    macd_diff = ind_1d.get("macd_diff")
    if macd_diff is not None and macd_diff > 0:
        score += max(0, 10 + w.get("macd_bull", 0.0))

    # ADX — сила тренда (5 pts + learned adj)
    adx = ind_1d.get("adx")
    if adx is not None and adx >= 20:
        score += max(0, 5 + w.get("adx_trending", 0.0))

    # EMA alignment bonus (learned adj)
    price = ind_1d.get("price")
    ema20 = ind_1d.get("ema20")
    ema50 = ind_1d.get("ema50")
    ema200 = ind_1d.get("ema200")
    if price and ema20 and ema50 and price > ema20 and ema20 > ema50:
        score += max(0, 5 + w.get("ema_bull", 0.0))
    if price and ema200 and price > ema200:
        score += max(0, 3 + w.get("ema_above_200", 0.0))

    # 4h подтверждение (15 pts)
    if ind_4h:
        rsi_4h = ind_4h.get("rsi")
        macd_4h = ind_4h.get("macd_diff")
        if rsi_4h is not None and rsi_4h <= 50:
            score += 8
        if macd_4h is not None and macd_4h > 0:
            score += 7

    # Fear & Greed (10 pts)
    if fg_score >= 1:
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

    # Объём: RVOL (10 pts) — подтверждение сигнала
    rvol = ind_1d.get("rvol")
    if rvol is not None:
        if rvol >= 2.0:     # объём вдвое выше среднего — сильный сигнал
            score += 10
        elif rvol >= 1.5:   # +50% от среднего
            score += 6
        elif rvol < 0.7:    # объём уходит — слабый сигнал
            score -= 5
    # OBV trend bonus
    obv_trend = ind_1d.get("obv_trend")
    if obv_trend == "up" and trend == "bullish":
        score += 3

    return max(0, min(100, score))



def _signal_status(setup_score: int, has_critical: bool,
                   short_score: int = 0) -> tuple[str, str, str, str]:
    """
    Возвращает (status, emoji, label_ru, direction).
    direction: 'LONG' | 'SHORT' | 'NEUTRAL'
    Анализирует как бычьи, так и медвежьи сетапы.
    """
    if has_critical:
        return "EVENT_RISK", "🚨", "СОБЫТИЕ-РИСК", "NEUTRAL"

    # SHORT побеждает если медвежий скор сильнее бычьего
    if short_score >= 75 and short_score > setup_score:
        return "STRONG_SHORT", "🔴", "СИЛЬНЫЙ ШОРТ", "SHORT"
    if short_score >= 55 and short_score > setup_score:
        return "SHORT_ZONE",   "🟠", "ЗОНА ШОРТА",   "SHORT"

    # LONG
    if setup_score >= 75:
        return "STRONG_SETUP", "🔥", "СИЛЬНЫЙ СЕТАП", "LONG"
    if setup_score >= 55:
        return "BUY_ZONE",     "🟢", "ЗОНА ПОКУПКИ",  "LONG"
    if setup_score >= 40:
        return "WATCH",        "👀", "НАБЛЮДЕНИЕ",    "NEUTRAL"
    return "NO_EDGE", "🟡", "НЕТ ПРЕИМУЩЕСТВА", "NEUTRAL"



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
    ind_1h = {}

    # ── 1d данные (обязательно) ──────────────────────────────────────────────
    df_1d = _get_ohlcv(coin, interval="1d", limit=200)
    if df_1d is None or df_1d.empty:
        return _no_data_result(coin, "Нет исторических данных (1d)")

    ind_1d = calculate_indicators(df_1d)

    if ind_1d.get("rsi") is None:
        return _no_data_result(coin, "Недостаточно данных для расчёта RSI")

    # ── 4h данные (подтверждение тренда) ─────────────────────────────────────
    df_4h = _get_ohlcv(coin, interval="4h", limit=200)
    if df_4h is not None and not df_4h.empty:
        ind_4h = calculate_indicators(df_4h)

    # ── 1h данные (тайминг входа) ─────────────────────────────────────────────
    df_1h = _get_ohlcv(coin, interval="1h", limit=100)
    if df_1h is not None and not df_1h.empty:
        ind_1h = calculate_indicators(df_1h)

    # ── 30m и 5m данные (краткосрочный тайминг / скальпинг) ──────────────────
    ind_30m: dict = {}
    ind_5m:  dict = {}
    try:
        df_30m = _get_ohlcv(coin, interval="30m", limit=80)
        if df_30m is not None and not df_30m.empty:
            ind_30m = calculate_indicators(df_30m)
    except Exception as e:
        logger.debug("30m data error for %s: %s", coin, e)
    try:
        df_5m = _get_ohlcv(coin, interval="5m", limit=60)
        if df_5m is not None and not df_5m.empty:
            ind_5m = calculate_indicators(df_5m)
    except Exception as e:
        logger.debug("5m data error for %s: %s", coin, e)

    # ── Тренд по EMA (определяем первым — нужен для RSI-интерпретации) ────────
    trend = get_trend_direction(ind_1d)
    trend_labels = {"bullish": "🟢 восходящий", "bearish": "🔴 нисходящий",
                    "neutral": "🟡 боковой", "unknown": "❓ не определён"}
    reasons.append(f"Тренд (EMA): {trend_labels.get(trend, trend)}")

    # ── Технический анализ 1d — с учётом тренда ──────────────────────────────
    # RSI интерпретируется в контексте тренда (bull support zone / bear resistance)
    try:
        from analysis.trading_rules import interpret_rsi_context
        rsi_sig, rsi_desc = interpret_rsi_context(ind_1d["rsi"], trend)
        reasons.append(f"[1d] {rsi_desc}")
    except Exception:
        rsi_reason, _ = interpret_rsi(ind_1d["rsi"])
        reasons.append(f"[1d] {rsi_reason}")

    rsi_reason, rsi_score = interpret_rsi(ind_1d["rsi"])  # backward compat for score

    macd_reason, macd_score = interpret_macd(ind_1d.get("macd_diff"))
    # Дополнительно: MACD zero-line crossover — самый сильный сигнал
    macd_val = ind_1d.get("macd")
    if macd_val is not None:
        if macd_val > 0:
            reasons.append("[1d] MACD выше нуля — бычья зона")
        else:
            reasons.append("[1d] MACD ниже нуля — медвежья зона")
    reasons.append(f"[1d] {macd_reason}")

    # Setup Score
    learned_weights = None
    try:
        from analysis.optimizer import get_learned_weights
        learned_weights = get_learned_weights(coin)
    except Exception as e:
        logger.debug("get_learned_weights failed: %s", e)

    setup_score = _calc_setup_score(
        ind_1d, ind_4h or None, 0, 0, trend,
        learned_weights=learned_weights,
    )
    short_score = _calc_short_score(
        ind_1d, ind_4h or None, 0, 0, trend,
    )

     # ── 1h тайминг ───────────────────────────────────────────────────────────────────────────
    entry_timing = "neutral"  # "now" / "wait" / "neutral" / "overbought" / "short_now"
    timing_reason = ""
    if ind_1h:
        rsi_1h  = ind_1h.get("rsi")
        macd_1h = ind_1h.get("macd_diff")
        bb_1h   = ind_1h.get("bb_pband")
        rvol_1h = ind_1h.get("rvol")

        # Определяем текущий сетап чтобы знать LONG/SHORT приоритет
        current_setup = "SHORT" if (short_score > setup_score and short_score >= 55) else "LONG"

        if rsi_1h is not None:
            if current_setup == "SHORT":
                # Для шорта: ждём перекупленности 1h
                if rsi_1h >= 65:
                    entry_timing = "now"
                    timing_reason = f"1h RSI {rsi_1h:.0f} — перекуплен, вход в шорт"
                elif rsi_1h <= 40:
                    entry_timing = "oversold"  # для шорта перепродан = ждём отката
                    timing_reason = f"1h RSI {rsi_1h:.0f} — перепродан, подожди отката вверх"
                elif macd_1h is not None and macd_1h < 0 and (bb_1h or 0.5) > 0.5:
                    entry_timing = "now"
                    timing_reason = f"1h MACD медвежий + BB верхняя половина — хороший момент для шорта"
                else:
                    entry_timing = "wait"
                    timing_reason = f"1h RSI {rsi_1h:.0f} — ждём перекупа для шорта"
            else:
                # Для лонга: ждём перепроданности 1h
                if rsi_1h <= 38:
                    entry_timing = "now"
                    timing_reason = f"1h RSI {rsi_1h:.0f} — перепродан, можно входить"
                elif rsi_1h >= 65:
                    entry_timing = "overbought"
                    timing_reason = f"1h RSI {rsi_1h:.0f} — перекуплен, подожди отката"
                elif macd_1h is not None and macd_1h > 0 and (bb_1h or 0.5) < 0.5:
                    entry_timing = "now"
                    timing_reason = f"1h MACD бычий + BB нижняя половина — хороший момент"
                else:
                    entry_timing = "wait"
                    timing_reason = f"1h RSI {rsi_1h:.0f} — нейтрально, нет идеального входа"

        # Volume spike на 1h — дополнительное подтверждение
        vol_note = ""
        if rvol_1h is not None and rvol_1h >= 2.0:
            vol_note = f" | СПАЙК объёма RVOL={rvol_1h:.1f}x — движение подтверждено"
        elif rvol_1h is not None and rvol_1h >= 1.5:
            vol_note = f" | RVOL={rvol_1h:.1f}x"
        reasons.append((f"[1h] {timing_reason}{vol_note}") if timing_reason else "[1h] нет данных")

    bb_reason, bb_score = interpret_bb(ind_1d.get("bb_pband"))
    reasons.append(f"[1d] {bb_reason}")

    adx = ind_1d.get("adx")
    if adx is not None:
        if adx >= 25:
            reasons.append(f"ADX {adx:.1f} — сильный тренд")
        elif adx >= 20:
            reasons.append(f"ADX {adx:.1f} — умеренный тренд")
        else:
            reasons.append(f"ADX {adx:.1f} — тренд слабый (боковик)")

    # ── Свечные паттерны (Нисон) ────────────────────────────────────────────
    try:
        from analysis.technical import detect_candle_patterns, detect_rsi_divergence, detect_bb_squeeze
        from ta.momentum import RSIIndicator

        candle_patterns = detect_candle_patterns(df_1d)
        for pat in candle_patterns:
            reasons.append(f"🕯️ {pat['name']}: {pat['description']}")

        # RSI дивергенция (самый надёжный сигнал по Элдеру)
        if len(df_1d) >= 14:
            rsi_series = RSIIndicator(close=df_1d["close"], window=14).rsi()
            divergence = detect_rsi_divergence(df_1d, rsi_series)
            if divergence["bullish"]:
                reasons.insert(0, divergence["description"])  # в начало — важнейший сигнал
            elif divergence["bearish"]:
                reasons.insert(0, divergence["description"])

        # BB Squeeze
        bb_squeeze = detect_bb_squeeze(df_1d, ind_1d)
        if bb_squeeze["squeeze"] or bb_squeeze["expanding"]:
            reasons.append(bb_squeeze["description"])

    except Exception as _pe:
        logger.debug("Pattern detection skip: %s", _pe)

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

    # ── 1h тайминг ───────────────────────────────────────────────────────────
    entry_timing = "neutral"  # "now" / "wait" / "neutral" / "overbought"
    timing_reason = ""
    if ind_1h:
        rsi_1h = ind_1h.get("rsi")
        macd_1h = ind_1h.get("macd_diff")
        bb_1h   = ind_1h.get("bb_pband")
        if rsi_1h is not None:
            if rsi_1h <= 38:
                entry_timing = "now"
                timing_reason = f"1h RSI {rsi_1h:.0f} — перепродан, можно входить"
            elif rsi_1h >= 65:
                entry_timing = "overbought"
                timing_reason = f"1h RSI {rsi_1h:.0f} — перекуплен, подожди отката"
            elif macd_1h is not None and macd_1h > 0 and (bb_1h or 0.5) < 0.5:
                entry_timing = "now"
                timing_reason = f"1h MACD бычий + BB нижняя половина — хороший момент"
            else:
                entry_timing = "wait"
                timing_reason = f"1h RSI {rsi_1h:.0f} — нейтрально, нет идеального входа"
        reasons.append(f"[1h] {timing_reason}" if timing_reason else "[1h] нет данных")

    # ── Fear & Greed ─────────────────────────────────────────────────────────
    fg_score = 0
    fg_label = ""
    try:
        from data.feargreed import get_fear_greed
        fg = get_fear_greed()
        fg_score = fg["score"]
        fg_label = fg["label_ru"]
        reasons.append(f"Fear & Greed: {fg['value']}/100 {fg_label}")
    except Exception as e:
        logger.warning("Fear & Greed: %s", e)

    # ── Binance Futures: фандинг + OI ────────────────────────────────────────
    futures_ctx = {}
    futures_score_adj = 0
    try:
        from market_data.binance_futures import get_futures_context
        futures_ctx = get_futures_context(coin)
        futures_score_adj = futures_ctx.get("signal_score_adj", 0)
        for note in futures_ctx.get("notes", []):
            reasons.append(f"[Futures] {note}")
    except Exception as e:
        logger.debug("Futures context: %s", e)

    # ── Новости ──────────────────────────────────────────────────────────────
    news_raw, news_reason, has_critical = _get_news_score(coin)
    if news_reason:
        reasons.append(news_reason)

    # ── Setup Score ───────────────────────────────────────────────────────────
    learned_weights = None
    try:
        from analysis.optimizer import get_learned_weights
        learned_weights = get_learned_weights(coin)
    except Exception as e:
        logger.debug("get_learned_weights failed: %s", e)

    setup_score = _calc_setup_score(
        ind_1d, ind_4h or None, fg_score, news_raw, trend,
        learned_weights=learned_weights,
    )

    # Явный медвежий SHORT score
    short_score = _calc_short_score(
        ind_1d, ind_4h or None, fg_score, news_raw, trend,
    )

    # Применяем поправку фьючерсного рынка
    setup_score = max(0, min(100, setup_score + futures_score_adj))
    short_score = max(0, min(100, short_score - futures_score_adj))  # фандинг уменьшает short

    if has_critical:
        setup_score = max(setup_score - 30, 0)
        short_score = max(short_score - 30, 0)

    # ── Персональная история (Phase 18) ──────────────────────────────────────
    personal_wr = None
    try:
        from analysis.trade_analyzer import get_coin_personal_score, _BEST_HOURS_CACHE
        import datetime
        personal = get_coin_personal_score(coin)
        if personal["score_adj"] != 0:
            setup_score = max(0, min(100, setup_score + personal["score_adj"]))
            note = personal.get("note", "")
            if note:
                reasons.append(note)
        personal_wr = personal.get("personal_wr")
        # Поправка по лучшему часу UTC
        current_hour = datetime.datetime.utcnow().hour
        best_hours = _BEST_HOURS_CACHE.get("hours", [])
        if best_hours and current_hour in best_hours:
            setup_score = min(100, setup_score + 8)
            reasons.append(f"⏰ Твоё лучшее время входа ({current_hour:02d}:00 UTC) — +8 к скору")
    except Exception as _pe:
        logger.debug("Personal score: %s", _pe)

    status, emoji, label_ru, direction = _signal_status(setup_score, has_critical, short_score)

    # ── Определяем ACTION (главный вывод) ────────────────────────────────────
    # Это то что пользователь видит в первую очередь
    if status == "EVENT_RISK":
        action      = "НЕ ВХОДИТЬ"
        action_icon = "🚨"
        action_desc = "Критическое событие — высокий риск"
    # ── SHORT actions ─────────────────────────────────────────────────────────────────
    elif status == "STRONG_SHORT":
        action      = "ШОРТИТЬ СЕЙЧАС"
        action_icon = "🔴"
        action_desc = "Сильный медвежий сетап — открывай SHORT"
    elif status == "SHORT_ZONE":
        action      = "ГОТОВИТЬСЯ К ШОРТУ"
        action_icon = "🟠"
        action_desc = "Медвежь подтверждён — жди 1h сигнала"
    # ── LONG actions ─────────────────────────────────────────────────────────────────
    elif status == "STRONG_SETUP" and entry_timing == "now":
        action      = "ПОКУПАТЬ СЕЙЧАС"
        action_icon = "🟢"
        action_desc = "Сильный сетап + 1h готов"
    elif status == "STRONG_SETUP" and entry_timing in ("wait", "neutral"):
        action      = "ГОТОВИТЬСЯ К ЛОНГУ"
        action_icon = "🟡"
        action_desc = "Сетап сильный, жди 1h сигнала"
    elif status == "STRONG_SETUP" and entry_timing == "overbought":
        action      = "ЖДАТЬ ОТКАТА"
        action_icon = "🟡"
        action_desc = "Сильный сетап, но 1h перекуплен"
    elif status == "BUY_ZONE" and entry_timing == "now":
        action      = "МОЖНО ЛОНГОВАТЬ"
        action_icon = "🟢"
        action_desc = "Зона покупки + 1h подтверждает"
    elif status == "BUY_ZONE":
        action      = "НАБЛЮДАТЬ"
        action_icon = "👀"
        action_desc = "Зона покупки — жди 1h сигнала"
    elif trend == "bearish" and setup_score < 35:
        action      = "НЕ ВХОДИТЬ"
        action_icon = "🔴"
        action_desc = "Нисходящий тренд, нет сетапа"
    else:
        action      = "НЕТ СИГНАЛА"
        action_icon = "⚪"
        action_desc = "Нейтральный рынок — наблюдаем"

    # ── Backward compat ──────────────────────────────────────────────────────
    old_score = rsi_score + macd_score + bb_score + fg_score
    signal = "BUY" if old_score >= 2 else ("SELL" if old_score <= -2 else "HOLD")

    # ── Торговый план LONG — на 4h данных (ближе к реальным уровням) ─────
    trading_plan = None
    short_plan   = None
    _plan_df = df_4h if (df_4h is not None and not df_4h.empty) else df_1d
    _plan_ind = ind_4h if ind_4h else ind_1d

    if status in ("BUY_ZONE", "STRONG_SETUP", "WATCH"):
        try:
            from analysis.levels import find_swing_points, find_key_levels
            from analysis.risk import calculate_trading_plan

            price    = _plan_ind.get("price") or float(_plan_df["close"].iloc[-1])
            swing    = find_swing_points(_plan_df)
            supports, resistances = find_key_levels(_plan_df)

            trading_plan = calculate_trading_plan(
                price=price,
                indicators=_plan_ind,
                supports=supports,
                resistances=resistances,
                swing_low=swing.recent_low,
            )
        except Exception as e:
            logger.error("Trading plan (LONG) error для %s: %s", coin, e)

        # ATR fallback — если swing points не нашлись, считаем от ATR
        if trading_plan is None or not trading_plan.setup_valid:
            try:
                from analysis.risk import calculate_trading_plan
                price = _plan_ind.get("price") or float(_plan_df["close"].iloc[-1])
                atr   = _plan_ind.get("atr") or price * 0.015  # 1.5% если нет ATR
                # Уровни от текущей цены через ATR
                trading_plan = calculate_trading_plan(
                    price=price,
                    indicators=_plan_ind,
                    supports=[price - 1.5 * atr, price - 3 * atr],
                    resistances=[price + 2 * atr, price + 4 * atr],
                    swing_low=price - 2 * atr,
                )
            except Exception as e2:
                logger.error("ATR fallback plan error для %s: %s", coin, e2)

    if status in ("SHORT_ZONE", "STRONG_SHORT"):
        try:
            from analysis.levels import find_swing_points, find_key_levels
            from analysis.risk import calculate_short_plan

            price    = _plan_ind.get("price") or float(_plan_df["close"].iloc[-1])
            swing    = find_swing_points(_plan_df)
            supports, resistances = find_key_levels(_plan_df)

            short_plan = calculate_short_plan(
                price=price,
                indicators=_plan_ind,
                supports=supports,
                resistances=resistances,
                swing_high=swing.recent_high,
            )
        except Exception as e:
            logger.error("Short plan error для %s: %s", coin, e)

        # ATR fallback для шорта
        if short_plan is None or not short_plan.setup_valid:
            try:
                from analysis.risk import calculate_short_plan
                price = _plan_ind.get("price") or float(_plan_df["close"].iloc[-1])
                atr   = _plan_ind.get("atr") or price * 0.015
                short_plan = calculate_short_plan(
                    price=price,
                    indicators=_plan_ind,
                    supports=[price - 2 * atr, price - 4 * atr],
                    resistances=[price + 1.5 * atr, price + 3 * atr],
                    swing_high=price + 2 * atr,
                )
            except Exception as e2:
                logger.error("ATR fallback short plan error для %s: %s", coin, e2)

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

    indicators_combined = {**ind_1d}
    if ind_4h:
        indicators_combined["rsi_4h"]      = ind_4h.get("rsi")
        indicators_combined["macd_diff_4h"] = ind_4h.get("macd_diff")
    if ind_1h:
        indicators_combined["rsi_1h"]      = ind_1h.get("rsi")
        indicators_combined["macd_diff_1h"] = ind_1h.get("macd_diff")
    if ind_30m:
        indicators_combined["rsi_30m"]     = ind_30m.get("rsi")
    if ind_5m:
        indicators_combined["rsi_5m"]      = ind_5m.get("rsi")

    return {
        "signal":        signal,
        "emoji":         emoji,
        "score":         old_score,
        "setup_score":   setup_score,
        "short_score":   short_score,
        "direction":     direction,
        "status":        status,
        "label_ru":      label_ru,
        "action":        action,
        "action_icon":   action_icon,
        "action_desc":   action_desc,
        "entry_timing":  entry_timing,
        "timing_reason": timing_reason,
        "reasons":       reasons,
        "indicators":    indicators_combined,
        "trading_plan":  trading_plan,
        "short_plan":    short_plan,
        "has_4h":        bool(ind_4h),
        "has_1h":        bool(ind_1h),
        "sentiment_score": sentiment_score,
        "trend":         trend,
        "fg_label":      fg_label,
        "futures_ctx":   futures_ctx,
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
