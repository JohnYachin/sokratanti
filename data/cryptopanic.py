"""
Получение новостей криптовалют.
Основной источник: NewsAPI (newsapi.org) — бесплатно, 100 запросов/день.
Запасной: CryptoPanic API (если ключ работает).
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

COIN_QUERIES: dict[str, str] = {
    "btc": "Bitcoin BTC crypto",
    "bitcoin": "Bitcoin BTC crypto",
    "eth": "Ethereum ETH crypto",
    "ethereum": "Ethereum ETH crypto",
    "sol": "Solana SOL crypto",
    "solana": "Solana SOL crypto",
    "bnb": "BNB Binance crypto",
    "binancecoin": "BNB Binance crypto",
    "doge": "Dogecoin DOGE crypto",
    "dogecoin": "Dogecoin DOGE crypto",
}

COIN_CURRENCIES: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "bnb": "BNB", "binancecoin": "BNB",
    "doge": "DOGE", "dogecoin": "DOGE",
}


def get_news(coin: str, limit: int = 15) -> list[dict]:
    """
    Возвращает список свежих новостей для монеты.
    Пробует NewsAPI, потом CryptoPanic, потом возвращает [].
    """
    news = _newsapi(coin, limit)
    if news:
        return news
    news = _cryptopanic(coin, limit)
    if news:
        return news
    logger.warning("Нет источников новостей. Добавь NEWS_API_KEY в .env (newsapi.org — бесплатно).")
    return []


def _newsapi(coin: str, limit: int) -> list[dict]:
    """Новости через NewsAPI (newsapi.org) — бесплатно, ключ обязателен."""
    api_key = os.getenv("NEWS_API_KEY")
    if not api_key:
        return []
    query = COIN_QUERIES.get(coin.lower(), coin + " crypto")
    try:
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "q": query,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": limit,
                "apiKey": api_key,
            },
            timeout=10,
        )
        r.raise_for_status()
        articles = r.json().get("articles", [])
        return [
            {
                "title": a["title"],
                "url": a["url"],
                "published": a.get("publishedAt", ""),
            }
            for a in articles[:limit]
            if a.get("title") and "[Removed]" not in a["title"]
        ]
    except Exception as e:
        logger.error("NewsAPI error: %s", e)
        return []


def _cryptopanic(coin: str, limit: int) -> list[dict]:
    """CryptoPanic как запасной вариант."""
    api_key = os.getenv("CRYPTOPANIC_API_KEY")
    if not api_key:
        return []
    currency = COIN_CURRENCIES.get(coin.lower(), coin.upper())
    try:
        r = requests.get(
            "https://cryptopanic.com/api/v1/posts/",
            params={
                "auth_token": api_key,
                "currencies": currency,
                "kind": "news",
                "public": "true",
            },
            timeout=10,
        )
        if r.status_code == 403:
            logger.warning("CryptoPanic: 403 Forbidden — ключ недействителен или нужна подписка.")
            return []
        r.raise_for_status()
        results = r.json().get("results", [])
        return [
            {
                "title": item["title"],
                "url": item["url"],
                "published": item.get("published_at", ""),
            }
            for item in results[:limit]
        ]
    except Exception as e:
        logger.error("CryptoPanic error: %s", e)
        return []
