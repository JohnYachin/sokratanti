"""
CAIOS Backtester — Architecture Roadmap: Backtesting Phase
Runs indicator-based strategy on historical data and computes performance metrics.
No LLM needed — pure deterministic signal generation from RSI/MACD/BB/EMA.
"""
import asyncio, math
from datetime import datetime, timezone

import httpx

# Reuse existing indicators module
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from app.data.indicators import compute_all


COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "BNB": "binancecoin",
    "SOL": "solana", "XRP": "ripple", "ADA": "cardano",
    "AVAX": "avalanche-2", "DOT": "polkadot", "MATIC": "matic-network",
    "LINK": "chainlink",
}


async def fetch_daily_history(coingecko_id: str, days: int = 90) -> list[dict]:
    """Fetch daily OHLCV. Returns list of {date, close, volume}."""
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart",
            params={"vs_currency": "usd", "days": days, "interval": "daily"}
        )
    data = r.json()
    prices  = data.get("prices", [])
    volumes = data.get("total_volumes", [])
    result  = []
    for (ts, price), (_, vol) in zip(prices, volumes):
        result.append({
            "date":   datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
            "close":  price,
            "volume": vol,
        })
    return result


def generate_signal(ind: dict) -> str:
    """Deterministic signal from indicators (no LLM)."""
    rsi    = ind.get("rsi_14")
    macd_h = ind.get("macd_histogram")
    bb_pos = ind.get("bb_position")
    trend  = ind.get("trend_signal", "NEUTRAL")

    bullish = 0
    bearish = 0

    if rsi is not None:
        if rsi < 30:   bullish += 2
        elif rsi < 40: bullish += 1
        elif rsi > 70: bearish += 2
        elif rsi > 60: bearish += 1

    if macd_h is not None:
        if macd_h > 0:  bullish += 1
        elif macd_h < 0: bearish += 1

    if bb_pos is not None:
        if bb_pos < 0.2:  bullish += 1
        elif bb_pos > 0.8: bearish += 1

    if trend == "BULLISH": bullish += 1
    elif trend == "BEARISH": bearish += 1

    if bullish >= 3:   return "BUY"
    elif bearish >= 3: return "SELL"
    return "HOLD"


def calc_sharpe(daily_returns: list[float], risk_free=0.0) -> float:
    """Annualised Sharpe ratio from daily returns."""
    if len(daily_returns) < 2:
        return 0.0
    avg = sum(daily_returns) / len(daily_returns)
    variance = sum((r - avg) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return 0.0
    return round((avg - risk_free) / std * math.sqrt(365), 2)


def calc_max_drawdown(equity_curve: list[float]) -> float:
    """Max drawdown % from peak."""
    peak = equity_curve[0]
    max_dd = 0.0
    for v in equity_curve:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def run_simulation(candles: list[dict], warmup: int = 52) -> dict:
    """
    Walk-forward simulation.
    Returns performance metrics dict.
    """
    if len(candles) < warmup + 5:
        return {"error": "Not enough historical data"}

    capital      = 10_000.0
    position     = 0.0       # coins held
    entry_price  = 0.0
    trades       = []
    equity_curve = []
    daily_returns= []
    prev_equity  = capital

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]

    current_signal = "HOLD"

    for i in range(warmup, len(candles)):
        price = candles[i]["close"]

        # Compute indicators on data up to today
        ind = compute_all(closes[:i+1], volumes[:i+1], price)
        signal = generate_signal(ind) if ind else "HOLD"

        # Execute on next bar (avoid look-ahead)
        if i + 1 >= len(candles):
            break
        exec_price = candles[i + 1]["close"]

        # BUY: enter position
        if signal == "BUY" and position == 0 and capital > 0:
            position    = capital / exec_price
            entry_price = exec_price
            capital     = 0.0
            current_signal = "BUY"

        # SELL: exit position
        elif signal == "SELL" and position > 0:
            capital  = position * exec_price
            pnl      = (exec_price - entry_price) / entry_price * 100
            trades.append({"entry": entry_price, "exit": exec_price, "pnl_pct": pnl, "correct": pnl > 0})
            position = 0.0
            entry_price = 0.0
            current_signal = "SELL"

        # Mark-to-market equity
        equity = capital + position * price
        equity_curve.append(equity)
        if prev_equity > 0:
            daily_returns.append((equity - prev_equity) / prev_equity)
        prev_equity = equity

    # Close open position at last price
    if position > 0:
        last_price = candles[-1]["close"]
        capital = position * last_price
        pnl     = (last_price - entry_price) / entry_price * 100
        trades.append({"entry": entry_price, "exit": last_price, "pnl_pct": pnl, "correct": pnl > 0})
        equity_curve.append(capital)

    if not equity_curve:
        return {"error": "No trades executed"}

    final_equity = equity_curve[-1]
    start_price  = candles[warmup]["close"]
    end_price    = candles[-1]["close"]
    bh_return    = (end_price - start_price) / start_price * 100

    strategy_return = (final_equity - 10_000) / 10_000 * 100
    win_rate = sum(1 for t in trades if t["correct"]) / max(len(trades), 1) * 100
    max_dd   = calc_max_drawdown(equity_curve)
    sharpe   = calc_sharpe(daily_returns)

    return {
        "strategy_return_pct": round(strategy_return, 2),
        "buyhold_return_pct":  round(bh_return, 2),
        "alpha":               round(strategy_return - bh_return, 2),
        "total_trades":        len(trades),
        "win_rate_pct":        round(win_rate, 1),
        "max_drawdown_pct":    max_dd,
        "sharpe_ratio":        sharpe,
        "final_equity":        round(final_equity, 2),
        "trades":              trades[-5:],  # last 5 trades
    }


async def backtest(symbol: str, days: int = 60) -> dict:
    """
    Main entry point.
    symbol: e.g. 'BTC'
    days: history length (30–365)
    Returns metrics dict or {'error': ...}
    """
    symbol = symbol.upper()
    cg_id  = COINGECKO_IDS.get(symbol)
    if not cg_id:
        return {"error": f"Unknown coin: {symbol}"}

    days = max(30, min(365, days))

    try:
        candles = await fetch_daily_history(cg_id, days)
    except Exception as e:
        return {"error": f"Data fetch failed: {e}"}

    if len(candles) < 20:
        return {"error": "Insufficient data"}

    result = run_simulation(candles)
    result["symbol"] = symbol
    result["days"]   = days
    return result


if __name__ == "__main__":
    import sys
    sym  = sys.argv[1] if len(sys.argv) > 1 else "BTC"
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 60

    async def main():
        print(f"\n🔬 Backtesting {sym} over {days} days...\n")
        r = await backtest(sym, days)
        if "error" in r:
            print(f"❌ {r['error']}")
            return
        strat = r["strategy_return_pct"]
        bh    = r["buyhold_return_pct"]
        alpha = r["alpha"]
        icon  = "🟢" if strat > 0 else "🔴"
        alpha_icon = "🟢" if alpha > 0 else "🔴"
        print(f"{icon}  Strategy return:   {strat:+.2f}%")
        print(f"📈 Buy & Hold return: {bh:+.2f}%")
        print(f"{alpha_icon}  Alpha:             {alpha:+.2f}%")
        print(f"🎯 Win Rate:         {r['win_rate_pct']:.1f}%")
        print(f"📊 Trades:           {r['total_trades']}")
        print(f"📉 Max Drawdown:     {r['max_drawdown_pct']:.2f}%")
        print(f"⚡ Sharpe Ratio:     {r['sharpe_ratio']}")
        print(f"💵 Final Equity:     ${r['final_equity']:,.2f}")

    asyncio.run(main())
