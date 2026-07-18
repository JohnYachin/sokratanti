"""
analysis/leverage.py — Модуль рекомендации плеча и риск-менеджмента.

Логика:
  - Плечо определяется setup_score + расстоянием до стопа
  - Чем дальше стоп → меньше плечо (иначе риск ликвидации)
  - Жёсткий cap: max 5x (консервативный подход для retail)
  - Размер позиции: фиксированный риск 2% депозита на сделку

Выход: рекомендация в виде словаря + форматированный текст для Telegram.
"""
from __future__ import annotations


def recommend_leverage(
    setup_score: int,
    entry: float,
    stop: float,
    target1: float,
    target2: float | None = None,
) -> dict:
    """
    Рассчитывает оптимальное плечо и параметры сделки.

    Args:
        setup_score: 0-100
        entry:   цена входа
        stop:    уровень стоп-лосса
        target1: первая цель
        target2: вторая цель (опционально)

    Returns:
        dict с leverage, risk_pct, reward1_pct, reward2_pct, confidence и т.д.
    """
    # Дистанции в %
    risk_pct    = abs(entry - stop) / entry          # 0.028 = 2.8%
    reward1_pct = abs(target1 - entry) / entry        # 0.063 = 6.3%
    reward2_pct = abs(target2 - entry) / entry if target2 else reward1_pct * 1.8
    rr_ratio    = reward1_pct / risk_pct if risk_pct > 0 else 0

    # Базовое плечо по score
    if setup_score >= 85:
        base_lev = 5
        confidence = "🔥 Очень высокая"
        conf_level = 4
    elif setup_score >= 75:
        base_lev = 3
        confidence = "✅ Высокая"
        conf_level = 3
    elif setup_score >= 65:
        base_lev = 2
        confidence = "👀 Умеренная"
        conf_level = 2
    else:
        base_lev = 1
        confidence = "⚠️ Осторожно — минимум"
        conf_level = 1

    # Корректировка: чем дальше стоп, тем меньше плечо
    # (не хотим >10% риска с плечом)
    lev = base_lev
    if risk_pct > 0.06:        # > 6% до стопа → 1x
        lev = 1
    elif risk_pct > 0.04:      # 4-6% → максимум 2x
        lev = min(lev, 2)
    elif risk_pct > 0.025:     # 2.5-4% → максимум 3x
        lev = min(lev, 3)
    # < 2.5% — используем base_lev

    # Итоговый риск и доход с плечом
    risk_lev    = risk_pct    * lev * 100   # % убытка при стопе
    reward1_lev = reward1_pct * lev * 100   # % дохода при T1
    reward2_lev = reward2_pct * lev * 100   # % дохода при T2

    # Размер позиции (% от депо) при риске 2% депо
    # position_size = 2% / risk_pct_unlevered
    # Пример: стоп 3% → позиция = 2%/3% = 67% депо → маржа = 67% / leverage
    pos_size_pct = 2.0 / (risk_pct * 100) if risk_pct > 0 else 100.0
    pos_size_pct = min(pos_size_pct, 100.0)   # max 100% депо
    margin_pct   = pos_size_pct / lev

    return {
        "leverage":     lev,
        "confidence":   confidence,
        "conf_level":   conf_level,
        "risk_pct":     risk_pct * 100,           # % без плеча
        "risk_lev_pct": risk_lev,                  # % с плечом
        "reward1_pct":  reward1_pct * 100,
        "reward1_lev":  reward1_lev,
        "reward2_pct":  reward2_pct * 100,
        "reward2_lev":  reward2_lev,
        "rr_ratio":     rr_ratio,
        "pos_size_pct": pos_size_pct,              # % депо в позицию
        "margin_pct":   margin_pct,                # маржа (депо %)
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

    Включает:
      - Зону входа, стоп, цели
      - Рекомендованное плечо
      - Риск и доход с плечом
      - Рекомендацию по размеру позиции
    """

    def _p(v: float) -> str:
        """Форматирует цену."""
        if v >= 1000:
            return f"${v:,.1f}"
        elif v >= 1:
            return f"${v:.3f}"
        else:
            return f"${v:.5f}"

    lev   = lev_data["leverage"]
    conf  = lev_data["confidence"]
    risk  = lev_data["risk_lev_pct"]
    r1    = lev_data["reward1_lev"]
    r2    = lev_data["reward2_lev"]
    rr    = lev_data["rr_ratio"]
    marg  = lev_data["margin_pct"]

    # Score bar
    filled = round(setup_score / 20)
    bar = "█" * filled + "░" * (5 - filled)

    target2_line = ""
    if target2:
        target2_line = f"  Цель 2: {_p(target2)} (<code>+{lev_data['reward2_pct']:.1f}%</code>, ×{lev} = <code>+{r2:.1f}%</code>)\n"

    notes_block = ""
    if notes:
        notes_block = "\n📋 " + " | ".join(notes[:3]) + "\n"

    lev_color = "🔴" if lev >= 5 else ("🟡" if lev >= 3 else "🟢")

    return (
        f"{emoji} <b>{signal_status} — {coin.upper()}</b>\n"
        f"<code>[{bar}]</code> {setup_score}/100\n\n"
        f"💰 <b>Торговый план:</b>\n"
        f"  Вход:    {_p(entry_low)} — {_p(entry_high)}\n"
        f"  Стоп:    {_p(stop)} (<code>-{lev_data['risk_pct']:.1f}%</code>)\n"
        f"  Цель 1:  {_p(target1)} (<code>+{lev_data['reward1_pct']:.1f}%</code>)\n"
        f"{target2_line}"
        f"  R/R:     <code>{rr:.2f}:1</code>\n\n"
        f"{lev_color} <b>Плечо: {lev}x</b>  |  {conf}\n"
        f"  Риск (стоп ×{lev}):    <code>-{risk:.1f}%</code>\n"
        f"  Доход T1 (×{lev}):  <code>+{r1:.1f}%</code>\n"
        f"  Доход T2 (×{lev}):  <code>+{r2:.1f}%</code>\n\n"
        f"📐 <b>Размер позиции</b> (риск 2% депо):\n"
        f"  Маржа: <code>~{marg:.0f}%</code> депозита\n"
        f"  Пример: $10k депо → маржа ~<code>${marg*100:.0f}</code>\n"
        f"{notes_block}\n"
        f"<i>⚠️ Это аналитика, не финансовый совет.\n"
        f"Всегда ставь стоп-лосс. Управляй рисками.</i>"
    )
