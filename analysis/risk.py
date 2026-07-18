"""
analysis/risk.py — Расчёт зон входа, stop-loss, целей и risk/reward.

Все расчёты детерминированы (без GPT):
  - Зона входа: вблизи ближайшей поддержки / нижней полосы BB / EMA
  - Stop-loss:  под swing low с ATR-буфером
  - Цели:       R/R 1:2 и 1:3.5, скорректированные по ближайшему сопротивлению
  - Chase limit: максимальная цена для входа (не гнаться за ценой)

Правило безопасности: если stop-loss нельзя определить →
  entry_zone и targets не возвращаются (нет stop = нет сигнала).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TradingPlan:
    """Торговый план для BUY сигнала."""
    entry_low: Optional[float]    # нижняя граница зоны входа
    entry_high: Optional[float]   # верхняя граница зоны входа
    stop_loss: Optional[float]    # уровень отмены (stop-loss)
    target_1: Optional[float]     # цель 1 (R/R ≈ 1:2)
    target_2: Optional[float]     # цель 2 (R/R ≈ 1:3.5)
    chase_limit: Optional[float]  # максимум цены для входа (не гнаться выше)
    risk_reward: Optional[float]  # R/R для target_1
    risk_pct: Optional[float]     # риск в % от entry_low до stop
    setup_valid: bool = False      # False если stop определить невозможно
    invalidation: str = ""        # почему стоп-лосс не определён


def calculate_trading_plan(
    price: float,
    indicators: dict,
    supports: list,
    resistances: list,
    swing_low: Optional[float] = None,
) -> TradingPlan:
    """
    Рассчитывает торговый план для текущей ситуации.

    Args:
        price:       текущая цена
        indicators:  словарь из calculate_indicators()
        supports:    список PriceLevel (поддержка)
        resistances: список PriceLevel (сопротивление)
        swing_low:   последний swing low (из SwingPoints.recent_low)
    """
    atr = indicators.get("atr")
    bb_low = indicators.get("bb_low")
    ema20 = indicators.get("ema20")
    ema50 = indicators.get("ema50")

    # ── Stop-loss: нужно чёткое invalidation level ───────────────────────────
    stop_loss = _calc_stop_loss(price, swing_low, atr, supports)

    if stop_loss is None or stop_loss >= price:
        return TradingPlan(
            entry_low=None, entry_high=None,
            stop_loss=None, target_1=None, target_2=None,
            chase_limit=None, risk_reward=None, risk_pct=None,
            setup_valid=False,
            invalidation="Нельзя определить чёткий уровень отмены сделки",
        )

    # ── Зона входа ───────────────────────────────────────────────────────────
    entry_low, entry_high = _calc_entry_zone(price, bb_low, ema20, ema50, supports)

    if entry_low is None:
        entry_low = price * 0.99
        entry_high = price * 1.005

    # Проверяем что entry выше stop
    if entry_low <= stop_loss:
        entry_low = stop_loss * 1.005

    # ── Chase limit ──────────────────────────────────────────────────────────
    chase_limit = round(entry_high * 1.015, _decimals(price))

    # ── Риск ─────────────────────────────────────────────────────────────────
    risk_per_unit = entry_low - stop_loss
    risk_pct = round((risk_per_unit / entry_low) * 100, 2) if entry_low > 0 else None

    # ── Цели: R/R 1:2 и 1:3.5, проверяем по сопротивлению ──────────────────
    t1_raw = entry_low + risk_per_unit * 2.0
    t2_raw = entry_low + risk_per_unit * 3.5

    # Если сопротивление мешает цели — ставим цель перед сопротивлением
    if resistances:
        nearest_res = resistances[0].price
        if nearest_res < t1_raw:
            t1_raw = nearest_res * 0.995  # чуть ниже сопротивления
        if len(resistances) >= 2:
            second_res = resistances[1].price
            if second_res < t2_raw:
                t2_raw = second_res * 0.995

    target_1 = round(t1_raw, _decimals(price))
    target_2 = round(t2_raw, _decimals(price))

    # R/R для target_1
    reward = target_1 - entry_low
    risk_reward = round(reward / risk_per_unit, 2) if risk_per_unit > 0 else None

    return TradingPlan(
        entry_low=round(entry_low, _decimals(price)),
        entry_high=round(entry_high, _decimals(price)),
        stop_loss=round(stop_loss, _decimals(price)),
        target_1=target_1,
        target_2=target_2,
        chase_limit=round(chase_limit, _decimals(price)),
        risk_reward=risk_reward,
        risk_pct=risk_pct,
        setup_valid=True,
        invalidation="",
    )


def _calc_stop_loss(
    price: float,
    swing_low: Optional[float],
    atr: Optional[float],
    supports: list,
) -> Optional[float]:
    """
    Определяет stop-loss ниже ключевого уровня.
    Приоритет: swing_low → ближайший support → price - 2*ATR
    """
    atr_buf = atr * 0.5 if atr else price * 0.02  # 2% если нет ATR

    candidates = []

    if swing_low is not None and swing_low < price:
        sl = swing_low - atr_buf
        if sl > 0 and sl < price * 0.95:  # не больше 5% риска
            candidates.append(sl)

    if supports:
        s0 = supports[0].price
        if s0 < price:
            sl = s0 - atr_buf
            if sl > 0 and sl < price * 0.95:
                candidates.append(sl)

    if atr:
        sl_atr = price - atr * 2.0
        if sl_atr > 0:
            candidates.append(sl_atr)

    if not candidates:
        return None

    # Берём самый высокий (консервативный, минимальный риск)
    return max(candidates)


def _calc_entry_zone(
    price: float,
    bb_low: Optional[float],
    ema20: Optional[float],
    ema50: Optional[float],
    supports: list,
) -> tuple[Optional[float], Optional[float]]:
    """
    Определяет зону входа как диапазон между несколькими уровнями.
    """
    candidates_low = []
    candidates_high = []

    # Нижняя полоса Боллинджера
    if bb_low and bb_low < price:
        candidates_low.append(bb_low)
        candidates_high.append(bb_low * 1.01)

    # EMA20 как динамическая поддержка
    if ema20 and ema20 < price:
        candidates_low.append(ema20 * 0.99)
        candidates_high.append(ema20 * 1.005)

    # EMA50
    if ema50 and ema50 < price and ema50 > (price * 0.8):
        candidates_low.append(ema50 * 0.99)
        candidates_high.append(ema50 * 1.005)

    # Ближайший уровень поддержки
    if supports:
        s0 = supports[0].price
        if s0 < price:
            candidates_low.append(s0 * 0.995)
            candidates_high.append(s0 * 1.01)

    if not candidates_low:
        return None, None

    # Зона входа: медиана нижних + верхних кандидатов
    import statistics
    entry_low  = statistics.median(candidates_low)
    entry_high = statistics.median(candidates_high)

    # Убеждаемся что high >= low
    if entry_high < entry_low:
        entry_high = entry_low * 1.01

    # Зона не должна быть слишком далеко от текущей цены (> 10%)
    if entry_low < price * 0.90:
        entry_low = price * 0.97
        entry_high = price * 1.00

    return entry_low, entry_high


def _decimals(price: float) -> int:
    """Количество знаков после запятой в зависимости от цены."""
    if price >= 1000:
        return 2
    elif price >= 10:
        return 4
    elif price >= 0.1:
        return 5
    else:
        return 6


def format_trading_plan(plan: TradingPlan, coin: str) -> str:
    """Форматирует торговый план для Telegram (HTML)."""
    if not plan.setup_valid:
        return f"⚠️ <i>Торговый план недоступен: {plan.invalidation}</i>"

    def fmt(v):
        if v is None:
            return "—"
        if v >= 1000:
            return f"${v:,.2f}"
        elif v >= 1:
            return f"${v:.4f}"
        else:
            return f"${v:.6f}"

    rr_str = f"{plan.risk_reward:.1f}" if plan.risk_reward else "—"
    risk_str = f"{plan.risk_pct:.1f}%" if plan.risk_pct else "—"

    return (
        f"📐 <b>Торговый план {coin.upper()}:</b>\n"
        f"  🟩 Зона входа:  <code>{fmt(plan.entry_low)} — {fmt(plan.entry_high)}</code>\n"
        f"  🚫 Stop-loss:   <code>{fmt(plan.stop_loss)}</code>  (риск {risk_str})\n"
        f"  🎯 Цель 1 (1:{rr_str}): <code>{fmt(plan.target_1)}</code>\n"
        f"  🎯 Цель 2:      <code>{fmt(plan.target_2)}</code>\n"
        f"  ⛔ Не входить выше: <code>{fmt(plan.chase_limit)}</code>"
    )
