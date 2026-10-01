"""
data/cryptopanic.py — Новости через CryptoPanic API (Growth Weekly).

ВАЖНО: CryptoPanic является ОПЦИОНАЛЬНЫМ источником (v1.1+).
Основные новости поступают через RSS (news/rss.py). Без CRYPTOPANIC_API_KEY
бот работает корректно — производится graceful degradation через RSS.
"""
import os
import time
import logging
import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://cryptopanic.com/api/growth_weekly/v2"

# Кэш 2 часа — не долбим API каждые 30 минут за 20 монет
_CACHE: dict[str, tuple[float, list]] = {}  # coin → (timestamp, data)
_CACHE_TTL = 7200  # 2 часа

COIN_CURRENCIES: dict[str, str] = {
    "btc": "BTC", "bitcoin": "BTC",
    "eth": "ETH", "ethereum": "ETH",
    "sol": "SOL", "solana": "SOL",
    "bnb": "BNB", "binancecoin": "BNB",
    "doge": "DOGE", "dogecoin": "DOGE",
}

COIN_QUERIES: dict[str, str] = {
    "btc": "Bitcoin BTC crypto",
    "ethereum": "Ethereum ETH crypto",
    "eth": "Ethereum ETH crypto",
    "sol": "Solana SOL crypto",
    "bnb": "BNB Binance crypto",
    "doge": "Dogecoin DOGE crypto",
}


def _api_key() -> str | None:
    return os.getenv("CRYPTOPANIC_API_KEY")


def get_news(coin: str, limit: int = 15) -> list[dict]:
    """
    Возвращает свежие новости для монеты.
    Каждый элемент содержит: title, url, published,
    votes_positive, votes_negative, panic_score.
    """
    cache_key = f"news:{coin.lower()}"
    if cache_key in _CACHE:
        ts, data = _CACHE[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return data

    key = _api_key()
    if not key:
        logger.warning("CRYPTOPANIC_API_KEY не задан.")
        return _newsapi_fallback(coin, limit)

    currency = COIN_CURRENCIES.get(coin.lower(), coin.upper())
    try:
        r = requests.get(
            f"{BASE_URL}/posts/",
            params={
                "auth_token": key,
                "currencies": currency,
                "kind": "news",
                "public": "true",
                "regions": "en",
            },
            timeout=10,
        )
        if r.status_code == 429:
            logger.warning("CryptoPanic 429 — rate limit, используем кэш или пропускаем")
            return _CACHE.get(cache_key, (0, []))[1]
        if r.status_code == 403:
            logger.warning("CryptoPanic 403 — проверь ключ или лимиты.")
            return _newsapi_fallback(coin, limit)
        r.raise_for_status()
        results = r.json().get("results", [])
        data = [_parse_item(item) for item in results[:limit]]
        _CACHE[cache_key] = (time.time(), data)
        return data
    except Exception as e:
        logger.error("CryptoPanic error: %s", e)
        return _newsapi_fallback(coin, limit)


def get_sentiment_posts(coin: str, sentiment: str = "bullish") -> list[dict]:
    """
    Возвращает новости отфильтрованные по настроению.
    sentiment: 'bullish' | 'bearish' | 'rising' | 'hot' | 'important'
    """
    key = _api_key()
    if not key:
        return []

    currency = COIN_CURRENCIES.get(coin.lower(), coin.upper())
    try:
        r = requests.get(
            f"{BASE_URL}/posts/",
            params={
                "auth_token": key,
                "currencies": currency,
                "filter": sentiment,
                "kind": "news",
                "public": "true",
                "regions": "en",
            },
            timeout=10,
        )
        r.raise_for_status()
        return [_parse_item(item) for item in r.json().get("results", [])[:10]]
    except Exception as e:
        logger.error("CryptoPanic sentiment error: %s", e)
        return []


def calc_vote_sentiment(coin: str) -> dict:
    """
    Вычисляет sentiment-оценку по голосам сообщества CryptoPanic.
    Возвращает score от -1.0 (медвежье) до +1.0 (бычье).
    """
    news = get_news(coin, limit=20)
    if not news:
        return {"score": 0.0, "bullish_count": 0, "bearish_count": 0, "panic_avg": 0}

    total_pos = sum(n["votes_positive"] for n in news)
    total_neg = sum(n["votes_negative"] for n in news)
    total = total_pos + total_neg
    panic_avg = sum(n["panic_score"] for n in news) / len(news)

    score = 0.0
    if total > 0:
        score = round((total_pos - total_neg) / total, 2)

    return {
        "score": score,
        "bullish_count": total_pos,
        "bearish_count": total_neg,
        "panic_avg": round(panic_avg, 1),
        "headlines": [n["title"] for n in news[:5]],
    }


def _parse_item(item: dict) -> dict:
    votes = item.get("votes") or {}
    return {
        "title": item.get("title", ""),
        "url": item.get("original_url") or item.get("url", ""),
        "published": item.get("published_at", ""),
        "votes_positive": votes.get("positive", 0),
        "votes_negative": votes.get("negative", 0),
        "panic_score": item.get("panic_score") or 0,
    }


def _newsapi_fallback(coin: str, limit: int) -> list[dict]:
    """NewsAPI как резервный источник если CryptoPanic недоступен."""
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
                "votes_positive": 0,
                "votes_negative": 0,
                "panic_score": 0,
            }
            for a in articles[:limit]
            if a.get("title") and "[Removed]" not in a["title"]
        ]
    except Exception as e:
        logger.error("NewsAPI error: %s", e)
        return []

