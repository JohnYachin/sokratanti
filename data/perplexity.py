"""
Perplexity API интеграция.
Ищет актуальные новости и аналитику по криптовалютам
прямо в интернете в реальном времени.
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

SEARCH_URL = "https://api.perplexity.ai/search"
CHAT_URL = "https://api.perplexity.ai/chat/completions"

COIN_NAMES = {
    "btc": "Bitcoin", "eth": "Ethereum", "sol": "Solana",
    "bnb": "BNB Binance", "doge": "Dogecoin",
}


def _api_key() -> str | None:
    return os.getenv("PERPLEXITY_API_KEY")


def search_crypto_news(coin: str, max_results: int = 5) -> list[dict]:
    """
    Ищет свежие новости по монете через Perplexity /search.
    Возвращает список: [{title, snippet, url, date}]
    """
    key = _api_key()
    if not key:
        return []

    coin_name = COIN_NAMES.get(coin.lower(), coin.upper())
    query = f"{coin_name} {coin.upper()} crypto news price today"

    try:
        r = requests.post(
            SEARCH_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "query": query,
                "max_results": max_results,
                "max_tokens_per_page": 300,
            },
            timeout=20,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            {
                "title": item.get("title", ""),
                "snippet": item.get("snippet", "")[:300],
                "url": item.get("url", ""),
                "date": item.get("date", ""),
            }
            for item in results
        ]
    except Exception as e:
        logger.error("Perplexity search error: %s", e)
        return []


def get_market_analysis(coin: str) -> dict:
    """
    Запрашивает у Perplexity sonar аналитику рынка по монете.
    Поиск идёт в реальном времени по всему интернету.
    Возвращает: {analysis, key_events, outlook, sources}
    """
    key = _api_key()
    if not key:
        return {}

    coin_name = COIN_NAMES.get(coin.lower(), coin.upper())

    try:
        r = requests.post(
            CHAT_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": "sonar",
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Ты — крипто-аналитик. Отвечай кратко и по делу. "
                            "Всегда отвечай на русском языке."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Найди самые свежие новости и события по {coin_name} ({coin.upper()}) "
                            f"за последние 24-48 часов. Ответь:\n"
                            f"1. Что происходит с ценой и почему?\n"
                            f"2. Какие ключевые события влияют?\n"
                            f"3. Краткий прогноз на ближайшие часы: бычий или медвежий?\n"
                            f"Максимум 4-5 предложений суммарно."
                        ),
                    },
                ],
                "max_tokens": 400,
                "temperature": 0.2,
            },
            timeout=25,
        )
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]

        # Извлекаем источники если есть
        citations = data.get("citations", [])

        return {
            "analysis": content,
            "sources": citations[:3],
        }
    except Exception as e:
        logger.error("Perplexity chat error: %s", e)
        return {}
