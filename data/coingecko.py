"""
Получение цен и OHLCV-данных с CoinGecko API (бесплатно, без ключа).
"""
import logging
import requests
import pandas as pd

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
}


def resolve(coin: str) -> str:
    """Резолвит тикер или имя в CoinGecko ID."""
    return COIN_IDS.get(coin.lower(), coin.lower())


def get_price(coin: str) -> dict:
    """Возвращает текущую цену и 24ч/7д статистику."""
    coin_id = resolve(coin)
    url = f"{COINGECKO_BASE}/coins/{coin_id}"
    params = {
        "localization": "false",
        "tickers": "false",
        "market_data": "true",
        "community_data": "false",
        "developer_data": "false",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    md = data["market_data"]

    return {
        "name": data["name"],
        "symbol": data["symbol"].upper(),
        "price_usd": md["current_price"]["usd"],
        "change_24h": md.get("price_change_percentage_24h") or 0.0,
        "change_7d": md.get("price_change_percentage_7d") or 0.0,
        "market_cap": md["market_cap"]["usd"],
        "volume_24h": md["total_volume"]["usd"],
        "high_24h": md["high_24h"]["usd"],
        "low_24h": md["low_24h"]["usd"],
    }


def get_ohlcv(coin: str, days: int = 60) -> pd.DataFrame:
    """
    Возвращает DataFrame с daily close и volume для технического анализа.
    Используется /coins/{id}/market_chart (интервал daily автоматически
    при days >= 2).
    """
    coin_id = resolve(coin)
    url = f"{COINGECKO_BASE}/coins/{coin_id}/market_chart"
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    raw = r.json()

    prices = raw["prices"]      # [[timestamp_ms, price], ...]
    volumes = raw["total_volumes"]

    df = pd.DataFrame(prices, columns=["timestamp", "close"])
    df["volume"] = [v[1] for v in volumes]
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    df = df[~df.index.duplicated(keep="last")]
    df.sort_index(inplace=True)
    return df
