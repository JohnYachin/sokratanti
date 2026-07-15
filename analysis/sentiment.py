"""
Sentiment-анализ через OpenAI GPT-4o-mini.
Источники: новости CryptoPanic (с голосами + panic score) + Reddit.
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

    # Собираем новости с метаданными
    news = get_news(coin, limit=12)
    reddit = get_reddit_posts(coin, limit=5)

    # Форматируем новости с контекстом голосов
    news_lines = []
    for n in news:
        line = n["title"]
        if n.get("votes_positive", 0) + n.get("votes_negative", 0) > 0:
            line += f" [+{n['votes_positive']}👍 -{n['votes_negative']}👎]"
        if n.get("panic_score", 0) > 5:
            line += f" [panic={n['panic_score']}]"
        news_lines.append(line)

    reddit_titles = [post["title"] for post in reddit]
    all_texts = news_lines + reddit_titles

    if not all_texts:
        return {
            "score": 0.0,
            "sentiment": "neutral",
            "summary": "Нет данных для анализа",
            "headlines": [],
        }

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY не задан — используем keyword sentiment.")
        return _keyword_sentiment(coin, all_texts)

    return _gpt_sentiment(coin, all_texts, api_key)


def _gpt_sentiment(coin: str, texts: list[str], api_key: str) -> dict:
    """Анализ через GPT-4o-mini с торговым контекстом."""
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)

        text_block = "\n".join(f"• {t}" for t in texts[:15])
        prompt = (
            f"Ты — опытный крипто-аналитик. Проанализируй настроение рынка "
            f"для {coin.upper()} по заголовкам ниже. "
            f"Учти голоса сообщества (👍/👎) и panic score если есть.\n\n"
            f"Заголовки:\n{text_block}\n\n"
            f"Дай оценку в JSON:\n"
            f'{{"score": <float от -1.0 (медвежий) до 1.0 (бычий)>, '
            f'"sentiment": "<bullish|bearish|neutral>", '
            f'"summary": "<2-3 предложения на русском: что происходит и что это значит для трейдера>", '
            f'"key_factor": "<главный фактор влияющий на цену сейчас>"}}'
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.3,
            max_tokens=400,
        )
        result = json.loads(response.choices[0].message.content)
        result["headlines"] = texts[:5]
        return result
    except Exception as e:
        logger.error("OpenAI error: %s", e)
        return _keyword_sentiment(coin, texts)


def _keyword_sentiment(coin: str, texts: list[str]) -> dict:
    """Keyword-анализ как fallback без OpenAI."""
    bullish_kw = [
        "bull", "surge", "rally", "moon", "pump", "ath", "breakout",
        "gain", "rise", "up", "green", "buy", "adoption", "growth",
        "record", "high", "support", "accumulate",
    ]
    bearish_kw = [
        "bear", "crash", "dump", "drop", "fall", "down", "red", "sell",
        "hack", "ban", "regulation", "fear", "loss", "decline",
        "warning", "risk", "liquidat",
    ]

    text_lower = " ".join(texts).lower()
    bull_count = sum(text_lower.count(w) for w in bullish_kw)
    bear_count = sum(text_lower.count(w) for w in bearish_kw)
    total = bull_count + bear_count

    score = round((bull_count - bear_count) / total, 2) if total > 0 else 0.0
    sentiment = "neutral"
    if score >= 0.2:
        sentiment = "bullish"
    elif score <= -0.2:
        sentiment = "bearish"

    return {
        "score": score,
        "sentiment": sentiment,
        "summary": f"Keyword-анализ: {bull_count} бычьих / {bear_count} медвежьих сигналов.",
        "key_factor": "—",
        "headlines": texts[:5],
    }
