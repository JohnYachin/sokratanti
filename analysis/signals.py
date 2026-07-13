"""
Движок торговых сигналов.
Объединяет технический анализ (RSI, MACD, BB) и sentiment.
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


def generate_signal(coin: str, include_sentiment: bool = False) -> dict:
    """
    Генерирует торговый сигнал BUY / SELL / HOLD.

    Система очков:
      RSI:          от -2 до +2
      MACD:         от -1 до +1
      Bollinger:    от -1 до +1
      Sentiment:    от -2 до +2 (если include_sentiment=True)

    Порог:  score >= +2 → BUY
            score <= -2 → SELL
            иначе      → HOLD
    """
    df = get_ohlcv(coin)
    indicators = calculate_indicators(df)

    score = 0
    reasons = []

    # RSI
    rsi_reason, rsi_score = interpret_rsi(indicators["rsi"])
    score += rsi_score
    reasons.append(rsi_reason)

    # MACD
    macd_reason, macd_score = interpret_macd(indicators["macd_diff"])
    score += macd_score
    reasons.append(macd_reason)

    # Bollinger Bands
    bb_reason, bb_score = interpret_bb(indicators["bb_pband"])
    score += bb_score
    reasons.append(bb_reason)

    # Sentiment (опционально, не делает запрос по умолчанию)
    sentiment_score = 0.0
    if include_sentiment:
        try:
            from analysis.sentiment import analyze_sentiment
            sent = analyze_sentiment(coin)
            sentiment_score = sent["score"]
            sent_points = round(sentiment_score * 2)
            score += sent_points
            mood = "бычье" if sentiment_score > 0 else "медвежье" if sentiment_score < 0 else "нейтральное"
            reasons.append(f"Настроение рынка: {mood} (score {sentiment_score:+.2f}, очки {sent_points:+d})")
        except Exception as e:
            logger.error("Sentiment error in signal: %s", e)

    # Итоговый сигнал
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
