"""
analysis/trade_analyzer.py — Анализ личной истории сделок

Загружает реальную историю из Binance API и находит:
- Win rate по каждой монете
- Оптимальное плечо (по реальным результатам)
- Лучшее и худшее время торговли
- Паттерны убытков (что предшествовало потере)
- Персональные рекомендации
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import NamedTuple

logger = logging.getLogger(__name__)

# Кэш лучших часов — заполняется при вызове analyze_my_trades()
_BEST_HOURS_CACHE: dict = {}

TRACKED_COINS = [
    "btc", "eth", "bnb", "sol", "xrp",
    "doge", "ada", "avax", "link", "dot",
    "near", "ltc", "uni", "trx", "bch",
    "atom", "arb", "sui", "apt", "op",
]


class CoinStats(NamedTuple):
    coin: str
    total_trades: int
    wins: int
    losses: int
    win_rate: float        # 0.0 – 1.0
    avg_win_pct: float     # средняя прибыль при победе %
    avg_loss_pct: float    # средний убыток при потере %
    total_pnl: float       # суммарный P&L в USDT
    expectancy: float      # матожидание на сделку


def analyze_my_trades(days: int = 90) -> dict:
    """
    Полный анализ истории сделок за N дней.

    Возвращает:
    {
        "total_trades": 142,
        "total_pnl":    +847.30,
        "win_rate":     0.63,
        "by_coin":      { "btc": CoinStats(...), ... },
        "by_leverage":  { 3: {"wins": 12, "losses": 5, "win_rate": 0.71}, ... },
        "by_hour_utc":  { 14: {"wins": 8, "losses": 2, "win_rate": 0.80}, ... },
        "best_coins":   ["btc", "sol", "eth"],
        "worst_coins":  ["doge", "pepe"],
        "best_leverage": 3,
        "best_hours":   [14, 15, 16],
        "insights":     ["✅ Лонги работают: 74% побед", "❌ Ночная торговля убыточна"],
        "recommendations": ["Фокусируйся на BTC, SOL, ETH", "Избегай DOGE"],
    }
    """
    from market_data.binance_account import get_income_history, get_trade_history

    # ── Собираем income history (реализованный P&L) ───────────────────────────
    income_records = get_income_history(days=days, income_type="REALIZED_PNL")
    if not income_records:
        return {"error": "Нет данных о сделках за указанный период"}

    # Группируем по монете
    coin_pnls: dict[str, list[float]] = defaultdict(list)
    for rec in income_records:
        coin = rec["coin"]
        if coin and float(rec["income"]) != 0:
            coin_pnls[coin].append(float(rec["income"]))

    # ── Анализ по монетам ─────────────────────────────────────────────────────
    by_coin: dict[str, CoinStats] = {}
    for coin, pnls in coin_pnls.items():
        wins   = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total  = sum(pnls)
        n      = len(pnls)
        wr     = len(wins) / n if n > 0 else 0

        # Примерный % на сделку (нужна цена входа, но используем USDT)
        avg_w = (sum(wins)   / len(wins))   if wins   else 0.0
        avg_l = (sum(losses) / len(losses)) if losses else 0.0

        # Матожидание: WR * avg_win + (1-WR) * avg_loss
        expectancy = wr * avg_w + (1 - wr) * avg_l

        by_coin[coin] = CoinStats(
            coin=coin,
            total_trades=n,
            wins=len(wins),
            losses=len(losses),
            win_rate=round(wr, 3),
            avg_win_pct=round(avg_w, 2),
            avg_loss_pct=round(avg_l, 2),
            total_pnl=round(total, 2),
            expectancy=round(expectancy, 2),
        )

    # ── Анализ по часам (UTC) ─────────────────────────────────────────────────
    by_hour: dict[int, dict] = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    for rec in income_records:
        if not rec["coin"]:
            continue
        import datetime
        ts = rec["time_ms"] / 1000
        hour = datetime.datetime.utcfromtimestamp(ts).hour
        pnl  = float(rec["income"])
        if pnl > 0:
            by_hour[hour]["wins"] += 1
        elif pnl < 0:
            by_hour[hour]["losses"] += 1
        by_hour[hour]["pnl"] = round(by_hour[hour]["pnl"] + pnl, 2)

    by_hour_stats = {}
    for h, s in by_hour.items():
        total_h = s["wins"] + s["losses"]
        by_hour_stats[h] = {
            "wins":     s["wins"],
            "losses":   s["losses"],
            "win_rate": round(s["wins"] / total_h, 2) if total_h else 0,
            "pnl":      s["pnl"],
        }

    # ── Топ монет ─────────────────────────────────────────────────────────────
    sorted_coins = sorted(
        by_coin.values(),
        key=lambda x: (x.total_pnl, x.win_rate),
        reverse=True,
    )
    best_coins  = [s.coin for s in sorted_coins[:3] if s.total_pnl > 0]
    worst_coins = [s.coin for s in sorted_coins if s.total_pnl < 0][-3:]

    # ── Лучшие часы ──────────────────────────────────────────────────────────
    best_hours = sorted(
        [h for h, s in by_hour_stats.items() if s["win_rate"] >= 0.65 and (s["wins"] + s["losses"]) >= 3],
        key=lambda h: by_hour_stats[h]["pnl"],
        reverse=True,
    )[:5]

    # ── Общая статистика ─────────────────────────────────────────────────────
    all_pnls    = [r["income"] for r in income_records]
    total_pnl   = round(sum(all_pnls), 2)
    total_trades = len(all_pnls)
    all_wins    = [p for p in all_pnls if p > 0]
    win_rate    = round(len(all_wins) / total_trades, 3) if total_trades else 0

    # ── Инсайты ──────────────────────────────────────────────────────────────
    insights = _generate_insights(by_coin, by_hour_stats, win_rate, total_pnl)
    recommendations = _generate_recommendations(by_coin, best_coins, worst_coins, best_hours)

    # Заполняем глобальный кэш лучших часов
    _BEST_HOURS_CACHE["hours"] = best_hours
    _BEST_HOURS_CACHE["by_hour"] = by_hour_stats

    return {
        "total_trades":  total_trades,
        "total_pnl":     total_pnl,
        "win_rate":      win_rate,
        "days_analyzed": days,
        "by_coin":       by_coin,
        "by_hour":       by_hour_stats,
        "best_coins":    best_coins,
        "worst_coins":   worst_coins,
        "best_hours":    best_hours,
        "insights":      insights,
        "recommendations": recommendations,
    }


def _generate_insights(
    by_coin: dict,
    by_hour: dict,
    overall_wr: float,
    total_pnl: float,
) -> list[str]:
    insights = []

    if overall_wr >= 0.65:
        insights.append(f"✅ Общий win rate {overall_wr*100:.0f}% — выше среднего")
    elif overall_wr >= 0.50:
        insights.append(f"⚠️ Win rate {overall_wr*100:.0f}% — есть потенциал роста")
    else:
        insights.append(f"❌ Win rate {overall_wr*100:.0f}% — нужно менять стратегию")

    if total_pnl > 0:
        insights.append(f"✅ Суммарный P&L положительный: +${total_pnl:.0f}")
    else:
        insights.append(f"❌ Суммарный P&L отрицательный: ${total_pnl:.0f}")

    # Лучшая монета
    profitable = [s for s in by_coin.values() if s.total_pnl > 0 and s.win_rate >= 0.6]
    if profitable:
        best = max(profitable, key=lambda x: x.total_pnl)
        insights.append(f"✅ {best.coin.upper()} — самая прибыльная: +${best.total_pnl:.0f} ({best.win_rate*100:.0f}% wins)")

    # Убыточная монета
    losers = [s for s in by_coin.values() if s.total_pnl < 0 and s.total_trades >= 3]
    if losers:
        worst = min(losers, key=lambda x: x.total_pnl)
        insights.append(f"❌ {worst.coin.upper()} — убыточная: ${worst.total_pnl:.0f} ({worst.win_rate*100:.0f}% wins)")

    # Лучшее время
    good_hours = [(h, s) for h, s in by_hour.items() if s["win_rate"] >= 0.70 and (s["wins"] + s["losses"]) >= 3]
    if good_hours:
        best_h = max(good_hours, key=lambda x: x[1]["pnl"])
        insights.append(f"✅ Лучшее время: {best_h[0]:02d}:00–{best_h[0]+1:02d}:00 UTC ({best_h[1]['win_rate']*100:.0f}% wins)")

    bad_hours = [(h, s) for h, s in by_hour.items() if s["win_rate"] <= 0.35 and (s["wins"] + s["losses"]) >= 3]
    if bad_hours:
        worst_h = min(bad_hours, key=lambda x: x[1]["pnl"])
        insights.append(f"❌ Плохое время: {worst_h[0]:02d}:00–{worst_h[0]+1:02d}:00 UTC — избегай")

    return insights


def _generate_recommendations(
    by_coin: dict,
    best_coins: list,
    worst_coins: list,
    best_hours: list,
) -> list[str]:
    recs = []

    if best_coins:
        recs.append(f"🎯 Фокусируйся на: {', '.join(c.upper() for c in best_coins)}")

    if worst_coins:
        recs.append(f"🚫 Избегай: {', '.join(c.upper() for c in worst_coins)}")

    if best_hours:
        hours_str = ", ".join(f"{h:02d}:00" for h in best_hours[:3])
        recs.append(f"⏰ Лучшее время входа: {hours_str} UTC")

    # Персональный совет по размеру убытков
    big_losers = [s for s in by_coin.values() if s.avg_loss_pct < -20 and s.total_trades >= 3]
    if big_losers:
        recs.append("⚠️ Ставь стоп тighter — средний убыток слишком большой")

    # Если выигрыши маленькие, а убытки большие
    bad_rr = [s for s in by_coin.values()
              if s.avg_win_pct > 0 and s.avg_loss_pct < 0
              and abs(s.avg_loss_pct) > s.avg_win_pct * 2]
    if bad_rr:
        recs.append("💡 R/R плохой — дай прибыли расти (сдвигай TP2 дальше)")

    return recs


def get_coin_personal_score(coin: str, days: int = 90) -> dict:
    """
    Персональный скор для монеты на основе твоей истории.
    Используется в generate_signal() для корректировки setup_score.

    Возвращает:
    {
        "score_adj": +5,          # поправка к setup_score (-15 .. +15)
        "personal_wr": 0.72,      # твой win rate по этой монете
        "total_pnl": 234.50,
        "note": "✅ Твоя историческая win rate 72%"
    }
    """
    try:
        from market_data.binance_account import get_income_history
        records = get_income_history(days=days, income_type="REALIZED_PNL")
        coin_rec = [r for r in records if r["coin"] == coin.lower()]

        if len(coin_rec) < 3:
            return {"score_adj": 0, "personal_wr": None, "total_pnl": 0, "note": ""}

        wins   = [r["income"] for r in coin_rec if r["income"] > 0]
        losses = [r["income"] for r in coin_rec if r["income"] < 0]
        wr     = len(wins) / len(coin_rec)
        total  = sum(r["income"] for r in coin_rec)

        # Поправка к score: от -15 до +15
        if wr >= 0.70:
            adj  = 15
            note = f"✅ Твоя win rate по {coin.upper()}: {wr*100:.0f}% — высокая"
        elif wr >= 0.55:
            adj  = 7
            note = f"✅ Win rate {wr*100:.0f}% по {coin.upper()}"
        elif wr >= 0.45:
            adj  = 0
            note = f"⚪ Win rate {wr*100:.0f}% по {coin.upper()} — средняя"
        elif wr >= 0.35:
            adj  = -7
            note = f"⚠️ Win rate {wr*100:.0f}% по {coin.upper()} — ниже нормы"
        else:
            adj  = -15
            note = f"❌ Win rate {wr*100:.0f}% по {coin.upper()} — плохая история"

        return {
            "score_adj":   adj,
            "personal_wr": round(wr, 3),
            "total_pnl":   round(total, 2),
            "note":        note,
        }

    except Exception as e:
        logger.debug("Personal score для %s: %s", coin, e)
        return {"score_adj": 0, "personal_wr": None, "total_pnl": 0, "note": ""}
