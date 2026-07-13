"""
Получение новостей криптовалют через CryptoPanic API.
Бесплатный ключ: https://cryptopanic.com/developers/api/
"""
import os
import logging
import requests

logger = logging.getLogger(__name__)

CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1"

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
    Если нет API-ключа — возвращает [] без ошибки.
    """
    api_key = os.getenv("CRYPTOPANIC_API_KEY")
    if not api_key:
        logger.warning("CRYPTOPANIC_API_KEY не задан — новости недоступны.")
        return []

    currency = COIN_CURRENCIES.get(coin.lower(), coin.upper())
    params = {
        "auth_token": api_key,
        "currencies": currency,
        "kind": "news",
        "public": "true",
    }

    try:
        r = requests.get(f"{CRYPTOPANIC_BASE}/posts/", params=params, timeout=10)
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
