"""
Получение данных с CoinGecko.
Использует /coins/markets — ОДИН запрос для всех монет (решает 429).
OHLCV берём через market_chart с кешем 15 минут.
"""
import time
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

BASE = "https://api.coingecko.com/api/v3"

COIN_IDS: dict[str, str] = {
    "btc":  "bitcoin",
    "eth":  "ethereum",
    "sol":  "solana",
    "bnb":  "binancecoin",
    "doge": "dogecoin",
    "xrp":  "ripple",
    "ada":  "cardano",
    "avax": "avalanche-2",
    "ton":  "the-open-network",
    # aliases
    "bitcoin":     "bitcoin",
    "ethereum":    "ethereum",
    "solana":      "solana",
    "binancecoin": "binancecoin",
    "dogecoin":    "dogecoin",
}

ALL_COINS = ["bitcoin", "ethereum", "solana", "binancecoin", "dogecoin"]

# ── Кеш ──────────────────────────────────────────────────────────────────────
_markets_cache: tuple[datetime, dict] | None = None   # (time, {id: data})
_ohlcv_cache:   dict[str, tuple[datetime, pd.DataFrame]] = {}

MARKETS_TTL = timedelta(minutes=4)
OHLCV_TTL   = timedelta(minutes=15)


def resolve(coin: str) -> str:
    return COIN_IDS.get(coin.lower(), coin.lower())


def _get(url: str, params: dict, retries: int = 3) -> dict | list:
    """GET с retry при 429."""
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=20)
            if r.status_code == 429:
                wait = 20 * (attempt + 1)
                logger.warning("CoinGecko 429 — пауза %ds (попытка %d/%d)", wait, attempt + 1, retries)
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            if attempt < retries - 1:
                time.sleep(10)
                continue
            raise
    raise RuntimeError("CoinGecko rate limit: исчерпаны попытки")


def _fetch_markets() -> dict:
    """
    Один запрос для ВСЕХ монет — /coins/markets.
    Возвращает словарь {coin_id: {...данные...}}.
    """
    global _markets_cache

    # Возвращаем кеш если свежий
    if _markets_cache:
        cached_at, data = _markets_cache
        if datetime.now() - cached_at < MARKETS_TTL:
            return data

    raw = _get(f"{BASE}/coins/markets", {
        "vs_currency": "usd",
        "ids": ",".join(ALL_COINS),
        "order": "market_cap_desc",
        "per_page": 10,
        "page": 1,
        "price_change_percentage": "24h,7d",
    })

    result = {}
    for item in raw:
        result[item["id"]] = {
            "name":       item["name"],
            "symbol":     item["symbol"].upper(),
            "price_usd":  item["current_price"],
            "change_24h": item.get("price_change_percentage_24h") or 0.0,
            "change_7d":  item.get("price_change_percentage_7d_in_currency") or 0.0,
            "market_cap": item.get("market_cap") or 0,
            "volume_24h": item.get("total_volume") or 0,
            "high_24h":   item.get("high_24h") or 0,
            "low_24h":    item.get("low_24h") or 0,
        }

    _markets_cache = (datetime.now(), result)
    logger.info("Markets обновлены: %d монет", len(result))
    return result


def get_price(coin: str) -> dict:
    """Цена из кешированного /coins/markets — не тратит отдельный запрос."""
    coin_id = resolve(coin)
    markets = _fetch_markets()
    if coin_id not in markets:
        raise ValueError(f"Монета '{coin}' не найдена в данных")
    return markets[coin_id]


def get_ohlcv(coin: str, days: int = 60) -> pd.DataFrame:
    """
    OHLCV данные для технического анализа.
    Кешируется 15 минут — один запрос на монету за сессию.
    """
    coin_id = resolve(coin)
    cache_key = f"{coin_id}_{days}"

    if cache_key in _ohlcv_cache:
        cached_at, df = _ohlcv_cache[cache_key]
        if datetime.now() - cached_at < OHLCV_TTL:
            return df

    # Небольшая пауза чтобы не флудить при первом запуске
    time.sleep(2)

    raw = _get(f"{BASE}/coins/{coin_id}/market_chart", {
        "vs_currency": "usd",
        "days": days,
        "interval": "daily",
    })

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
