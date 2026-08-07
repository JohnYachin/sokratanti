"""
CAIOS News Fetcher — Architecture Section 5: Data Sources
Fetches crypto news headlines from CryptoCompare (free, no API key required).
Provides sentiment scoring and formatted summaries for AI agent prompts.
"""
import asyncio, time
from datetime import datetime, timezone

import httpx

NEWS_API = "https://min-api.cryptocompare.com/data/v2/news/"

COIN_CATEGORIES = {
    "BTC":  "BTC,Bitcoin",
    "ETH":  "ETH,Ethereum",
    "BNB":  "BNB",
    "SOL":  "SOL,Solana",
    "XRP":  "XRP,Ripple",
    "ADA":  "ADA,Cardano",
    "AVAX": "AVAX,Avalanche",
    "DOT":  "DOT,Polkadot",
    "MATIC":"MATIC,Polygon",
    "LINK": "LINK,Chainlink",
}

# Keywords for basic sentiment scoring
BULLISH_WORDS = {
    "surge", "rally", "breakout", "bullish", "soar", "rise", "gain", "high",
    "adoption", "partnership", "launch", "upgrade", "milestone", "record",
    "institutional", "inflow", "buy", "accumulate", "moon", "pump", "upward",
}
BEARISH_WORDS = {
    "crash", "fall", "drop", "bearish", "plunge", "decline", "loss", "low",
    "hack", "exploit", "ban", "regulation", "lawsuit", "sell", "dump",
    "outflow", "fear", "concern", "warning", "risk", "downward", "correction",
}


def score_headline(title: str, body: str = "") -> float:
    """
    Simple keyword-based sentiment score.
    Returns -1.0 (very bearish) to +1.0 (very bullish).
    """
    text = (title + " " + body).lower()
    words = set(text.split())
    bull = len(words & BULLISH_WORDS)
    bear = len(words & BEARISH_WORDS)
    total = bull + bear
    if total == 0:
        return 0.0
    return round((bull - bear) / total, 3)


async def fetch_news(symbol: str, limit: int = 5) -> list[dict]:
    """
    Fetch latest news for a coin symbol.
    Returns list of {title, source, url, published_at, sentiment_score}.
    """
    categories = COIN_CATEGORIES.get(symbol.upper(), symbol)
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                NEWS_API,
                params={"lang": "EN", "categories": categories, "sortOrder": "latest"}
            )
        items = r.json().get("Data", [])
        result = []
        for item in items[:limit]:
            ts = item.get("published_on", 0)
            published = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M %d.%m") if ts else "?"
            score = score_headline(item.get("title", ""), item.get("body", "")[:200])
            result.append({
                "title":           item.get("title", ""),
                "source":          item.get("source_info", {}).get("name", item.get("source", "?")),
                "url":             item.get("url", ""),
                "published_at":    published,
                "sentiment_score": score,
            })
        return result
    except Exception as e:
        return []


async def fetch_market_news(limit: int = 5) -> list[dict]:
    """Fetch general crypto market news."""
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                NEWS_API,
                params={"lang": "EN", "categories": "Market,Trading,Bitcoin,Ethereum", "sortOrder": "latest"}
            )
        items = r.json().get("Data", [])
        result = []
        for item in items[:limit]:
            ts  = item.get("published_on", 0)
            pub = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M %d.%m") if ts else "?"
            result.append({
                "title":        item.get("title", ""),
                "source":       item.get("source_info", {}).get("name", "?"),
                "url":          item.get("url", ""),
                "published_at": pub,
                "sentiment_score": score_headline(item.get("title", ""), item.get("body", "")[:200]),
            })
        return result
    except Exception:
        return []


def calc_news_sentiment(news: list[dict]) -> dict:
    """
    Aggregate sentiment across news list.
    Returns {score, label, bull_count, bear_count, neutral_count}.
    """
    if not news:
        return {"score": 0.0, "label": "Neutral", "bull_count": 0, "bear_count": 0, "neutral_count": 0}

    scores     = [n["sentiment_score"] for n in news]
    avg_score  = sum(scores) / len(scores)
    bull_count = sum(1 for s in scores if s > 0.1)
    bear_count = sum(1 for s in scores if s < -0.1)
    neut_count = len(scores) - bull_count - bear_count

    if avg_score >= 0.2:   label = "Bullish 🟢"
    elif avg_score >= 0.05: label = "Slightly Bullish 🟡"
    elif avg_score <= -0.2: label = "Bearish 🔴"
    elif avg_score <= -0.05:label = "Slightly Bearish 🟡"
    else:                   label = "Neutral ⬜"

    return {
        "score":         round(avg_score, 3),
        "label":         label,
        "bull_count":    bull_count,
        "bear_count":    bear_count,
        "neutral_count": neut_count,
    }


def format_news_for_prompt(news: list[dict], symbol: str) -> str:
    """Format news for AI agent prompt."""
    if not news:
        return f"Recent {symbol} news: not available"

    sentiment = calc_news_sentiment(news)
    lines = [f"📰 Recent {symbol} News (sentiment: {sentiment['label']}):"]
    for n in news[:4]:
        s = n["sentiment_score"]
        icon = "🟢" if s > 0.1 else "🔴" if s < -0.1 else "⬜"
        lines.append(f"  {icon} [{n['published_at']}] {n['title'][:80]}")
    return "\n".join(lines)
