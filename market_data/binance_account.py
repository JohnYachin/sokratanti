"""
market_data/binance_account.py — Binance Futures Account API (Read-Only)

Использует HMAC-SHA256 подписанные запросы к /fapi/v2/account,
/fapi/v1/positionRisk, /fapi/v1/userTrades, /fapi/v1/income.

Требует: BINANCE_API_KEY, BINANCE_API_SECRET в .env
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)

# Загружаем .env если ещё не загружен
try:
    from dotenv import load_dotenv as _ld
    _ld(override=False)
except ImportError:
    pass

BASE = "https://fapi.binance.com"
TIMEOUT = 10


def _sign(params: dict, secret: str) -> str:
    query = urlencode(params)
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _headers() -> dict:
    key = os.getenv("BINANCE_API_KEY", "")
    if not key:
        raise EnvironmentError("BINANCE_API_KEY не задан в .env")
    return {"X-MBX-APIKEY": key}


def _get(path: str, params: dict | None = None) -> dict | list:
    secret = os.getenv("BINANCE_API_SECRET", "")
    if not secret:
        raise EnvironmentError("BINANCE_API_SECRET не задан в .env")

    p = dict(params or {})
    p["timestamp"] = int(time.time() * 1000)
    p["recvWindow"] = 5000
    p["signature"] = _sign(p, secret)

    r = requests.get(f"{BASE}{path}", params=p, headers=_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ── Баланс ────────────────────────────────────────────────────────────────────

def get_balance() -> dict:
    """
    Возвращает USDT баланс Futures аккаунта.
    {
        "available": 2450.30,   # доступно
        "wallet":    2847.00,   # всего в кошельке
        "unrealized_pnl": 312.50,
    }
    """
    data = _get("/fapi/v2/balance")
    for asset in data:
        if asset.get("asset") == "USDT":
            return {
                "available":      round(float(asset.get("availableBalance", 0)), 2),
                "wallet":         round(float(asset.get("balance", 0)), 2),
                "unrealized_pnl": round(float(asset.get("crossUnPnl", 0)), 2),
            }
    return {"available": 0.0, "wallet": 0.0, "unrealized_pnl": 0.0}


# ── Открытые позиции ──────────────────────────────────────────────────────────

def get_open_positions() -> list[dict]:
    """
    Возвращает список открытых Futures позиций с ненулевым размером.
    Каждый элемент:
    {
        "coin":         "BTC",
        "symbol":       "BTCUSDT",
        "side":         "LONG" | "SHORT",
        "size":         0.01,
        "entry_price":  63500.0,
        "mark_price":   64200.0,
        "leverage":     3,
        "unrealized_pnl": 21.0,
        "pnl_pct":      3.3,
        "liq_price":    58240.0,
        "margin":       211.67,
    }
    """
    data = _get("/fapi/v2/positionRisk")
    positions = []
    for p in data:
        amt = float(p.get("positionAmt", 0))
        if amt == 0:
            continue
        symbol     = p["symbol"]
        coin       = symbol.replace("USDT", "").replace("BUSD", "").lower()
        entry      = float(p.get("entryPrice", 0))
        mark       = float(p.get("markPrice", 0))
        upnl       = float(p.get("unRealizedProfit", 0))
        lev        = int(float(p.get("leverage", 1)))
        liq        = float(p.get("liquidationPrice", 0))
        size       = abs(amt)
        margin     = (size * entry) / lev if lev > 0 else 0
        pnl_pct    = (upnl / margin * 100) if margin > 0 else 0

        positions.append({
            "coin":            coin,
            "symbol":          symbol,
            "side":            "LONG" if amt > 0 else "SHORT",
            "size":            round(size, 6),
            "entry_price":     round(entry, 4),
            "mark_price":      round(mark, 4),
            "leverage":        lev,
            "unrealized_pnl":  round(upnl, 2),
            "pnl_pct":         round(pnl_pct, 2),
            "liq_price":       round(liq, 4),
            "margin":          round(margin, 2),
        })
    return sorted(positions, key=lambda x: abs(x["unrealized_pnl"]), reverse=True)


# ── История сделок ────────────────────────────────────────────────────────────

def get_trade_history(coin: str, days: int = 90, limit: int = 500) -> list[dict]:
    """
    История завершённых сделок по монете за последние N дней.
    Каждый элемент:
    {
        "coin":       "btc",
        "symbol":     "BTCUSDT",
        "side":       "BUY" | "SELL",
        "price":      63500.0,
        "qty":        0.01,
        "realized_pnl": 12.5,
        "commission": 0.63,
        "time_ms":    1695000000000,
        "time_str":   "2026-09-18 14:30",
    }
    """
    symbol = f"{coin.upper()}USDT"
    start_ms = int((time.time() - days * 86400) * 1000)

    data = _get("/fapi/v1/userTrades", {
        "symbol":    symbol,
        "startTime": start_ms,
        "limit":     limit,
    })

    trades = []
    for t in data:
        import datetime
        ts = int(t["time"])
        dt = datetime.datetime.utcfromtimestamp(ts / 1000)
        trades.append({
            "coin":         coin.lower(),
            "symbol":       symbol,
            "side":         t.get("side", ""),
            "price":        float(t.get("price", 0)),
            "qty":          float(t.get("qty", 0)),
            "realized_pnl": float(t.get("realizedPnl", 0)),
            "commission":   float(t.get("commission", 0)),
            "time_ms":      ts,
            "time_str":     dt.strftime("%Y-%m-%d %H:%M"),
        })
    return trades


# ── Income (реализованный P&L) ────────────────────────────────────────────────

def get_income_history(days: int = 30, income_type: str = "REALIZED_PNL") -> list[dict]:
    """
    История реализованного P&L (закрытые позиции).
    income_type: "REALIZED_PNL" | "FUNDING_FEE" | "COMMISSION"

    Возвращает список:
    {
        "symbol":   "BTCUSDT",
        "coin":     "btc",
        "income":   45.30,    # > 0 прибыль, < 0 убыток
        "time_str": "2026-09-15 10:23",
        "time_ms":  1694768580000,
    }
    """
    start_ms = int((time.time() - days * 86400) * 1000)
    data = _get("/fapi/v1/income", {
        "incomeType": income_type,
        "startTime":  start_ms,
        "limit":      1000,
    })

    result = []
    for item in data:
        import datetime
        ts  = int(item["time"])
        dt  = datetime.datetime.utcfromtimestamp(ts / 1000)
        sym = item.get("symbol", "")
        coin = sym.replace("USDT", "").replace("BUSD", "").lower() if sym else ""
        result.append({
            "symbol":   sym,
            "coin":     coin,
            "income":   float(item.get("income", 0)),
            "time_str": dt.strftime("%Y-%m-%d %H:%M"),
            "time_ms":  ts,
        })
    return result


# ── Проверка подключения ──────────────────────────────────────────────────────

def check_api_connection() -> tuple[bool, str]:
    """
    Проверяет что API ключ настроен и работает.
    Возвращает (ok, message).
    """
    try:
        bal = get_balance()
        return True, f"✅ Binance API подключён. Баланс: ${bal['wallet']:.2f} USDT"
    except EnvironmentError as e:
        return False, f"❌ {e}"
    except requests.HTTPError as e:
        code = e.response.status_code if e.response else "?"
        if code == 401:
            return False, "❌ Неверный API ключ (401)"
        return False, f"❌ Binance API ошибка: {code}"
    except Exception as e:
        return False, f"❌ Ошибка: {e}"
