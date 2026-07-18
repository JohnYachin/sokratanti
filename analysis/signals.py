"""
analysis/signals.py — Движок торговых сигналов v1.2

Источники данных:
  - Binance REST (1d + 4h свечи) — основной источник
  - CoinGecko fallback (1d only) — если Binance недоступен

Система оценки (score -9..+9):
  RSI(1d):         от -2 до +2
  MACD(1d):        от -1 до +1
  BB(1d):          от -1 до +1
  RSI(4h):         от -1 до +1  (подтверждение краткосрочного momentum)
  MACD(4h):        от -1 до +1  (согласованность таймфреймов)
  Fear & Greed:    от -2 до +2
  News score:      от -1 до +1  (RSS primary, CryptoPanic optional)
  AI Sentiment:    от -2 до +2  (только если include_sentiment=True)

Пороги:
  score >= +2  →  BUY
  score <= -2  →  SELL
  иначе        →  HOLD
"""
import logging
from analysis.technical import calculate_indicators, interpret_rsi, interpret_macd, interpret_bb

logger = logging.getLogger(__name__)


def _get_ohlcv(coin: str, interval: str = "1d", limit: int = 200):
    """
    Загружает свечи: сначала Binance, потом CoinGecko (только 1d).
    Никогда не бросает — возвращает None при полном отказе.
    """
    try:
        from market_data.binance_rest import get_klines
        df = get_klines(coin, interval=interval, limit=limit)
        if not df.empty and len(df) >= 30:
            return df
        logger.warning("Binance вернул мало данных для %s %s (%d свечей)",
                       coin, interval, len(df))
    except Exception as e:
        logger.warning("Binance error для %s %s: %s", coin, interval, e)

    # Fallback только для дневного
    if interval == "1d":
        try:
            from data.coingecko import get_ohlcv
            df = get_ohlcv(coin, days=90)
            if not df.empty:
                logger.info("Используем CoinGecko fallback для %s", coin)
                return df
        except Exception as e:
            logger.error("CoinGecko fallback тоже недоступен для %s: %s", coin, e)

    return None


def _get_news_score(coin: str) -> tuple[float, str]:
    """
    Возвращает (score -1..+1, описание).
    Приоритет: RSS → CryptoPanic → 0.0 (graceful degradation).
    """
    # 1. RSS (бесплатно, без ключа)
    try:
        from news.rss import get_news_for_coin
        from news.normalizer import calc_news_score
        rss_items = get_news_for_coin(coin, limit=15)
        if rss_items:
            score = calc_news_score(rss_items, symbol=coin.upper())
            critical = [i for i in rss_items if i.is_critical]
            if critical:
                note = f"⚠️ Крит. событие: {critical[0].title[:55]}… (тональность {score:+.2f})"
            else:
                note = f"Новости RSS: {len(rss_items)} статей (тональность {score:+.2f})"
            return score, note
    except Exception as e:
        logger.warning("RSS news score error: %s", e)

    # 2. Fallback: CryptoPanic (если ключ задан)
    try:
        from data.cryptopanic import calc_vote_sentiment
        cp = calc_vote_sentiment(coin)
        cp_score = float(cp.get("score", 0.0))
        if cp.get("bullish_count", 0) + cp.get("bearish_count", 0) > 0:
            note = (
                f"Новости (CryptoPanic): +{cp['bullish_count']}👍 "
                f"-{cp['bearish_count']}👎 (тональность {cp_score:+.2f})"
            )
            return cp_score, note
    except Exception as e:
        logger.debug("CryptoPanic fallback skipped: %s", e)

    return 0.0, "Новости: нет данных (нейтрально)"


def generate_signal(coin: str, include_sentiment: bool = False) -> dict:
    """
    Генерирует торговый сигнал BUY / SELL / HOLD.

    Анализирует два таймфрейма:
      1d — основной (тренд)
      4h — подтверждение (краткосрочный momentum)
    """
    score = 0
    reasons = []
    indicators_1d = {}
    indicators_4h = {}

    # ── Дневной таймфрейм (основной) ────────────────────────────────────────
    df_1d = _get_ohlcv(coin, interval="1d", limit=200)
    if df_1d is not None and not df_1d.empty:
        try:
            indicators_1d = calculate_indicators(df_1d)

            rsi_reason, rsi_score = interpret_rsi(indicators_1d["rsi"])
            score += rsi_score
            reasons.append(f"[1d] {rsi_reason}")

            macd_reason, macd_score = interpret_macd(indicators_1d["macd_diff"])
            score += macd_score
            reasons.append(f"[1d] {macd_reason}")

            bb_reason, bb_score = interpret_bb(indicators_1d["bb_pband"])
            score += bb_score
            reasons.append(f"[1d] {bb_reason}")
        except Exception as e:
            logger.error("Ошибка индикаторов 1d для %s: %s", coin, e)
    else:
        reasons.append("⚠️ Данные 1d недоступны")

    # ── 4h таймфрейм (подтверждение) ────────────────────────────────────────
    df_4h = _get_ohlcv(coin, interval="4h", limit=200)
    if df_4h is not None and not df_4h.empty:
        try:
            indicators_4h = calculate_indicators(df_4h)

            # RSI 4h — проверяем согласованность с 1d
            rsi_4h = indicators_4h["rsi"]
            if rsi_4h <= 35:
                rsi_4h_score, rsi_4h_desc = +1, f"RSI 4h {rsi_4h:.1f} — перепродан (подтверждение)"
            elif rsi_4h >= 65:
                rsi_4h_score, rsi_4h_desc = -1, f"RSI 4h {rsi_4h:.1f} — перекуплен (предупреждение)"
            else:
                rsi_4h_score, rsi_4h_desc = 0, f"RSI 4h {rsi_4h:.1f} — нейтральный"
            score += rsi_4h_score
            reasons.append(f"[4h] {rsi_4h_desc}")

            # MACD 4h — краткосрочный импульс
            macd_4h_reason, macd_4h_score = interpret_macd(indicators_4h["macd_diff"])
            score += macd_4h_score
            reasons.append(f"[4h] {macd_4h_reason}")
        except Exception as e:
            logger.warning("Ошибка индикаторов 4h для %s: %s", coin, e)
    else:
        logger.info("4h данные недоступны для %s — пропускаем", coin)

    # ── Fear & Greed Index ───────────────────────────────────────────────────
    try:
        from data.feargreed import get_fear_greed
        fg = get_fear_greed()
        fg_score = fg["score"]   # -2..+2
        score += fg_score
        reasons.append(
            f"Fear & Greed: {fg['value']}/100 {fg['label_ru']} (очки {fg_score:+d})"
        )
    except Exception as e:
        logger.warning("Fear & Greed error: %s", e)

    # ── Новостной score ──────────────────────────────────────────────────────
    try:
        news_raw, news_reason = _get_news_score(coin)
        news_points = 1 if news_raw > 0.15 else (-1 if news_raw < -0.15 else 0)
        score += news_points
        if news_reason:
            reasons.append(f"{news_reason} (очки {news_points:+d})")
    except Exception as e:
        logger.warning("News score error: %s", e)

    # ── AI Sentiment (опционально) ───────────────────────────────────────────
    sentiment_score = 0.0
    if include_sentiment:
        try:
            from analysis.sentiment import analyze_sentiment
            sent = analyze_sentiment(coin)
            sentiment_score = sent["score"]
            sent_points = round(sentiment_score * 2)
            score += sent_points
            mood = (
                "бычье" if sentiment_score > 0
                else "медвежье" if sentiment_score < 0
                else "нейтральное"
            )
            reasons.append(
                f"AI Sentiment: {mood} (score {sentiment_score:+.2f}, очки {sent_points:+d})"
            )
        except Exception as e:
            logger.error("Sentiment error: %s", e)

    # ── Итоговый сигнал ──────────────────────────────────────────────────────
    if score >= 2:
        signal, emoji = "BUY", "🟢"
    elif score <= -2:
        signal, emoji = "SELL", "🔴"
    else:
        signal, emoji = "HOLD", "🟡"

    # Объединяем индикаторы обоих таймфреймов для отображения
    indicators_combined = {**indicators_1d}
    if indicators_4h:
        indicators_combined["rsi_4h"] = indicators_4h.get("rsi")
        indicators_combined["macd_diff_4h"] = indicators_4h.get("macd_diff")

    return {
        "signal":          signal,
        "emoji":           emoji,
        "score":           score,
        "reasons":         reasons,
        "indicators":      indicators_combined,
        "sentiment_score": sentiment_score,
        "has_4h":          bool(indicators_4h),
    }
