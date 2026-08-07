"""
CAIOS Phase 3 — Technical Indicators
Calculates RSI, MACD, Bollinger Bands, EMA from CoinGecko historical data.
Pure Python — no pandas/numpy/ta required.
"""
import math
import statistics
from typing import Optional


# ─── MATH HELPERS ────────────────────────────────────────

def ema(prices: list[float], period: int) -> list[float]:
    """Exponential Moving Average."""
    if len(prices) < period:
        return []
    k = 2 / (period + 1)
    result = [sum(prices[:period]) / period]  # seed with SMA
    for p in prices[period:]:
        result.append(p * k + result[-1] * (1 - k))
    return result


def sma(prices: list[float], period: int) -> list[float]:
    """Simple Moving Average."""
    return [sum(prices[i:i+period]) / period for i in range(len(prices) - period + 1)]


def calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """RSI(14). Returns 0–100 or None if not enough data."""
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [abs(min(d, 0)) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_macd(closes: list[float], fast=12, slow=26, signal=9) -> Optional[dict]:
    """MACD(12,26,9). Returns {line, signal, histogram} or None."""
    if len(closes) < slow + signal:
        return None
    ema_fast = ema(closes, fast)
    ema_slow = ema(closes, slow)

    # Align lengths
    diff = len(ema_fast) - len(ema_slow)
    ema_fast = ema_fast[diff:]

    macd_line = [f - s for f, s in zip(ema_fast, ema_slow)]
    signal_line = ema(macd_line, signal)

    diff2 = len(macd_line) - len(signal_line)
    histogram = [m - s for m, s in zip(macd_line[diff2:], signal_line)]

    return {
        "line":      round(macd_line[-1], 6),
        "signal":    round(signal_line[-1], 6),
        "histogram": round(histogram[-1], 6),
    }


def calc_bollinger(closes: list[float], period=20, std_mult=2.0) -> Optional[dict]:
    """Bollinger Bands(20,2). Returns {upper, middle, lower, position}."""
    if len(closes) < period:
        return None
    window = closes[-period:]
    middle = sum(window) / period
    std    = statistics.stdev(window)
    upper  = middle + std_mult * std
    lower  = middle - std_mult * std
    price  = closes[-1]
    band_width = upper - lower
    position = (price - lower) / band_width if band_width > 0 else 0.5

    return {
        "upper":    round(upper, 6),
        "middle":   round(middle, 6),
        "lower":    round(lower, 6),
        "position": round(max(0.0, min(1.0, position)), 4),
    }


def calc_ema_values(closes: list[float]) -> dict:
    """Returns latest EMA-20 and EMA-50."""
    result = {}
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if e20:
        result["ema_20"] = round(e20[-1], 6)
    if e50:
        result["ema_50"] = round(e50[-1], 6)
    return result


def calc_volume_ratio(volumes: list[float], period=20) -> Optional[float]:
    """Current volume vs N-day average."""
    if len(volumes) < period + 1:
        return None
    avg = sum(volumes[-period-1:-1]) / period
    if avg == 0:
        return None
    return round(volumes[-1] / avg, 3)


def determine_trend(rsi, macd, bb_pos, ema_20, ema_50, price) -> str:
    """Simple rule-based trend signal."""
    bullish = 0
    bearish = 0

    if rsi is not None:
        if rsi < 30:
            bullish += 2  # Oversold
        elif rsi < 45:
            bullish += 1
        elif rsi > 70:
            bearish += 2  # Overbought
        elif rsi > 55:
            bearish += 1

    if macd is not None:
        if macd["histogram"] > 0 and macd["line"] > macd["signal"]:
            bullish += 1
        elif macd["histogram"] < 0 and macd["line"] < macd["signal"]:
            bearish += 1

    if bb_pos is not None:
        if bb_pos < 0.2:
            bullish += 1  # Near lower band
        elif bb_pos > 0.8:
            bearish += 1  # Near upper band

    if ema_20 and ema_50 and price:
        if price > ema_20 > ema_50:
            bullish += 1  # Price above both EMAs, bullish alignment
        elif price < ema_20 < ema_50:
            bearish += 1  # Price below both EMAs, bearish

    if bullish > bearish + 1:
        return "BULLISH"
    elif bearish > bullish + 1:
        return "BEARISH"
    return "NEUTRAL"


def compute_all(closes: list[float], volumes: list[float] = None, current_price: float = None) -> dict:
    """
    Main function: compute all indicators from close price list.
    Returns flat dict ready for Supabase insertion.
    """
    if not closes or len(closes) < 15:
        return {}

    price = current_price or closes[-1]
    rsi   = calc_rsi(closes)
    macd  = calc_macd(closes)
    bb    = calc_bollinger(closes)
    emas  = calc_ema_values(closes)
    vol_r = calc_volume_ratio(volumes) if volumes else None

    result = {
        "rsi_14":        rsi,
        "ema_20":        emas.get("ema_20"),
        "ema_50":        emas.get("ema_50"),
        "macd_line":     macd["line"]      if macd else None,
        "macd_signal":   macd["signal"]    if macd else None,
        "macd_histogram":macd["histogram"] if macd else None,
        "bb_upper":      bb["upper"]       if bb else None,
        "bb_middle":     bb["middle"]      if bb else None,
        "bb_lower":      bb["lower"]       if bb else None,
        "bb_position":   bb["position"]    if bb else None,
        "volume_ratio":  vol_r,
        "trend_signal":  determine_trend(
            rsi,
            macd,
            bb["position"] if bb else None,
            emas.get("ema_20"),
            emas.get("ema_50"),
            price
        ),
    }
    return {k: v for k, v in result.items() if v is not None}


def format_for_prompt(ind: dict, price: float) -> str:
    """Format indicators as readable string for AI agent prompts."""
    if not ind:
        return "Technical indicators: not available"

    lines = ["📊 Technical Indicators:"]

    # RSI
    rsi = ind.get("rsi_14")
    if rsi is not None:
        zone = "🔴 OVERBOUGHT" if rsi > 70 else "🟢 OVERSOLD" if rsi < 30 else "🟡 NEUTRAL"
        lines.append(f"  RSI(14): {rsi:.1f} → {zone}")

    # MACD
    macd_h = ind.get("macd_histogram")
    if macd_h is not None:
        macd_dir = "🟢 BULLISH crossover" if macd_h > 0 else "🔴 BEARISH crossover"
        lines.append(f"  MACD histogram: {macd_h:+.4f} → {macd_dir}")

    # Bollinger Bands
    bb_pos = ind.get("bb_position")
    bb_up  = ind.get("bb_upper")
    bb_lo  = ind.get("bb_lower")
    if bb_pos is not None:
        bb_zone = "near LOWER band (potential bounce)" if bb_pos < 0.2 else \
                  "near UPPER band (potential reversal)" if bb_pos > 0.8 else "MIDDLE of bands"
        lines.append(f"  Bollinger: price at {bb_pos*100:.0f}% of band → {bb_zone}")
        if bb_up and bb_lo:
            lines.append(f"  BB range: ${bb_lo:,.4f} – ${bb_up:,.4f}")

    # EMA trend
    ema20 = ind.get("ema_20")
    ema50 = ind.get("ema_50")
    if ema20 and ema50:
        if price > ema20 > ema50:
            ema_trend = "🟢 BULLISH (price > EMA20 > EMA50)"
        elif price < ema20 < ema50:
            ema_trend = "🔴 BEARISH (price < EMA20 < EMA50)"
        else:
            ema_trend = "🟡 MIXED"
        lines.append(f"  EMA20: ${ema20:,.4f} | EMA50: ${ema50:,.4f} → {ema_trend}")

    # Volume
    vol_r = ind.get("volume_ratio")
    if vol_r is not None:
        vol_txt = f"🔥 HIGH ({vol_r:.1f}x avg)" if vol_r > 1.5 else \
                  f"📉 LOW ({vol_r:.1f}x avg)" if vol_r < 0.7 else f"NORMAL ({vol_r:.1f}x avg)"
        lines.append(f"  Volume: {vol_txt}")

    # Overall trend
    trend = ind.get("trend_signal", "NEUTRAL")
    trend_icon = "🟢" if trend == "BULLISH" else "🔴" if trend == "BEARISH" else "🟡"
    lines.append(f"\n  {trend_icon} Overall trend signal: {trend}")

    return "\n".join(lines)
