"""
market_data/binance_rest.py — Binance Spot Public API (без API-ключей).

Эндпоинт: GET https://api.binance.com/api/v3/klines
Поддерживаемые интервалы: 1h, 4h, 1d
Монеты: BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, DOGEUSDT

Особенности:
  - Нет API-ключей (публичный endpoint)
  - Последняя незакрытая свеча всегда отбрасывается
  - Кеш: 2 мин (1h), 8 мин (4h), 30 мин (1d)
  - Retry при 429 и сетевых ошибках
  - CoinGecko как fallback если Binance недоступен
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Конфигурация ──────────────────────────────────────────────────────────────
BINANCE_BASE = "https://api.binance.com/api/v3"

COIN_TO_SYMBOL: dict[str, str] = {
    "btc": "BTCUSDT", "bitcoin": "BTCUSDT",
    "eth": "ETHUSDT", "ethereum": "ETHUSDT",
    "sol": "SOLUSDT", "solana": "SOLUSDT",
    "bnb": "BNBUSDT", "binancecoin": "BNBUSDT",
    "doge": "DOGEUSDT", "dogecoin": "DOGEUSDT",
    "xrp": "XRPUSDT", "ripple": "XRPUSDT",
    "ada": "ADAUSDT", "cardano": "ADAUSDT",
    "avax": "AVAXUSDT", "avalanche-2": "AVAXUSDT",
    "ton": "TONUSDT",  "the-open-network": "TONUSDT",
}

# TTL кеша в секундах для каждого интервала
CACHE_TTL: dict[str, int] = {
    "1h":  120,    # 2 минуты
    "4h":  480,    # 8 минут
    "1d":  1800,   # 30 минут
}

# Длительность одной свечи в секундах (для определения незакрытой)
INTERVAL_SECONDS: dict[str, int] = {
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
}

# In-memory кеш: {(symbol, interval, limit): (fetched_at, DataFrame)}
_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}


def resolve_symbol(coin: str) -> str:
    """Конвертирует тикер монеты в символ Binance (напр. 'btc' → 'BTCUSDT')."""
    sym = COIN_TO_SYMBOL.get(coin.lower())
    if sym is None:
        sym = coin.upper() + "USDT"
        logger.warning("Неизвестная монета '%s', используем '%s'", coin, sym)
    return sym


def get_klines(
    coin: str,
    interval: str = "1d",
    limit: int = 200,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Загружает исторические свечи с Binance.

    Returns:
        DataFrame с колонками [open, high, low, close, volume],
        индекс — UTC datetime. Последняя незакрытая свеча отброшена.
        При ошибке возвращает пустой DataFrame с нужными колонками.

    Raises:
        Никогда не бросает — при ошибке пробует CoinGecko fallback.
    """
    if interval not in CACHE_TTL:
        logger.error("Неподдерживаемый интервал: %s", interval)
        return _empty_df()

    symbol = resolve_symbol(coin)
    cache_key = (symbol, interval, limit)

    # Проверяем кеш
    if use_cache and cache_key in _cache:
        fetched_at, df = _cache[cache_key]
        if time.time() - fetched_at < CACHE_TTL[interval]:
            return df

    df = _fetch_binance(symbol, interval, limit)

    if df.empty:
        logger.warning("Binance вернул пустой результат для %s %s, пробуем fallback", symbol, interval)
        df = _coingecko_fallback(coin, interval, limit)

    if not df.empty:
        _cache[cache_key] = (time.time(), df)

    return df


def _fetch_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Загружает свечи с Binance REST API с retry при 429."""
    retries = [1, 3, 10]  # задержки в секундах
    for attempt, wait in enumerate(retries, 1):
        try:
            resp = requests.get(
                f"{BINANCE_BASE}/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit + 1},
                timeout=10,
                headers={"User-Agent": "Sokratanti/2.0"},
            )

            if resp.status_code == 429:
                logger.warning("Binance rate limit (429), ожидаем %ds", wait)
                time.sleep(wait)
                continue

            if resp.status_code == 400:
                logger.error("Binance 400 для %s — возможно монета не торгуется: %s",
                             symbol, resp.text[:100])
                return _empty_df()

            resp.raise_for_status()
            raw = resp.json()

            if not raw:
                return _empty_df()

            df = _parse_klines(raw)
            df = _drop_unclosed(df, interval)
            df = _validate(df, symbol, interval)
            return df

        except requests.RequestException as e:
            logger.warning("Binance fetch attempt %d/%d для %s: %s",
                           attempt, len(retries), symbol, e)
            if attempt < len(retries):
                time.sleep(wait)

    return _empty_df()


def _parse_klines(raw: list) -> pd.DataFrame:
    """
    Парсит ответ Binance klines в DataFrame.
    Формат строки: [open_time, open, high, low, close, volume, close_time, ...]
    """
    records = []
    for row in raw:
        open_time_ms = int(row[0])
        records.append({
            "timestamp": datetime.fromtimestamp(open_time_ms / 1000, tz=timezone.utc),
            "open":      float(row[1]),
            "high":      float(row[2]),
            "low":       float(row[3]),
            "close":     float(row[4]),
            "volume":    float(row[5]),
            "close_time_ms": int(row[6]),
        })

    df = pd.DataFrame(records)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    return df


def _drop_unclosed(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    """
    Отбрасывает последнюю незакрытую свечу.
    Свеча считается незакрытой если её close_time > текущего времени.
    """
    if df.empty or "close_time_ms" not in df.columns:
        return df

    now_ms = time.time() * 1000
    mask = df["close_time_ms"] <= now_ms
    closed = df[mask].copy()

    if len(closed) < len(df):
        logger.debug("Отброшена %d незакрытая свеча (%s)", len(df) - len(closed), interval)

    # Убираем служебную колонку
    closed.drop(columns=["close_time_ms"], inplace=True, errors="ignore")
    return closed


def _validate(df: pd.DataFrame, symbol: str, interval: str) -> pd.DataFrame:
    """Проверяет качество данных, логирует проблемы."""
    if df.empty:
        return df

    # Нулевые или отрицательные цены
    bad_prices = (df["close"] <= 0) | (df["open"] <= 0)
    if bad_prices.any():
        logger.warning("Binance %s %s: %d свечей с нулевой/отрицательной ценой — удаляем",
                       symbol, interval, bad_prices.sum())
        df = df[~bad_prices]

    # Дубликаты по индексу
    if df.index.duplicated().any():
        logger.warning("Binance %s %s: дубликаты в индексе — удаляем", symbol, interval)
        df = df[~df.index.duplicated(keep="last")]

    # Проверяем свежесть последней свечи
    if not df.empty:
        last_ts = df.index[-1]
        age_hours = (datetime.now(timezone.utc) - last_ts).total_seconds() / 3600
        max_age = INTERVAL_SECONDS.get(interval, 86400) / 3600 * 3
        if age_hours > max_age:
            logger.warning("Binance %s %s: последняя свеча устарела на %.1f ч.",
                           symbol, interval, age_hours)

    return df


def _coingecko_fallback(coin: str, interval: str, limit: int) -> pd.DataFrame:
    """
    Fallback на CoinGecko если Binance недоступен.
    Работает только для interval='1d'.
    """
    if interval != "1d":
        logger.warning("CoinGecko fallback поддерживает только 1d, не %s", interval)
        return _empty_df()

    try:
        from data.coingecko import get_ohlcv
        logger.info("Используем CoinGecko fallback для %s", coin)
        return get_ohlcv(coin, days=min(limit, 90))
    except Exception as e:
        logger.error("CoinGecko fallback ошибка: %s", e)
        return _empty_df()


def _empty_df() -> pd.DataFrame:
    """Пустой DataFrame с нужными колонками."""
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


def healthcheck() -> dict:
    """Проверяет доступность Binance API."""
    try:
        resp = requests.get(f"{BINANCE_BASE}/ping", timeout=5)
        return {"status": "ok", "latency_ms": round(resp.elapsed.total_seconds() * 1000)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def get_supported_symbols() -> list[str]:
    """Возвращает список всех поддерживаемых символов."""
    return sorted(set(COIN_TO_SYMBOL.values()))
