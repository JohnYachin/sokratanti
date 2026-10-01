"""
news/rss.py — RSS агрегатор крипто-новостей.

Бесплатные источники без API-ключей:
  - CoinDesk
  - CoinTelegraph
  - Decrypt
  - The Block
  - Bitcoin Magazine
  - Binance Blog (для BNB)

Использует stdlib xml.etree.ElementTree — нет новых зависимостей.
Каждая функция gracefully возвращает [] при недоступности источника.
"""
from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Optional

import requests

from news.normalizer import NewsItem, extract_symbols, deduplicate, filter_stale

logger = logging.getLogger(__name__)

# ── RSS источники ──────────────────────────────────────────────────────────────
RSS_SOURCES: list[dict] = [
    {
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "reliability": 0.9,
    },
    {
        "name": "CoinTelegraph",
        "url": "https://cointelegraph.com/rss",
        "reliability": 0.85,
    },
    {
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "reliability": 0.8,
    },
    {
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
        "reliability": 0.85,
    },
    {
        "name": "Bitcoin Magazine",
        "url": "https://bitcoinmagazine.com/feed",
        "reliability": 0.75,
    },
    {
        "name": "Binance Blog",
        "url": "https://www.binance.com/en/blog/rss",
        "reliability": 0.7,
    },
]

# Простой in-memory кеш: {source_url: (fetched_at, items)}
_cache: dict[str, tuple[float, list[NewsItem]]] = {}
CACHE_TTL = 1800  # 30 минут (было 5 — слишком часто)


def _parse_date(date_str: str | None) -> datetime:
    """Парсим RFC 2822 дату в UTC datetime."""
    if not date_str:
        return datetime.now(timezone.utc)
    try:
        return parsedate_to_datetime(date_str).astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _fetch_rss(source: dict, timeout: int = 8) -> list[NewsItem]:
    """Загружает и парсит один RSS фид. Возвращает [] при любой ошибке."""
    url = source["url"]
    name = source["name"]

    # Проверяем кеш
    cached = _cache.get(url)
    if cached:
        fetched_at, items = cached
        if time.time() - fetched_at < CACHE_TTL:
            return items

    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Sokratanti-Bot/2.0 (crypto analytics)"},
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("RSS fetch error [%s]: %s", name, e)
        return []

    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        # Пробуем lxml с режимом восстановления (обрабатывает сломанный HTML/XML)
        try:
            from lxml import etree as lxml_et
            parser = lxml_et.XMLParser(recover=True, encoding="utf-8")
            lxml_root = lxml_et.fromstring(resp.content, parser=parser)
            # Конвертируем обратно в ET для дальнейшей обработки
            root = ET.fromstring(lxml_et.tostring(lxml_root, encoding="unicode").encode("utf-8"))
        except Exception:
            # lxml недоступен или тоже не смог — пробуем с очисткой
            try:
                import re as _re
                clean = _re.sub(r'&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)', '&amp;', resp.text)
                root = ET.fromstring(clean.encode("utf-8"))
            except Exception as e2:
                logger.debug("RSS parse skip [%s]: %s", name, e2)
                return []

    items: list[NewsItem] = []

    # Поддерживаем RSS 2.0 и Atom
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    # RSS 2.0: channel/item
    for item_el in root.findall(".//item"):
        title = (item_el.findtext("title") or "").strip()
        link  = (item_el.findtext("link") or "").strip()
        pub   = item_el.findtext("pubDate")
        desc  = (item_el.findtext("description") or "").strip()

        if not title or not link:
            continue

        # Убираем HTML теги из описания
        desc_clean = ET.fromstring(f"<x>{desc}</x>").text or "" if "<" in desc else desc

        published = _parse_date(pub)
        symbols = extract_symbols(title + " " + desc_clean)

        items.append(NewsItem(
            title=title,
            url=link,
            source=name,
            published_at=published,
            symbols=symbols,
            summary=desc_clean[:300],
        ))

    # Atom: entry
    for entry in root.findall(".//atom:entry", ns):
        title = (entry.findtext("atom:title", namespaces=ns) or "").strip()
        link_el = entry.find("atom:link", ns)
        link = (link_el.get("href", "") if link_el is not None else "").strip()
        pub  = entry.findtext("atom:published", namespaces=ns)
        summary = (entry.findtext("atom:summary", namespaces=ns) or "").strip()

        if not title or not link:
            continue

        published = _parse_date(pub)
        symbols = extract_symbols(title + " " + summary)

        items.append(NewsItem(
            title=title,
            url=link,
            source=name,
            published_at=published,
            symbols=symbols,
            summary=summary[:300],
        ))

    _cache[url] = (time.time(), items)
    logger.debug("RSS [%s]: получено %d новостей", name, len(items))
    return items


def get_all_news(max_age_hours: int = 48, limit: int = 50) -> list[NewsItem]:
    """
    Агрегирует новости со всех RSS источников.
    Дедуплицирует и фильтрует устаревшие. Возвращает [] при полном отказе.
    """
    all_items: list[NewsItem] = []

    for source in RSS_SOURCES:
        items = _fetch_rss(source)
        all_items.extend(items)

    # Дедупликация и фильтр по дате
    all_items = deduplicate(all_items)
    all_items = filter_stale(all_items, max_age_hours=max_age_hours)

    # Сортируем по дате (свежие первые)
    all_items.sort(key=lambda x: x.published_at, reverse=True)

    return all_items[:limit]


def get_news_for_coin(coin: str, limit: int = 10,
                      max_age_hours: int = 48) -> list[NewsItem]:
    """
    Новости для конкретной монеты.
    Сначала берём точные совпадения (symbols), потом общие крипто-новости.
    """
    symbol = coin.upper()
    # Нормализуем тикер → символ
    _alias = {"BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
               "BINANCECOIN": "BNB", "DOGECOIN": "DOGE"}
    symbol = _alias.get(symbol, symbol)

    all_items = get_all_news(max_age_hours=max_age_hours, limit=200)

    # Точное совпадение по символу
    exact = [i for i in all_items if symbol in i.symbols]
    # Общие новости (без привязки к символу)
    general = [i for i in all_items if not i.symbols]

    # Объединяем: сначала точные, потом общие
    combined = exact + [i for i in general if i not in exact]
    return combined[:limit]
