"""
market_data/binance_futures.py — Binance USDT-M Futures публичный API.

Эндпоинты (fapi.binance.com) — БЕЗ API-ключей:
  /fapi/v1/klines        — свечи фьючерсов (то что видишь в Binance Futures)
  /fapi/v1/premiumIndex  — Mark Price (реальная цена фьючерса)
  /fapi/v1/fundingRate   — история ставок финансирования (фандинг)
  /fapi/v1/openInterest  — открытый интерес (OI) в монетах
  /fapi/v1/ticker/24hr   — 24ч статистика фьючерса

Почему Futures ≠ Spot:
  - Mark Price  = индексная цена + funding basis (важна при ликвидации)
  - Last Price  = последняя сделка в фьючерсе
  - Funding Rate — выплачивается каждые 8 часов: положительный → лонги платят
    шортам (рынок «перегрет вверх»), отрицательный → шорты платят лонгам

Торговые сигналы из Futures данных:
  Funding Rate > +0.1%  → рынок перегрет лонгами → осторожно с покупкой
  Funding Rate < -0.05% → рынок перегрет шортами → потенциальный шорт-сквиз
  OI растёт + цена растёт → сильный бычий тренд подтверждён
  OI падает + цена растёт → шорт-сквиз (ненадёжный рост)
  OI растёт + цена падает → сильный медвежий тренд
  OI падает + цена падает → принудительная ликвидация лонгов
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
FUTURES_BASE = "https://fapi.binance.com/fapi/v1"

COIN_TO_SYMBOL: dict[str, str] = {
    "btc":  "BTCUSDT",  "bitcoin":    "BTCUSDT",
    "eth":  "ETHUSDT",  "ethereum":   "ETHUSDT",
    "bnb":  "BNBUSDT",  "binancecoin":"BNBUSDT",
    "sol":  "SOLUSDT",  "solana":     "SOLUSDT",
    "xrp":  "XRPUSDT",  "ripple":     "XRPUSDT",
    "doge": "DOGEUSDT", "dogecoin":   "DOGEUSDT",
    "ada":  "ADAUSDT",  "cardano":    "ADAUSDT",
    "avax": "AVAXUSDT", "avalanche-2":"AVAXUSDT",
    "link": "LINKUSDT", "chainlink":  "LINKUSDT",
    "dot":  "DOTUSDT",  "polkadot":   "DOTUSDT",
    "ton":  "TONUSDT",  "toncoin":    "TONUSDT",  # TON — только Spot!
}

# Монеты не доступные на Futures — используем Binance Spot
SPOT_ONLY_COINS: set[str] = {"ton", "toncoin"}

CACHE_TTL: dict[str, int] = {
    "5m":  30,     # 30 секунд
    "15m": 60,     # 1 минута
    "30m": 120,    # 2 минуты
    "1h":  60,     # 1 минута
    "4h":  240,    # 4 минуты
    "1d":  900,    # 15 минут
}

# Кеш свечей
_klines_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}
# Кеш futures data (funding, OI, mark price)
_futures_cache: dict[str, tuple[float, dict]] = {}
FUTURES_CACHE_TTL = 300  # 5 минут


def resolve_symbol(coin: str) -> str:
    sym = COIN_TO_SYMBOL.get(coin.lower())
    if sym is None:
        sym = coin.upper() + "USDT"
        logger.warning("Futures: неизвестная монета '%s', используем '%s'", coin, sym)
    return sym


# ── Свечи фьючерсов ───────────────────────────────────────────────────────────

def get_klines(
    coin: str,
    interval: str = "1d",
    limit: int = 200,
    use_cache: bool = True,
) -> pd.DataFrame:
    """
    Загружает свечи Binance Futures (fapi.binance.com/fapi/v1/klines).
    Возвращает DataFrame [open, high, low, close, volume].

    Это ТЕ ЖЕ свечи что видишь в Binance Futures Trading — фьючерсная цена.
    Fallback на Spot если фьючерс недоступен.
    """
    if interval not in CACHE_TTL:
        logger.error("Futures: неподдерживаемый интервал: %s", interval)
        return _empty_df()

    symbol = resolve_symbol(coin)
    cache_key = (symbol, interval, limit)

    if use_cache and cache_key in _klines_cache:
        fetched_at, df = _klines_cache[cache_key]
        if time.time() - fetched_at < CACHE_TTL[interval]:
            return df

    # TON и другие SPOT_ONLY монеты — сразу используем Spot API
    if coin.lower() in SPOT_ONLY_COINS:
        try:
            from market_data.binance_rest import get_klines as spot_klines
            df = spot_klines(coin, interval=interval, limit=limit, use_cache=False)
            if not df.empty:
                _klines_cache[cache_key] = (time.time(), df)
            return df
        except Exception as e:
            logger.error("TON spot klines error: %s", e)
            return _empty_df()

    df = _fetch_futures_klines(symbol, interval, limit)

    if df.empty:
        logger.warning("Futures klines пусты для %s %s, fallback на Spot", symbol, interval)
        try:
            from market_data.binance_rest import get_klines as spot_klines
            df = spot_klines(coin, interval=interval, limit=limit, use_cache=False)
        except Exception as e:
            logger.error("Spot fallback: %s", e)

    if not df.empty:
        _klines_cache[cache_key] = (time.time(), df)

    return df


def _fetch_futures_klines(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Загружает свечи с Futures API."""
    for wait in [1, 3, 10]:
        try:
            resp = requests.get(
                f"{FUTURES_BASE}/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit + 1},
                timeout=10,
                headers={"User-Agent": "Sokratanti/2.0"},
            )
            if resp.status_code == 429:
                logger.warning("Futures rate limit, ждём %ds", wait)
                time.sleep(wait)
                continue
            if resp.status_code in (400, 404):
                logger.warning("Futures %s не найден (%d)", symbol, resp.status_code)
                return _empty_df()
            resp.raise_for_status()
            raw = resp.json()
            if not raw:
                return _empty_df()
            df = _parse_klines(raw)
            df = _drop_unclosed(df)
            return df
        except requests.RequestException as e:
            logger.warning("Futures klines error %s: %s", symbol, e)
            time.sleep(wait)
    return _empty_df()


def _parse_klines(raw: list) -> pd.DataFrame:
    records = []
    for row in raw:
        records.append({
            "timestamp":     datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc),
            "open":          float(row[1]),
            "high":          float(row[2]),
            "low":           float(row[3]),
            "close":         float(row[4]),
            "volume":        float(row[5]),
            "close_time_ms": int(row[6]),
        })
    df = pd.DataFrame(records).set_index("timestamp").sort_index()
    return df


def _drop_unclosed(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "close_time_ms" not in df.columns:
        return df
    now_ms = time.time() * 1000
    closed = df[df["close_time_ms"] <= now_ms].copy()
    closed.drop(columns=["close_time_ms"], inplace=True, errors="ignore")
    return closed


def _empty_df() -> pd.DataFrame:
    return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])


# ── Mark Price (цена фьючерса для ликвидации) ─────────────────────────────────

def get_mark_price(coin: str) -> dict:
    """
    Возвращает Mark Price, Index Price, Last Funding Rate.

    Mark Price ≠ Last Price:
      - Mark Price: рассчитан по индексу + basis. По нему идут ликвидации.
      - Last Price: последняя сделка в книге ордеров фьючерса.
      - Index Price: средняя по нескольким биржам (spot reference).

    Returns: {
        mark_price: float,
        index_price: float,
        last_funding_rate: float,  # в долях (0.0001 = 0.01%)
        last_funding_rate_pct: float,  # в процентах
        next_funding_time: int,  # unix ms
    }
    """
    cache_key = f"mark_{coin}"
    if cache_key in _futures_cache:
        fetched_at, data = _futures_cache[cache_key]
        if time.time() - fetched_at < FUTURES_CACHE_TTL:
            return data

    symbol = resolve_symbol(coin)
    try:
        resp = requests.get(
            f"{FUTURES_BASE}/premiumIndex",
            params={"symbol": symbol},
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json()
        rate_raw = float(raw.get("lastFundingRate", 0))
        result = {
            "mark_price":             float(raw.get("markPrice", 0)),
            "index_price":            float(raw.get("indexPrice", 0)),
            "last_funding_rate":      rate_raw,
            "last_funding_rate_pct":  rate_raw * 100,
            "next_funding_time":      int(raw.get("nextFundingTime", 0)),
            "symbol":                 symbol,
        }
        _futures_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.error("get_mark_price %s: %s", coin, e)
        return {"mark_price": 0, "index_price": 0, "last_funding_rate": 0,
                "last_funding_rate_pct": 0, "next_funding_time": 0, "symbol": symbol}


# ── Funding Rate ──────────────────────────────────────────────────────────────

def get_funding_rate(coin: str, limit: int = 8) -> dict:
    """
    Возвращает историю ставок финансирования + анализ.

    Funding Rate выплачивается каждые 8 часов (00:00, 08:00, 16:00 UTC).
    Положительный фандинг = лонги платят шортам (рынок перегрет вверх).
    Отрицательный фандинг = шорты платят лонгам (рынок перегрет вниз).

    Пороги:
      > +0.10% за 8ч  = сильный «перегрев» лонгов → осторожно с покупкой
      > +0.05% за 8ч  = умеренное давление лонгов
      < -0.05% за 8ч  = шорты перегреты → потенциальный шорт-сквиз
      ≈ 0%            = нейтральный рынок

    Returns: {
        current_rate_pct: float,
        avg_rate_pct: float,   # среднее за limit ставок
        signal: str,           # 'overheated_longs' / 'short_squeeze' / 'neutral'
        signal_ru: str,
        rates: list[float],    # последние limit ставок в %
        annualized_pct: float, # годовой эквивалент (rate * 3 * 365)
    }
    """
    cache_key = f"funding_{coin}"
    if cache_key in _futures_cache:
        fetched_at, data = _futures_cache[cache_key]
        if time.time() - fetched_at < FUTURES_CACHE_TTL:
            return data

    symbol = resolve_symbol(coin)
    try:
        resp = requests.get(
            f"{FUTURES_BASE}/fundingRate",
            params={"symbol": symbol, "limit": limit},
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json()

        rates_pct = [float(r["fundingRate"]) * 100 for r in raw]
        if not rates_pct:
            return _empty_funding()

        current = rates_pct[-1]
        avg = sum(rates_pct) / len(rates_pct)

        # Торговый сигнал из фандинга
        if current > 0.10:
            signal = "overheated_longs"
            signal_ru = f"🔴 Лонги перегреты ({current:+.3f}%) — осторожно с покупкой"
        elif current > 0.05:
            signal = "longs_dominant"
            signal_ru = f"🟡 Лонги доминируют ({current:+.3f}%) — умеренный риск"
        elif current < -0.05:
            signal = "short_squeeze_risk"
            signal_ru = f"🟢 Шорты перегреты ({current:+.3f}%) — возможен шорт-сквиз"
        elif current < -0.02:
            signal = "shorts_dominant"
            signal_ru = f"🟡 Шорты доминируют ({current:+.3f}%) — умеренный позитив"
        else:
            signal = "neutral"
            signal_ru = f"⚪ Фандинг нейтральный ({current:+.3f}%)"

        result = {
            "current_rate_pct": current,
            "avg_rate_pct":     avg,
            "signal":           signal,
            "signal_ru":        signal_ru,
            "rates":            rates_pct,
            "annualized_pct":   avg * 3 * 365,  # 3 выплаты/день * 365 дней
            "symbol":           symbol,
        }
        _futures_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.error("get_funding_rate %s: %s", coin, e)
        return _empty_funding()


def _empty_funding() -> dict:
    return {"current_rate_pct": 0, "avg_rate_pct": 0, "signal": "neutral",
            "signal_ru": "⚪ Фандинг: нет данных", "rates": [],
            "annualized_pct": 0, "symbol": ""}


# ── Open Interest ─────────────────────────────────────────────────────────────

def get_open_interest(coin: str) -> dict:
    """
    Возвращает открытый интерес (OI) по фьючерсу.

    Open Interest = суммарное кол-во открытых контрактов.
    Интерпретация в комбинации с ценой:
      OI↑ + Цена↑ = сильный тренд подтверждён новыми лонгами
      OI↑ + Цена↓ = сильный медвежий тренд подтверждён новыми шортами
      OI↓ + Цена↑ = шорт-сквиз (закрытие шортов толкает цену вверх)
      OI↓ + Цена↓ = закрытие лонгов (слабый падающий рынок)

    Returns: {
        open_interest: float,   # в монетах
        open_interest_usdt: float,  # в USDT (если доступно)
        symbol: str,
    }
    """
    cache_key = f"oi_{coin}"
    if cache_key in _futures_cache:
        fetched_at, data = _futures_cache[cache_key]
        if time.time() - fetched_at < FUTURES_CACHE_TTL:
            return data

    symbol = resolve_symbol(coin)
    try:
        resp = requests.get(
            f"{FUTURES_BASE}/openInterest",
            params={"symbol": symbol},
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json()
        oi = float(raw.get("openInterest", 0))

        # Получаем цену чтобы посчитать USDT
        mark = get_mark_price(coin)
        oi_usdt = oi * mark.get("mark_price", 0)

        result = {
            "open_interest":      oi,
            "open_interest_usdt": oi_usdt,
            "symbol":             symbol,
        }
        _futures_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.error("get_open_interest %s: %s", coin, e)
        return {"open_interest": 0, "open_interest_usdt": 0, "symbol": symbol}


# ── 24h Ticker фьючерса ───────────────────────────────────────────────────────

def get_futures_ticker(coin: str) -> dict:
    """
    Возвращает 24h статистику Futures тикера.

    Returns: {
        last_price: float,
        mark_price: float,
        price_change_pct: float,
        high: float,
        low: float,
        volume: float,     # в монетах
        volume_usdt: float,  # в USDT
        count: int,          # кол-во сделок
    }
    """
    cache_key = f"ticker_{coin}"
    if cache_key in _futures_cache:
        fetched_at, data = _futures_cache[cache_key]
        if time.time() - fetched_at < 60:  # 1 мин для тикера
            return data

    symbol = resolve_symbol(coin)
    try:
        resp = requests.get(
            f"{FUTURES_BASE}/ticker/24hr",
            params={"symbol": symbol},
            timeout=8,
        )
        resp.raise_for_status()
        raw = resp.json()

        # Mark price отдельным запросом
        mark_data = get_mark_price(coin)

        high_val = float(raw.get("highPrice", 0))
        low_val  = float(raw.get("lowPrice", 0))
        vol_coin = float(raw.get("volume", 0))
        vol_usdt = float(raw.get("quoteVolume", 0))

        result = {
            "last_price":        float(raw.get("lastPrice", 0)),
            "mark_price":        mark_data.get("mark_price", 0),
            "price_change_pct":  float(raw.get("priceChangePercent", 0)),
            "high":              high_val,
            "high_24h":          high_val,
            "low":               low_val,
            "low_24h":           low_val,
            "volume":            vol_coin,
            "volume_24h":        vol_coin,
            "volume_usdt":       vol_usdt,
            "count":             int(raw.get("count", 0)),
            "last_funding_rate_pct": mark_data.get("last_funding_rate_pct", 0),
            "symbol":            symbol,
        }
        _futures_cache[cache_key] = (time.time(), result)
        return result
    except Exception as e:
        logger.error("get_futures_ticker %s: %s", coin, e)
        return {"last_price": 0, "mark_price": 0, "price_change_pct": 0,
                "high": 0, "high_24h": 0, "low": 0, "low_24h": 0,
                "volume": 0, "volume_24h": 0, "volume_usdt": 0,
                "count": 0, "last_funding_rate_pct": 0, "symbol": symbol}


# ── Комплексный анализ фьючерсного рынка ─────────────────────────────────────

def get_futures_context(coin: str) -> dict:
    """
    Собирает полный контекст фьючерсного рынка для торгового решения.

    Включает: mark price, funding rate, open interest, 24h ticker.
    Возвращает готовый signal_score_adj (-15..+10) для добавления к setup_score.

    Логика корректировки:
      Funding перегрет (>0.1%)  → -10 к score (не лучший момент для лонга)
      Funding умеренный (>0.05%) → -5 к score
      Funding шорт-сквиз (<-0.05%) → +8 к score (хороший лонг-сетап)
      Funding нейтральный        → 0

    Returns: {
        ticker, funding, mark_price, open_interest,
        signal_score_adj: int,   # коррекция к setup_score
        summary_ru: str,         # краткое описание для сообщения
    }
    """
    ticker   = get_futures_ticker(coin)
    funding  = get_funding_rate(coin)
    mark     = get_mark_price(coin)
    oi       = get_open_interest(coin)

    score_adj = 0
    notes     = []

    # Фандинг корректировка
    fr = funding["current_rate_pct"]
    if fr > 0.10:
        score_adj -= 10
        notes.append(f"⚠️ Фандинг {fr:+.3f}% — перегрев лонгов")
    elif fr > 0.05:
        score_adj -= 5
        notes.append(f"🟡 Фандинг {fr:+.3f}% — лонги доминируют")
    elif fr < -0.05:
        score_adj += 8
        notes.append(f"🟢 Фандинг {fr:+.3f}% — шорты перегреты (шорт-сквиз риск)")
    elif fr < -0.02:
        score_adj += 3
        notes.append(f"🟡 Фандинг {fr:+.3f}% — шорты умеренно доминируют")
    else:
        notes.append(f"⚪ Фандинг {fr:+.3f}% — нейтральный")

    # Mark price vs Last price: если mark выше last > 0.1% → предупреждение
    mp = mark.get("mark_price", 0)
    lp = ticker.get("last_price", mp)
    if mp > 0 and lp > 0:
        basis_pct = (mp - lp) / lp * 100
        if abs(basis_pct) > 0.1:
            notes.append(f"📐 Basis: Mark/Last = {basis_pct:+.2f}%")

    summary_ru = " | ".join(notes) if notes else "Нет данных по фьючерсу"

    return {
        "ticker":          ticker,
        "funding":         funding,
        "mark_price":      mark,
        "open_interest":   oi,
        "signal_score_adj": score_adj,
        "summary_ru":      summary_ru,
        "notes":           notes,
    }


def healthcheck() -> dict:
    """Проверяет доступность Binance Futures API."""
    try:
        resp = requests.get(f"{FUTURES_BASE}/ping", timeout=5)
        return {"status": "ok", "latency_ms": round(resp.elapsed.total_seconds() * 1000)}
    except Exception as e:
        return {"status": "error", "error": str(e)}
