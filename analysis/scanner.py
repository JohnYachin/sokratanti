"""
Сканер всех монет с вероятностью сигнала.
Ранжирует BUY/SELL возможности по всем отслеживаемым монетам.
"""
import logging
from analysis.signals import generate_signal

logger = logging.getLogger(__name__)

TRACKED_COINS = ["btc", "eth", "sol", "bnb", "doge"]

# Максимально возможный score: RSI(2)+MACD(1)+BB(1)+F&G(2)+CP(1) = 7
MAX_SCORE = 7


def score_to_probability(score: int) -> int:
    """
    Конвертирует score (-7..+7) в вероятность удачи (%).
    50% = нейтрально, 95% = максимальная уверенность.
    """
    # Нормализуем к диапазону 0..1
    normalized = score / MAX_SCORE  # -1.0..+1.0
    # Маппим на 50%..95% (или 5%..50% для продажи)
    probability = 50 + normalized * 45
    return max(5, min(95, round(probability)))


def get_signal_bar(probability: int, signal: str) -> str:
    """Визуальный индикатор уверенности."""
    filled = round(probability / 10)
    if signal == "BUY":
        bar = "🟩" * filled + "⬜" * (10 - filled)
    elif signal == "SELL":
        bar = "🟥" * filled + "⬜" * (10 - filled)
    else:
        bar = "🟨" * 5 + "⬜" * 5
    return bar


def scan_all_coins() -> list[dict]:
    """
    Сканирует все монеты, возвращает список отсортированный
    от лучшей покупки к лучшей продаже.
    """
    results = []

    for coin in TRACKED_COINS:
        try:
            result = generate_signal(coin)
            score = result["score"]
            signal = result["signal"]
            prob = score_to_probability(abs(score))

            # Для HOLD вероятность всегда ~50%
            if signal == "HOLD":
                prob = score_to_probability(score)

            results.append({
                "coin": coin.upper(),
                "signal": signal,
                "emoji": result["emoji"],
                "score": score,
                "probability": prob,
                "reasons": result["reasons"],
                "indicators": result["indicators"],
            })
        except Exception as e:
            logger.error("Scan error for %s: %s", coin, e)

    # Сортируем: BUY сверху (по убыванию score), SELL снизу
    results.sort(key=lambda x: x["score"], reverse=True)
    return results


def get_top_opportunity(results: list[dict]) -> dict | None:
    """Возвращает лучшую возможность (BUY или SELL)."""
    buys = [r for r in results if r["signal"] == "BUY"]
    sells = [r for r in results if r["signal"] == "SELL"]

    if buys:
        return max(buys, key=lambda x: x["score"])
    if sells:
        return min(sells, key=lambda x: x["score"])
    return None
