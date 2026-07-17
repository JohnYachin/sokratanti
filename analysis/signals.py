"""
analysis/signals.py — Движок торговых сигналов v1.1

Источники оценки (score -7..+7):
  RSI:            от -2 до +2
  MACD:           от -1 до +1
  Bollinger Bands: от -1 до +1
  Fear & Greed:   от -2 до +2
  News score:     от -1 до +1  (RSS primary, CryptoPanic optional)
  AI Sentiment:   от -2 до +2  (только если include_sentiment=True)

CryptoPanic полностью опционален — бот работает без CRYPTOPANIC_API_KEY.
При недоступности любого источника → score того источника = 0, сигнал не падает.
"""
import logging
from data.coingecko import get_ohlcv
from analysis.technical import (
    calculate_indicators,
    interpret_rsi,
    interpret_macd,
    interpret_bb,
)

logger = logging.getLogger(__name__)


def _get_news_score(coin: str) -> tuple[float, str]:
    """
    Возвращает (score -1..+1, описание).
    Приоритет: RSS → CryptoPanic → 0.0 (graceful degradation).
    Никогда не бросает исключение наружу.
    """
    # 1. Пробуем RSS (бесплатно, без ключа)
    try:
        from news.rss import get_news_for_coin
        from news.normalizer import calc_news_score
        rss_items = get_news_for_coin(coin, limit=15)
        if rss_items:
            score = calc_news_score(rss_items, symbol=coin.upper())
            # Проверяем критические события (hack/delist/outage)
            critical = [i for i in rss_items if i.is_critical]
            if critical:
                note = f"⚠️ Крит. событие: {critical[0].title[:60]}… (очки {score:+.2f})"
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

    # 3. Нет данных → нейтрально
    return 0.0, "Новости: нет данных (нейтрально)"


def generate_signal(coin: str, include_sentiment: bool = False) -> dict:
    """
    Генерирует торговый сигнал BUY / SELL / HOLD.

    Пороговые значения:
      score >= +2  →  BUY
      score <= -2  →  SELL
      иначе        →  HOLD
    """
    df = get_ohlcv(coin)
    indicators = calculate_indicators(df)

    score = 0
    reasons = []

    # ── Технический анализ ──────────────────────────────────────────────────
    rsi_reason, rsi_score = interpret_rsi(indicators["rsi"])
    score += rsi_score
    reasons.append(rsi_reason)

    macd_reason, macd_score = interpret_macd(indicators["macd_diff"])
    score += macd_score
    reasons.append(macd_reason)

    bb_reason, bb_score = interpret_bb(indicators["bb_pband"])
    score += bb_score
    reasons.append(bb_reason)

    # ── Fear & Greed Index ──────────────────────────────────────────────────
    try:
        from data.feargreed import get_fear_greed
        fg = get_fear_greed()
        fg_score = fg["score"]          # -2..+2
        score += fg_score
        reasons.append(
            f"Fear & Greed: {fg['value']}/100 {fg['label_ru']} "
            f"(очки {fg_score:+d})"
        )
    except Exception as e:
        logger.warning("Fear & Greed error: %s", e)

    # ── Новостной score (RSS primary, CryptoPanic fallback) ─────────────────
    try:
        news_raw, news_reason = _get_news_score(coin)
        # Конвертируем -1..+1 в целые очки для score
        news_points = 1 if news_raw > 0.15 else (-1 if news_raw < -0.15 else 0)
        score += news_points
        if news_reason:
            reasons.append(f"{news_reason} (очки {news_points:+d})")
    except Exception as e:
        logger.warning("News score error: %s", e)

    # ── AI Sentiment (опционально) ──────────────────────────────────────────
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
                f"AI Sentiment: {mood} "
                f"(score {sentiment_score:+.2f}, очки {sent_points:+d})"
            )
        except Exception as e:
            logger.error("Sentiment error in signal: %s", e)

    # ── Итоговый сигнал ─────────────────────────────────────────────────────
    if score >= 2:
        signal, emoji = "BUY", "🟢"
    elif score <= -2:
        signal, emoji = "SELL", "🔴"
    else:
        signal, emoji = "HOLD", "🟡"

    return {
        "signal": signal,
        "emoji": emoji,
        "score": score,
        "reasons": reasons,
        "indicators": indicators,
        "sentiment_score": sentiment_score,
    }
