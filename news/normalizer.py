"""
news/normalizer.py — Нормализованная модель новости.

NewsItem — единый формат для всех источников (RSS, CryptoPanic, Reddit).
Содержит: keyword-based sentiment, определение типа события, хэш для дедупликации.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ── Тип события ───────────────────────────────────────────────────────────────
EVENT_TYPES = {
    "hack":        ["hack", "hacked", "exploit", "breach", "stolen", "theft", "vulnerability"],
    "delisting":   ["delist", "delisted", "removed from", "trading suspended"],
    "listing":     ["listed on", "listing on", "now available on", "launches on", "added to"],
    "regulation":  ["sec ", "cftc", "regulation", "regulated", "ban", "banned", "illegal",
                    "lawsuit", "legal action", "court", "fine"],
    "etf":         ["etf", "spot etf", "etf approval", "blackrock", "grayscale"],
    "partnership": ["partnership", "integration", "collaborat", "announces deal"],
    "upgrade":     ["upgrade", "update", "mainnet", "fork", "protocol"],
    "macro":       ["fed", "federal reserve", "inflation", "interest rate", "recession",
                    "gdp", "cpi", "ppi"],
    "whale":       ["whale", "large transaction", "moved", "transferred millions"],
    "unlock":      ["token unlock", "vesting", "cliff", "unlocked"],
    "outage":      ["outage", "down", "offline", "unavailable", "maintenance"],
}

# ── Sentiment keywords ─────────────────────────────────────────────────────────
_BULLISH = [
    "surge", "soar", "rally", "bull", "bullish", "ath", "all-time high", "breakout",
    "gain", "rise", "rising", "record", "milestone", "adoption", "institutional",
    "accumulate", "buy", "long", "pump", "moon", "approval", "partnership", "launch",
    "growth", "positive", "recovery", "rebound", "strong", "demand", "inflow",
]
_BEARISH = [
    "crash", "dump", "drop", "fall", "bear", "bearish", "decline", "plunge",
    "loss", "sell", "short", "fear", "panic", "regulation", "ban", "hack",
    "exploit", "breach", "outage", "lawsuit", "fine", "correction", "weak",
    "outflow", "concern", "warning", "risk", "delist", "fraud", "scam",
]

# Сильные события (блокируют BUY сигнал до проверки)
CRITICAL_EVENTS = {"hack", "delisting", "outage"}


@dataclass
class NewsItem:
    title: str
    url: str
    source: str
    published_at: datetime            # UTC
    symbols: list[str] = field(default_factory=list)
    summary: str = ""
    language: str = "en"
    sentiment_score: float = 0.0      # -1..+1 (keyword-based)
    event_type: str = "other"
    is_critical: bool = False         # hack/delist/outage
    raw_hash: str = ""                # sha256(url)[:16] для дедупликации

    def __post_init__(self) -> None:
        if not self.raw_hash:
            self.raw_hash = hashlib.sha256(self.url.encode()).hexdigest()[:16]
        if not self.sentiment_score:
            self.sentiment_score = _calc_sentiment(self.title + " " + self.summary)
        if self.event_type == "other":
            self.event_type = _detect_event_type(self.title)
        self.is_critical = self.event_type in CRITICAL_EVENTS


def _calc_sentiment(text: str) -> float:
    """Keyword-based sentiment от -1.0 до +1.0."""
    text_l = text.lower()
    bull = sum(1 for w in _BULLISH if w in text_l)
    bear = sum(1 for w in _BEARISH if w in text_l)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 3)


def _detect_event_type(title: str) -> str:
    """Определяет тип события по заголовку."""
    title_l = title.lower()
    for event, keywords in EVENT_TYPES.items():
        if any(kw in title_l for kw in keywords):
            return event
    return "other"


# ── Утилиты ───────────────────────────────────────────────────────────────────

def extract_symbols(text: str, known: list[str] | None = None) -> list[str]:
    """Находит упоминания монет в тексте."""
    if known is None:
        known = ["BTC", "ETH", "SOL", "BNB", "DOGE", "XRP", "ADA", "AVAX",
                 "Bitcoin", "Ethereum", "Solana", "Binance", "Dogecoin"]
    text_upper = text.upper()
    found = set()
    for sym in known:
        if sym.upper() in text_upper:
            # Нормализуем к тикеру
            mapping = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
                       "BINANCE": "BNB", "DOGECOIN": "DOGE"}
            found.add(mapping.get(sym.upper(), sym.upper()))
    return sorted(found)


def deduplicate(items: list[NewsItem]) -> list[NewsItem]:
    """Убирает дубли по raw_hash."""
    seen: set[str] = set()
    result = []
    for item in items:
        if item.raw_hash not in seen:
            seen.add(item.raw_hash)
            result.append(item)
    return result


def filter_stale(items: list[NewsItem], max_age_hours: int = 48) -> list[NewsItem]:
    """Убирает новости старше max_age_hours."""
    now = datetime.now(timezone.utc)
    cutoff = max_age_hours * 3600
    return [
        item for item in items
        if (now - item.published_at).total_seconds() < cutoff
    ]


def calc_news_score(items: list[NewsItem], symbol: str | None = None) -> float:
    """
    Агрегированный sentiment score для монеты/рынка.
    Возвращает -1.0..+1.0. Критические события тянут вниз сильнее.
    """
    relevant = [
        i for i in items
        if symbol is None or (symbol.upper() in i.symbols or not i.symbols)
    ]
    if not relevant:
        return 0.0

    total = 0.0
    weight_sum = 0.0
    for item in relevant[:15]:  # берём максимум 15 новостей
        w = 2.0 if item.is_critical else 1.0
        total += item.sentiment_score * w
        weight_sum += w

    raw = total / weight_sum if weight_sum else 0.0
    return round(max(-1.0, min(1.0, raw)), 3)
