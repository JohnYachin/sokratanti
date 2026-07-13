"""
Sentiment-анализ через OpenAI GPT-4o-mini.
Анализирует новости CryptoPanic + Reddit посты.
"""
import os
import json
import logging

logger = logging.getLogger(__name__)


def analyze_sentiment(coin: str) -> dict:
    """
    Собирает новости и посты, анализирует настроение через GPT.
    Если OpenAI недоступен — возвращает нейтральный результат.
    """
    from data.cryptopanic import get_news
    from data.reddit import get_reddit_posts

    # Собираем данные
    news = get_news(coin, limit=10)
    reddit = get_reddit_posts(coin, limit=5)

    headlines = [item["title"] for item in news]
    reddit_titles = [post["title"] for post in reddit]
    all_texts = headlines + reddit_titles

    if not all_texts:
        return {
            "score": 0.0,
            "sentiment": "neutral",
            "summary": "Нет данных для анализа (настрой API-ключи в .env)",
            "headlines": [],
        }

    # Проверяем наличие OpenAI ключа
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY не задан — используем keyword sentiment.")
        return _keyword_sentiment(coin, all_texts)

    return _gpt_sentiment(coin, all_texts, api_key)


def _gpt_sentiment(coin: str, texts: list[str], api_key: str) -> dict:
    """Анализ через GPT-4o-mini."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        text_block = "\n".join(f"- {t}" for t in texts[:15])
        prompt = (
            f"Проанализируй настроение рынка для {coin.upper()} "
            f"по следующим заголовкам новостей и постам:\n\n"
            f"{text_block}\n\n"
            f"Ответь строго в JSON формате:\n"
            f'{{"score": <float -1.0 до 1.0>, '
            f'"sentiment": "<bullish|bearish|neutral>", '
            f'"summary": "<1-2 предложения на русском>"}}'
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=300,
        )
        result = json.loads(response.choices[0].message.content)
        result["headlines"] = texts[:5]
        return result
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return _keyword_sentiment(coin, texts)


def _keyword_sentiment(coin: str, texts: list[str]) -> dict:
    """
    Простой keyword-анализ как fallback без OpenAI.
    """
    bullish_kw = [
        "bull", "surge", "rally", "moon", "pump", "ath", "breakout",
        "gain", "rise", "up", "green", "buy", "adoption", "growth",
    ]
    bearish_kw = [
        "bear", "crash", "dump", "drop", "fall", "down", "red", "sell",
        "hack", "ban", "regulation", "fear", "loss", "decline",
    ]

    text_lower = " ".join(texts).lower()
    bull_count = sum(text_lower.count(w) for w in bullish_kw)
    bear_count = sum(text_lower.count(w) for w in bearish_kw)
    total = bull_count + bear_count

    if total == 0:
        score = 0.0
    else:
        score = round((bull_count - bear_count) / total, 2)

    sentiment = "neutral"
    if score >= 0.2:
        sentiment = "bullish"
    elif score <= -0.2:
        sentiment = "bearish"

    summary = (
        f"Анализ по ключевым словам: "
        f"{bull_count} бычьих / {bear_count} медвежьих сигналов."
    )

    return {
        "score": score,
        "sentiment": sentiment,
        "summary": summary,
        "headlines": texts[:5],
    }
