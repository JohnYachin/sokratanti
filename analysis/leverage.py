"""
analysis/leverage.py — Модуль рекомендации плеча и риск-менеджмента v2.0

Улучшения Phase 16:
  - Расчёт цены ликвидации (Binance Futures USDT-M формула)
  - Плечо учитывает Funding Rate (высокий фандинг → меньше плечо)
  - Плечо учитывает волатильность (ATR % от цены)
  - Размер позиции в USDT при депозите $X
  - Предупреждение если ликвидация близко к стопу
"""
from __future__ import annotations


def calc_liquidation_price(
    entry: float,
    leverage: int,
    direction: str = "LONG",
    maintenance_margin_rate: float = 0.004,  # Binance USDT-M: 0.4% для большинства монет
) -> float:
    """
    Рассчитывает цену ликвидации для Binance USDT-M Futures.

    Формула Binance (упрощённая):
      LONG:  liq = entry * (1 - 1/leverage + maintenance_margin_rate)
      SHORT: liq = entry * (1 + 1/leverage - maintenance_margin_rate)

    Реальная формула чуть сложнее (учитывает unrealized PnL и маржу),
    но для оценки опасности этого достаточно.
    """
    if leverage <= 0:
        return 0.0
    if direction == "LONG":
        liq = entry * (1 - 1 / leverage + maintenance_margin_rate)
    else:  # SHORT
        liq = entry * (1 + 1 / leverage - maintenance_margin_rate)
    return round(liq, _price_decimals(entry))


def recommend_leverage(
    setup_score: int,
    entry: float,
    stop: float,
    target1: float,
    target2: float | None = None,
    funding_rate_pct: float = 0.0,   # текущий Funding Rate в %
    atr_pct: float | None = None,    # ATR как % от цены (волатильность)
    direction: str = "LONG",
) -> dict:
    """
    Рассчитывает оптимальное плечо и параметры сделки.

    Args:
        setup_score:      0-100
        entry:            цена входа
        stop:             уровень стоп-лосса
        target1:          первая цель
        target2:          вторая цель (опционально)
        funding_rate_pct: текущий Funding Rate (%) — влияет на плечо
        atr_pct:          ATR как % от цены — волатильность
        direction:        "LONG" | "SHORT"

    Returns:
        dict с leverage, liquidation_price, risk_pct, reward1_pct, ...
    """
    # Дистанции в %
    if direction == "SHORT":
        risk_pct    = abs(stop - entry)   / entry
        reward1_pct = abs(entry - target1) / entry
        reward2_pct = abs(entry - target2) / entry if target2 else reward1_pct * 1.8
    else:
        risk_pct    = abs(entry - stop)   / entry
        reward1_pct = abs(target1 - entry) / entry
        reward2_pct = abs(target2 - entry) / entry if target2 else reward1_pct * 1.8

    rr_ratio = reward1_pct / risk_pct if risk_pct > 0 else 0

    # ── Базовое плечо по setup_score ──────────────────────────────────────────
    if setup_score >= 85:
        base_lev   = 5
        confidence = "🔥 Очень высокая"
        conf_level = 4
    elif setup_score >= 75:
        base_lev   = 3
        confidence = "✅ Высокая"
        conf_level = 3
    elif setup_score >= 65:
        base_lev   = 2
        confidence = "👀 Умеренная"
        conf_level = 2
    else:
        base_lev   = 1
        confidence = "⚠️ Осторожно — минимум"
        conf_level = 1

    # ── Коррекция по риску (расстояние до стопа) ──────────────────────────────
    lev = base_lev
    if risk_pct > 0.06:       # >6% до стопа → 1x
        lev = 1
    elif risk_pct > 0.04:     # 4–6% → max 2x
        lev = min(lev, 2)
    elif risk_pct > 0.025:    # 2.5–4% → max 3x
        lev = min(lev, 3)

    # ── Коррекция по Funding Rate ──────────────────────────────────────────────
    # Высокий положительный фандинг при LONG = лонги перегреты → снижаем плечо
    funding_note = ""
    if direction == "LONG":
        if funding_rate_pct > 0.10:
            lev = min(lev, 2)
            funding_note = "⚠️ Фандинг высокий — плечо снижено"
        elif funding_rate_pct > 0.05:
            lev = min(lev, 3)
            funding_note = "ℹ️ Умеренный фандинг"
    elif direction == "SHORT":
        if funding_rate_pct < -0.05:
            lev = min(lev, 2)
            funding_note = "⚠️ Фандинг отрицательный — шорты перегреты, плечо снижено"

    # ── Коррекция по волатильности (ATR) ──────────────────────────────────────
    vol_note = ""
    if atr_pct is not None:
        if atr_pct > 0.05:     # >5% суточный ATR — рынок очень волатилен
            lev = min(lev, 2)
            vol_note = "⚠️ Высокая волатильность — плечо снижено"
        elif atr_pct > 0.03:   # 3–5% ATR
            lev = min(lev, 3)

    lev = max(1, lev)  # минимум 1x

    # ── Цена ликвидации ───────────────────────────────────────────────────────
    liquidation_price = calc_liquidation_price(entry, lev, direction)

    # Буфер между стопом и ликвидацией
    if direction == "LONG":
        liq_buffer_pct = (stop - liquidation_price) / entry * 100 if stop > 0 else 0
        liq_safe = liquidation_price < stop  # стоп срабатывает ДО ликвидации
    else:
        liq_buffer_pct = (liquidation_price - stop) / entry * 100 if stop > 0 else 0
        liq_safe = liquidation_price > stop

    # ── Финансы с плечом ──────────────────────────────────────────────────────
    risk_lev    = risk_pct    * lev * 100
    reward1_lev = reward1_pct * lev * 100
    reward2_lev = reward2_pct * lev * 100

    # Размер позиции (2% риска от депозита)
    pos_size_pct = 2.0 / (risk_pct * 100) if risk_pct > 0 else 100.0
    pos_size_pct = min(pos_size_pct, 100.0)
    margin_pct   = pos_size_pct / lev

    return {
        "leverage":          lev,
        "confidence":        confidence,
        "conf_level":        conf_level,
        "risk_pct":          risk_pct * 100,
        "risk_lev_pct":      risk_lev,
        "reward1_pct":       reward1_pct * 100,
        "reward1_lev":       reward1_lev,
        "reward2_pct":       reward2_pct * 100,
        "reward2_lev":       reward2_lev,
        "rr_ratio":          rr_ratio,
        "pos_size_pct":      pos_size_pct,
        "margin_pct":        margin_pct,
        "liquidation_price": liquidation_price,
        "liq_buffer_pct":    liq_buffer_pct,
        "liq_safe":          liq_safe,
        "funding_note":      funding_note,
        "vol_note":          vol_note,
        "direction":         direction,
    }


def format_trade_signal(
    coin: str,
    setup_score: int,
    signal_status: str,
    emoji: str,
    entry_low: float,
    entry_high: float,
    stop: float,
    target1: float,
    target2: float | None,
    lev_data: dict,
    notes: list[str] | None = None,
) -> str:
    """
    Форматирует полный торговый сигнал для Telegram (HTML).

    Phase 16: добавлена цена ликвидации и предупреждения.
    """

    def _p(v: float) -> str:
        if v >= 1000:  return f"${v:,.1f}"
        elif v >= 1:   return f"${v:.3f}"
        else:          return f"${v:.5f}"

    lev   = lev_data["leverage"]
    conf  = lev_data["confidence"]
    risk  = lev_data["risk_lev_pct"]
    r1    = lev_data["reward1_lev"]
    r2    = lev_data["reward2_lev"]
    rr    = lev_data["rr_ratio"]
    marg  = lev_data["margin_pct"]
    liq   = lev_data.get("liquidation_price", 0)
    liq_safe     = lev_data.get("liq_safe", True)
    funding_note = lev_data.get("funding_note", "")
    vol_note     = lev_data.get("vol_note", "")
    direction    = lev_data.get("direction", "LONG")

    # Score bar
    filled = round(setup_score / 20)
    bar = "█" * filled + "░" * (5 - filled)

    # Direction label
    dir_icon = "🟢 LONG" if direction == "LONG" else "🔴 SHORT"

    target2_line = ""
    if target2:
        target2_line = (
            f"  Цель 2:  {_p(target2)} "
            f"(<code>+{lev_data['reward2_pct']:.1f}%</code>, "
            f"×{lev} = <code>+{r2:.1f}%</code>)\n"
        )

    # Ликвидация
    liq_line = ""
    if liq > 0:
        liq_warn = "" if liq_safe else "  ⚠️ <b>СТОП ДОЛЖЕН СРАБАТЫВАТЬ ДО ЛИКВИДАЦИИ!</b>\n"
        liq_line = f"  💀 Ликвидация ×{lev}: <code>{_p(liq)}</code>\n{liq_warn}"

    # Предупреждения
    warn_lines = ""
    if funding_note:
        warn_lines += f"  {funding_note}\n"
    if vol_note:
        warn_lines += f"  {vol_note}\n"

    notes_block = ""
    if notes:
        notes_block = "\n📋 " + " | ".join(notes[:3]) + "\n"

    lev_color = "🔴" if lev >= 5 else ("🟡" if lev >= 3 else "🟢")

    return (
        f"{emoji} <b>{signal_status} — {coin.upper()}</b>  {dir_icon}\n"
        f"<code>[{bar}]</code> {setup_score}/100\n\n"

        f"💰 <b>Торговый план:</b>\n"
        f"  Вход:    {_p(entry_low)} — {_p(entry_high)}\n"
        f"  Стоп:    {_p(stop)} (<code>-{lev_data['risk_pct']:.1f}%</code>)\n"
        f"  Цель 1:  {_p(target1)} (<code>+{lev_data['reward1_pct']:.1f}%</code>)\n"
        f"{target2_line}"
        f"  R/R:     <code>{rr:.2f}:1</code>\n\n"

        f"{lev_color} <b>Плечо: {lev}x</b>  |  {conf}\n"
        f"  Риск (стоп ×{lev}):  <code>-{risk:.1f}%</code>\n"
        f"  Доход T1 (×{lev}):   <code>+{r1:.1f}%</code>\n"
        f"  Доход T2 (×{lev}):   <code>+{r2:.1f}%</code>\n"
        f"{liq_line}"
        f"{warn_lines}\n"

        f"📐 <b>Размер позиции</b> (риск 2% депо):\n"
        f"  Маржа: <code>~{marg:.0f}%</code> депозита\n"
        f"  Пример $1000 депо → маржа ~<code>${marg*10:.0f}</code>\n"

        f"{notes_block}\n"
        f"<i>⚠️ Аналитика, не финансовый совет.\n"
        f"Всегда ставь стоп-лосс. Управляй рисками.</i>"
    )


def _price_decimals(price: float) -> int:
    if price >= 1000: return 1
    elif price >= 10: return 2
    elif price >= 1:  return 3
    elif price >= 0.1: return 4
    else: return 6
