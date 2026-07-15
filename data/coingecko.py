"""
Получение цен и OHLCV-данных с CoinGecko API (бесплатно, без ключа).
Включает кеширование и задержки для обхода rate-limit (30 req/min).
"""
import time
import logging
import requests
import pandas as pd
from functools import lru_cache
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# Маппинг тикеров → CoinGecko ID
COIN_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "bnb": "binancecoin",
    "binancecoin": "binancecoin",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
    "xrp": "ripple",
    "ada": "cardano",
    "avax": "avalanche-2",
    "ton": "the-open-network",
}

# ── Простой кеш в памяти ──────────────────────────────────────────────────────
_price_cache:  dict[str, tuple[datetime, dict]] = {}
_ohlcv_cache:  dict[str, tuple[datetime, pd.DataFrame]] = {}

PRICE_TTL = timedelta(minutes=3)   # цены кешируем 3 мин
OHLCV_TTL = timedelta(minutes=10)  # OHLCV кешируем 10 мин

# Задержка между запросами чтобы не получить 429
REQUEST_DELAY = 1.2   # секунды


def resolve(coin: str) -> str:
    """Резолвит тикер или имя в CoinGecko ID."""
    return COIN_IDS.get(coin.lower(), coin.lower())


def _get(url: str, params: dict, retries: int = 3) -> dict:
    """HTTP GET с retry при 429."""
    for attempt in range(retries):
        r = requests.get(url, params=params, timeout=20)
        if r.status_code == 429:
            wait = 15 * (attempt + 1)
            logger.warning("CoinGecko 429 — ждём %ds (попытка %d/%d)", wait, attempt+1, retries)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise Exception("CoinGecko rate limit exceeded после нескольких попыток")


def get_price(coin: str) -> dict:
    """Возвращает текущую цену и 24ч/7д статистику. Кешируется 3 мин."""
    coin_id = resolve(coin)

    # Проверяем кеш
    if coin_id in _price_cache:
        cached_at, data = _price_cache[coin_id]
        if datetime.now() - cached_at < PRICE_TTL:
            return data

    time.sleep(REQUEST_DELAY)
    url = f"{COINGECKO_BASE}/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    raw = _get(url, params)
    md = raw["market_data"]

    result = {
        "name": raw["name"],
        "symbol": raw["symbol"].upper(),
        "price_usd": md["current_price"]["usd"],
        "change_24h": md.get("price_change_percentage_24h") or 0.0,
        "change_7d": md.get("price_change_percentage_7d") or 0.0,
        "market_cap": md["market_cap"]["usd"],
        "volume_24h": md["total_volume"]["usd"],
        "high_24h": md["high_24h"]["usd"],
        "low_24h": md["low_24h"]["usd"],
    }
    _price_cache[coin_id] = (datetime.now(), result)
    return result


def get_ohlcv(coin: str, days: int = 60) -> pd.DataFrame:
    """
    Возвращает DataFrame с daily close и volume для технического анализа.
    Кешируется 10 мин — не делает лишних запросов при scan всех монет.
    """
    coin_id = resolve(coin)
    cache_key = f"{coin_id}_{days}"

    # Проверяем кеш
    if cache_key in _ohlcv_cache:
        cached_at, df = _ohlcv_cache[cache_key]
        if datetime.now() - cached_at < OHLCV_TTL:
            return df

    time.sleep(REQUEST_DELAY)
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    raw = _get(url, params)

    prices  = raw["prices"]
    volumes = raw["total_volumes"]

    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["volume"] = [v[1] for v in volumes]
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)

    _ohlcv_cache[cache_key] = (datetime.now(), df)
    return df
