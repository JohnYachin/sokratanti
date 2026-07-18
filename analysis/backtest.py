"""
analysis/backtest.py — Движок бэктестирования v1.0

Методология:
  - Для каждой свечи i берём только данные [0..i] (нет look-ahead bias)
  - Считаем индикаторы на срезе → определяем сигнал
  - При сигнале BUY: вход на close[i], stop = entry - ATR*2, target = entry + ATR*3.5
  - Выход: target / stop / таймаут (15 баров)
  - Одновременно максимум 1 открытая сделка

Возвращает:
  BacktestResult с полной статистикой и списком сделок.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from analysis.technical import calculate_indicators

logger = logging.getLogger(__name__)

# Минимум баров для прогрева индикаторов (EMA200 нужно 200+)
WARMUP_BARS = 60
# Таймаут позиции в барах
MAX_HOLD_BARS = 20
# ATR мультипликаторы
ATR_STOP_MULT   = 1.5
ATR_TARGET_MULT = 2.5


def _run_backtest_core(
    df,
    coin: str,
    interval: str,
    lookback: int,
    min_score: int,
    atr_stop_mult: float,
    atr_target_mult: float,
    max_hold_bars: int,
) -> "Optional[BacktestResult]":
    """
    Ядро бэктеста: принимает готовый DataFrame.
    Вызывается из run_backtest() и из optimizer.run_grid_search()
    (где DataFrame загружается один раз для всех комбинаций).
    """
    if interval == "1d":
        lookback_bars = lookback
    elif interval == "4h":
        lookback_bars = lookback * 6
    elif interval == "1h":
        lookback_bars = lookback * 24
    else:
        lookback_bars = lookback

    test_start = max(WARMUP_BARS, len(df) - lookback_bars)
    df_full = df

    trades: "list[Trade]" = []
    in_trade    = False
    entry_price = 0.0
    stop_loss   = 0.0
    target      = 0.0
    entry_date  = None
    entry_bar   = 0
    entry_score = 0

    equity       = 0.0
    equity_curve = [0.0]
    peak_equity  = 0.0
    max_dd       = 0.0

    for i in range(WARMUP_BARS, len(df_full)):
        df_slice  = df_full.iloc[:i + 1]
        bar_date  = df_full.index[i]
        bar_close = float(df_full["close"].iloc[i])
        bar_high  = float(df_full["high"].iloc[i]) if "high" in df_full.columns else bar_close
        bar_low   = float(df_full["low"].iloc[i])  if "low"  in df_full.columns else bar_close
        in_test   = i >= test_start

        if in_trade:
            bars_held  = i - entry_bar
            exit_price = None
            outcome    = None

            if bar_low <= stop_loss:
                exit_price = stop_loss;  outcome = "loss"
            elif bar_high >= target:
                exit_price = target;     outcome = "win"
            elif bars_held >= max_hold_bars:
                exit_price = bar_close;  outcome = "timeout"

            if exit_price is not None:
                pnl = (exit_price - entry_price) / entry_price
                if in_test:
                    trades.append(Trade(
                        entry_date=entry_date, exit_date=bar_date,
                        coin=coin, entry_price=entry_price,
                        exit_price=exit_price, stop_loss=stop_loss,
                        target=target, pnl_pct=pnl, outcome=outcome,
                        bars_held=bars_held, entry_score=entry_score,
                    ))
                    equity += pnl
                    equity_curve.append(round(equity, 4))
                    peak_equity = max(peak_equity, equity)
                    max_dd = min(max_dd, equity - peak_equity)
                in_trade = False

        if not in_trade and in_test:
            try:
                ind   = calculate_indicators(df_slice)
                score = _calc_bar_score(ind)
                if score >= min_score:
                    atr = ind.get("atr") or bar_close * 0.02
                    entry_price = bar_close
                    stop_loss   = entry_price - atr * atr_stop_mult
                    target      = entry_price + atr * atr_target_mult
                    in_trade    = True
                    entry_date  = bar_date
                    entry_bar   = i
                    entry_score = score
            except Exception as e:
                logger.debug("Backtest core error bar %d: %s", i, e)

    if not trades:
        return BacktestResult(
            coin=coin, interval=interval, period_days=lookback,
            n_trades=0, n_wins=0, n_losses=0, n_timeouts=0,
            win_rate=0.0, avg_win_pct=0.0, avg_loss_pct=0.0,
            avg_rr_achieved=0.0, total_return=0.0, max_drawdown=0.0,
            best_trade=0.0, worst_trade=0.0, avg_bars_held=0.0,
            trades=[], equity_curve=[0.0],
        )

    wins     = [t for t in trades if t.outcome == "win"]
    losses   = [t for t in trades if t.outcome == "loss"]
    timeouts = [t for t in trades if t.outcome == "timeout"]

    win_rate     = len(wins) / len(trades)
    avg_win      = sum(t.pnl_pct for t in wins)    / len(wins)    if wins    else 0.0
    avg_loss     = sum(t.pnl_pct for t in losses)  / len(losses)  if losses  else 0.0
    total_return = sum(t.pnl_pct for t in trades)
    best_trade   = max(t.pnl_pct for t in trades)
    worst_trade  = min(t.pnl_pct for t in trades)
    avg_bars     = sum(t.bars_held for t in trades) / len(trades)

    rr_list = []
    for t in wins:
        risk = (t.entry_price - t.stop_loss) / t.entry_price
        if risk > 0:
            rr_list.append(t.pnl_pct / risk)
    avg_rr = sum(rr_list) / len(rr_list) if rr_list else 0.0

    return BacktestResult(
        coin=coin, interval=interval, period_days=lookback,
        n_trades=len(trades), n_wins=len(wins),
        n_losses=len(losses), n_timeouts=len(timeouts),
        win_rate=win_rate, avg_win_pct=avg_win, avg_loss_pct=avg_loss,
        avg_rr_achieved=avg_rr, total_return=total_return,
        max_drawdown=max_dd, best_trade=best_trade,
        worst_trade=worst_trade, avg_bars_held=avg_bars,
        trades=trades, equity_curve=equity_curve,
    )



@dataclass
class Trade:
    entry_date:  datetime
    exit_date:   Optional[datetime]
    coin:        str
    entry_price: float
    exit_price:  float
    stop_loss:   float
    target:      float
    pnl_pct:     float        # в долях: 0.05 = +5%
    outcome:     str          # 'win' | 'loss' | 'timeout'
    bars_held:   int
    entry_score: int          # setup score на момент входа


@dataclass
class BacktestResult:
    coin:           str
    interval:       str
    period_days:    int
    n_trades:       int
    n_wins:         int
    n_losses:       int
    n_timeouts:     int
    win_rate:       float      # 0..1
    avg_win_pct:    float
    avg_loss_pct:   float
    avg_rr_achieved: float     # средний реализованный R/R для wins
    total_return:   float      # суммарный P&L всех сделок (некомпаундированный)
    max_drawdown:   float      # максимальная просадка equity curve (отрицательное)
    best_trade:     float
    worst_trade:    float
    avg_bars_held:  float
    trades:         list[Trade] = field(default_factory=list)
    equity_curve:   list[float] = field(default_factory=list)


def _calc_bar_score(ind: dict) -> int:
    """
    Упрощённый setup score для бэктеста (только технические, без новостей/F&G).
    Диапазон: -50..+70 (тест: вход при >= 30)
    """
    score = 0

    # RSI (0-15 pts)
    rsi = ind.get("rsi")
    if rsi is not None:
        if rsi <= 30:    score += 15
        elif rsi <= 40:  score += 12
        elif rsi <= 50:  score += 6
        elif rsi >= 70:  score -= 12
        elif rsi >= 60:  score -= 6

    # MACD (0-10 pts)
    macd = ind.get("macd_diff")
    if macd is not None:
        if macd > 0:  score += 10
        else:         score -= 8

    # Bollinger Bands (0-10 pts)
    pband = ind.get("bb_pband")
    if pband is not None:
        if pband <= 0.15:   score += 10
        elif pband <= 0.30: score += 5
        elif pband >= 0.85: score -= 10
        elif pband >= 0.70: score -= 5

    # EMA тренд (0-25 pts)
    price  = ind.get("price")
    ema20  = ind.get("ema20")
    ema50  = ind.get("ema50")
    ema200 = ind.get("ema200")

    if price and ema20 and ema50:
        if price > ema20 and ema20 > ema50:
            score += 20    # бычье выравнивание
        elif price < ema20 and ema20 < ema50:
            score -= 15    # медвежье

    if price and ema200:
        score += 5 if price > ema200 else -5

    # ADX (0-5 pts)
    adx = ind.get("adx")
    if adx is not None:
        score += 5 if adx >= 20 else 0

    return score


def run_backtest(
    coin: str,
    interval: str = "1d",
    lookback: int = 180,
    min_score: int = None,   # None → берём из БД (оптимизированные)
) -> Optional[BacktestResult]:
    """
    Запускает бэктест.
    Если min_score=None — использует оптимальные параметры из БД (если есть).

    Args:
        coin:       Тикер (btc, eth и т.д.)
        interval:   Таймфрейм (1d, 4h)
        lookback:   Количество дней для теста
        min_score:  Минимальный score для входа (None → из БД или 20)
    """
    from market_data.binance_rest import get_klines

    # Загружаем оптимальные параметры если есть
    try:
        from analysis.optimizer import get_optimized_params
        params = get_optimized_params(coin, interval)
    except Exception:
        params = {}

    eff_min_score  = min_score   if min_score  is not None else params.get("min_score", 20)
    eff_atr_stop   = params.get("atr_stop",   ATR_STOP_MULT)
    eff_atr_target = params.get("atr_target",  ATR_TARGET_MULT)
    eff_max_hold   = params.get("max_hold",   MAX_HOLD_BARS)

    needed_bars = lookback + WARMUP_BARS + eff_max_hold + 10
    df = get_klines(coin, interval=interval, limit=needed_bars)

    if df.empty or len(df) < WARMUP_BARS + 20:
        logger.error("Недостаточно данных для бэктеста %s %s", coin, interval)
        return None

    return _run_backtest_core(
        df=df,
        coin=coin,
        interval=interval,
        lookback=lookback,
        min_score=eff_min_score,
        atr_stop_mult=eff_atr_stop,
        atr_target_mult=eff_atr_target,
        max_hold_bars=eff_max_hold,
    )

    timeouts = [t for t in trades if t.outcome == "timeout"]

    win_rate       = len(wins) / len(trades)
    avg_win        = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0.0
    avg_loss       = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0.0
    total_return   = sum(t.pnl_pct for t in trades)
    best_trade     = max(t.pnl_pct for t in trades)
    worst_trade    = min(t.pnl_pct for t in trades)
    avg_bars       = sum(t.bars_held for t in trades) / len(trades)

    # Реализованный R/R для wins: (win_pct) / abs(stop_dist / entry)
    rr_list = []
    for t in wins:
        risk = (t.entry_price - t.stop_loss) / t.entry_price
        if risk > 0:
            rr_list.append(t.pnl_pct / risk)
    avg_rr = sum(rr_list) / len(rr_list) if rr_list else 0.0

    return BacktestResult(
        coin=coin, interval=interval, period_days=lookback,
        n_trades=len(trades),
        n_wins=len(wins),
        n_losses=len(losses),
        n_timeouts=len(timeouts),
        win_rate=win_rate,
        avg_win_pct=avg_win,
        avg_loss_pct=avg_loss,
        avg_rr_achieved=avg_rr,
        total_return=total_return,
        max_drawdown=max_dd,
        best_trade=best_trade,
        worst_trade=worst_trade,
        avg_bars_held=avg_bars,
        trades=trades,
        equity_curve=equity_curve,
    )


def format_backtest(result: BacktestResult) -> str:
    """Форматирует результат для Telegram (HTML)."""
    if result.n_trades == 0:
        return (
            f"📊 <b>Бэктест {result.coin.upper()} ({result.interval}, "
            f"{result.period_days}д)</b>\n\n"
            "⚠️ Нет сделок за указанный период.\n"
            "Попробуй уменьшить минимальный score или увеличить период."
        )

    interval_ru = {"1d": "1 день", "4h": "4 часа", "1h": "1 час"}.get(result.interval, result.interval)

    # Equity bar
    eq_pct = result.total_return * 100
    eq_sign = "+" if eq_pct >= 0 else ""
    eq_emoji = "📈" if eq_pct >= 0 else "📉"

    # Win rate bar (10 блоков)
    filled = round(result.win_rate * 10)
    wr_bar = "🟩" * filled + "⬜" * (10 - filled)

    # Последние 5 сделок
    recent = result.trades[-5:]
    trade_lines = []
    for t in recent:
        sign = "+" if t.pnl_pct >= 0 else ""
        icon = "✅" if t.outcome == "win" else ("❌" if t.outcome == "loss" else "⏱")
        date = t.entry_date.strftime("%d.%m") if hasattr(t.entry_date, "strftime") else str(t.entry_date)[:5]
        trade_lines.append(
            f"  {icon} {sign}{t.pnl_pct * 100:.1f}% за {t.bars_held} бар  ({date})"
        )

    trades_block = "\n".join(trade_lines)

    return (
        f"📊 <b>Бэктест {result.coin.upper()}</b> "
        f"({interval_ru}, {result.period_days} дней)\n\n"
        f"Сделок: <b>{result.n_trades}</b>  "
        f"(✅{result.n_wins} / ❌{result.n_losses} / ⏱{result.n_timeouts})\n"
        f"Win rate: <code>{result.win_rate * 100:.1f}%</code>  {wr_bar}\n\n"
        f"Ср. прибыль:   <code>+{result.avg_win_pct * 100:.1f}%</code>\n"
        f"Ср. убыток:    <code>{result.avg_loss_pct * 100:.1f}%</code>\n"
        f"Ср. R/R:       <code>{result.avg_rr_achieved:.2f}:1</code>\n"
        f"Ср. держали:   <code>{result.avg_bars_held:.1f} бар</code>\n\n"
        f"Лучшая:        <code>+{result.best_trade * 100:.1f}%</code>\n"
        f"Худшая:        <code>{result.worst_trade * 100:.1f}%</code>\n"
        f"Макс. просадка:<code>{result.max_drawdown * 100:.1f}%</code>\n\n"
        f"{eq_emoji} <b>Итог (некомпаунд):</b> "
        f"<code>{eq_sign}{eq_pct:.1f}%</code>\n\n"
        f"📋 <b>Последние {len(recent)} сделок:</b>\n"
        f"{trades_block}\n\n"
        f"<i>⚠️ Прошлые результаты не гарантируют будущих.\n"
        f"Бэктест без учёта спреда, комиссий и проскальзывания.</i>"
    )
