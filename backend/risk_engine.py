"""
CAIOS Risk Engine — Architecture Section 2: Risk Engine Layer
Calculates position sizing, stop-loss, take-profit, and risk score for signals.
Uses Kelly Criterion, ATR-based stops, and volatility-adjusted sizing.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from app.data.indicators import compute_all, calc_rsi, ema


# ─── ATR (Average True Range) ────────────────

def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Average True Range — measures volatility."""
    if len(closes) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        )
        true_ranges.append(tr)
    # Wilder smoothing
    atr = sum(true_ranges[:period]) / period
    for tr in true_ranges[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 6)


def calc_volatility(closes: list[float], period: int = 20) -> float | None:
    """Annualised historical volatility (%)."""
    if len(closes) < period + 1:
        return None
    returns = [(closes[i] - closes[i-1]) / closes[i-1] for i in range(1, len(closes))]
    recent  = returns[-period:]
    avg     = sum(recent) / period
    variance = sum((r - avg) ** 2 for r in recent) / (period - 1)
    daily_vol = math.sqrt(variance)
    annual_vol = daily_vol * math.sqrt(365) * 100
    return round(annual_vol, 2)


# ─── KELLY CRITERION ─────────────────────────

def kelly_fraction(win_rate: float, avg_win: float, avg_loss: float) -> float:
    """
    Kelly Criterion: optimal bet fraction.
    win_rate: probability of winning (0–1)
    avg_win / avg_loss: average win/loss sizes (positive)
    Returns fraction of capital to risk (capped at 25%).
    """
    if avg_loss <= 0:
        return 0.0
    b = avg_win / avg_loss      # reward-to-risk ratio
    q = 1 - win_rate
    kelly = (b * win_rate - q) / b
    return max(0.0, min(0.25, kelly))  # cap at 25%


# ─── RISK SCORE (1–10) ───────────────────────

def calc_risk_score(
    signal: str,
    confidence: float,
    rsi: float | None,
    volatility: float | None,
    bb_position: float | None,
    trend: str,
) -> dict:
    """
    Risk score 1 (safe) to 10 (dangerous).
    Returns {score, breakdown, recommendation}.
    """
    score = 5.0  # baseline

    # Signal confidence
    score -= (confidence - 0.5) * 4  # high confidence → lower risk

    # RSI extremes reduce risk (oversold BUY / overbought SELL)
    if rsi is not None:
        if signal in ("BUY", "STRONG_BUY"):
            if rsi < 30:    score -= 1.5  # oversold = safer BUY
            elif rsi > 60:  score += 2.0  # buying into strength = riskier
        elif signal in ("SELL", "STRONG_SELL"):
            if rsi > 70:    score -= 1.5  # overbought = safer SELL
            elif rsi < 40:  score += 2.0  # selling into weakness = riskier

    # Volatility
    if volatility is not None:
        if volatility > 100:   score += 2.0  # extreme vol
        elif volatility > 70:  score += 1.0
        elif volatility < 40:  score -= 0.5  # low vol = safer

    # Trend alignment
    if signal in ("BUY", "STRONG_BUY") and trend == "BULLISH":
        score -= 1.0  # aligned with trend
    elif signal in ("BUY", "STRONG_BUY") and trend == "BEARISH":
        score += 1.5  # counter-trend
    elif signal in ("SELL", "STRONG_SELL") and trend == "BEARISH":
        score -= 1.0
    elif signal in ("SELL", "STRONG_SELL") and trend == "BULLISH":
        score += 1.5

    score = round(max(1.0, min(10.0, score)), 1)

    if score <= 3:
        rec = "✅ Низкий риск — можно входить"
    elif score <= 6:
        rec = "⚠️ Средний риск — уменьшите позицию"
    else:
        rec = "🔴 Высокий риск — осторожно"

    return {"score": score, "recommendation": rec}


# ─── POSITION SIZING ─────────────────────────

def calc_position(
    capital: float,
    price: float,
    atr: float,
    risk_score: float,
    atr_multiplier: float = 2.0,
    max_risk_pct: float = 0.02,  # 2% of capital per trade
) -> dict:
    """
    ATR-based position sizing.
    Stop-loss = price ± ATR × multiplier
    Position = (capital × risk%) / stop_distance
    """
    stop_distance = atr * atr_multiplier
    risk_per_trade = capital * max_risk_pct * (1 - (risk_score - 1) / 18)  # scale by risk

    units = risk_per_trade / stop_distance if stop_distance > 0 else 0
    position_usd = units * price

    # Cap at 25% of portfolio
    position_usd = min(position_usd, capital * 0.25)
    units = position_usd / price if price > 0 else 0

    return {
        "units":         round(units, 6),
        "position_usd":  round(position_usd, 2),
        "pct_of_capital": round(position_usd / capital * 100, 1) if capital > 0 else 0,
    }


# ─── STOP-LOSS / TAKE-PROFIT ─────────────────

def calc_levels(
    signal: str,
    price: float,
    atr: float,
    risk_reward: float = 2.0,
    atr_multiplier: float = 2.0,
) -> dict:
    """
    ATR-based stop-loss and take-profit.
    risk_reward: target R:R ratio (default 2:1)
    """
    stop_dist = atr * atr_multiplier
    tp_dist   = stop_dist * risk_reward

    if signal in ("BUY", "STRONG_BUY"):
        stop_loss   = price - stop_dist
        take_profit = price + tp_dist
    else:  # SELL / STRONG_SELL
        stop_loss   = price + stop_dist
        take_profit = price - tp_dist

    sl_pct = abs(stop_loss - price) / price * 100
    tp_pct = abs(take_profit - price) / price * 100

    return {
        "stop_loss":        round(stop_loss, 4),
        "take_profit":      round(take_profit, 4),
        "stop_loss_pct":    round(sl_pct, 2),
        "take_profit_pct":  round(tp_pct, 2),
        "risk_reward":      risk_reward,
    }


# ─── MAIN ENTRY POINT ────────────────────────

def assess(
    signal: str,
    confidence: float,
    price: float,
    closes: list[float],
    highs: list[float] | None = None,
    lows: list[float] | None = None,
    capital: float = 10_000.0,
    ind: dict | None = None,
) -> dict:
    """
    Full risk assessment for a signal.
    Returns complete risk report dict.
    """
    if not closes or len(closes) < 15:
        return {"error": "Not enough historical data"}

    # Use provided indicators or compute
    if ind is None:
        ind = compute_all(closes, None, price)

    rsi     = ind.get("rsi_14")
    bb_pos  = ind.get("bb_position")
    trend   = ind.get("trend_signal", "NEUTRAL")

    # ATR (needs highs/lows; approximate with closes if missing)
    if highs and lows:
        atr = calc_atr(highs, lows, closes)
    else:
        # Approximate ATR from close prices only
        ranges = [abs(closes[i] - closes[i-1]) for i in range(1, len(closes))]
        atr = sum(ranges[-14:]) / 14 if len(ranges) >= 14 else price * 0.02

    volatility = calc_volatility(closes)

    # Risk score
    risk = calc_risk_score(signal, confidence, rsi, volatility, bb_pos, trend)

    # Levels
    levels = calc_levels(signal, price, atr)

    # Position sizing
    position = calc_position(capital, price, atr, risk["score"])

    return {
        "signal":       signal,
        "price":        price,
        "risk_score":   risk["score"],
        "recommendation": risk["recommendation"],
        "stop_loss":    levels["stop_loss"],
        "take_profit":  levels["take_profit"],
        "stop_loss_pct":   levels["stop_loss_pct"],
        "take_profit_pct": levels["take_profit_pct"],
        "risk_reward":  levels["risk_reward"],
        "position_usd":    position["position_usd"],
        "position_pct":    position["pct_of_capital"],
        "units":        position["units"],
        "volatility":   volatility,
        "atr":          round(atr, 4),
    }
