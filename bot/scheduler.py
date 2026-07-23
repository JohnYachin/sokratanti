"""
bot/scheduler.py — Планировщик v2.0

Изменения:
  - _last_signals перенесён из RAM в БД (таблица alert_state)
  - Алерты теперь идемпотентны: ключ coin:status:hour предотвращает повторы
  - Cooldown 4 часа между алертами по одной монете
  - Алерты по новым статусам: BUY_ZONE, STRONG_SETUP, WATCH, EVENT_RISK
  - Авто-отчёт обновлён для нового формата сигналов (setup_score)
"""
import asyncio
import os
import logging
from telegram.ext import Application
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

TRACKED_COINS = [
    # Топ-10
    "btc", "eth", "bnb", "sol", "xrp",
    "doge", "ada", "avax", "link", "dot",
    # Топ 11-20
    "near", "ltc", "uni", "shib", "trx",
    "bch", "atom", "xlm", "etc", "fil",
    # Топ 21-30
    "arb", "sui", "pepe", "apt", "hbar",
    "icp", "vet", "mkr", "aave", "op",
]

# Статусы, по которым шлём алерт
ALERT_STATUSES = {"STRONG_SETUP", "BUY_ZONE", "EVENT_RISK"}
# Cooldown между алертами одной монеты (секунды)
ALERT_COOLDOWN = 2 * 3600  # 2 часа


def schedule_jobs(app: Application):
    """Регистрирует расписание авто-отчётов и алертов."""
    user_id_str = os.getenv("TELEGRAM_USER_ID")
    if not user_id_str:
        logger.warning("TELEGRAM_USER_ID не задан — авто-задачи отключены.")
        return

    user_id = int(user_id_str)
    interval_hours = float(os.getenv("REPORT_INTERVAL_HOURS", "4"))

    app.job_queue.run_repeating(
        _send_market_report,
        interval=interval_hours * 3600,
        first=60,
        data={"user_id": user_id},
        name="market_report",
    )
    app.job_queue.run_repeating(
        _check_signal_alerts,
        interval=1800,   # каждые 30 минут
        first=90,
        data={"user_id": user_id},
        name="signal_alerts",
    )
    # Обзор возможностей каждые 30 минут — все монеты с точками входа
    app.job_queue.run_repeating(
        _send_opportunities_report,
        interval=1800,   # каждые 30 минут
        first=30,
        data={"user_id": user_id},
        name="opportunities_report",
    )
    # Трекинг результатов каждый час
    app.job_queue.run_repeating(
        _check_signal_outcomes,
        interval=3600,
        first=300,
        data={"user_id": user_id},
        name="signal_outcomes",
    )
    # Еженедельная авто-оптимизация (воскресенье 03:00 UTC)
    import datetime as _dt
    app.job_queue.run_daily(
        _weekly_optimize,
        time=_dt.time(3, 0, tzinfo=_dt.timezone.utc),
        days=(6,),
        data={"user_id": user_id},
        name="weekly_optimize",
    )
    logger.info(
        "Запланирован авто-отчёт каждые %.1f ч., алерты каждый час, трекинг каждый час, оптимизация по воскресеньям.",
        interval_hours,
    )


# ── 30-минутный обзор возможностей (LONG + SHORT + AI) ───────────────────────
async def _send_opportunities_report(context):
    """
    Каждые 30 минут анализирует все 30 монет.
    Показывает LONG и SHORT возможности с entry/SL/TP1/TP2.
    Использует Perplexity AI для рыночного контекста.
    """
    from analysis.signals import generate_signal

    user_id = context.job.data["user_id"]

    def fp(v):
        if v is None: return "—"
        if v >= 1000: return f"${v:,.0f}"
        if v >= 1:    return f"${v:,.2f}"
        return f"${v:.5f}"

    def pct(entry, target):
        if not entry or not target: return ""
        p = (target - entry) / entry * 100
        sign = "+" if p >= 0 else ""
        return f"({sign}{p:.1f}%)"

    longs  = []   # LONG возможности
    shorts = []   # SHORT возможности
    watch  = []   # На наблюдении

    for coin in TRACKED_COINS:
        try:
            result = await asyncio.to_thread(generate_signal, coin)
            status    = result.get("status", "NO_EDGE")
            direction = result.get("direction", "NEUTRAL")
            score     = result.get("setup_score", 0)
            short_score = result.get("short_score", 0)
            plan      = result.get("trading_plan")
            short_plan = result.get("short_plan")
            emoji     = result.get("emoji", "📊")
            action    = result.get("action", "")
            trend     = result.get("trend", "unknown")
            ind       = result.get("indicators", {})
            rsi       = ind.get("rsi")

            trend_icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(trend, "❓")
            rsi_str = f"RSI {rsi:.0f}" if rsi else ""

            # ── LONG сигнал ──────────────────────────────────────────────
            if direction == "LONG" and plan and plan.setup_valid:
                longs.append({
                    "coin": coin.upper(), "emoji": emoji, "score": score,
                    "action": action, "trend_icon": trend_icon, "rsi_str": rsi_str,
                    "entry_l": plan.entry_low, "entry_h": plan.entry_high,
                    "sl": plan.stop_loss, "tp1": plan.target_1, "tp2": plan.target_2,
                    "rr": plan.risk_reward or 0,
                })

            # ── SHORT сигнал ─────────────────────────────────────────────
            elif direction == "SHORT" and short_plan and short_plan.setup_valid:
                shorts.append({
                    "coin": coin.upper(), "emoji": emoji, "score": short_score,
                    "action": action, "trend_icon": trend_icon, "rsi_str": rsi_str,
                    "entry_l": short_plan.entry_low, "entry_h": short_plan.entry_high,
                    "sl": short_plan.stop_loss, "tp1": short_plan.target_1,
                    "tp2": short_plan.target_2, "rr": short_plan.risk_reward or 0,
                })

            # ── Наблюдение ───────────────────────────────────────────────
            elif status == "WATCH" and score >= 40:
                watch.append(
                    f"👀 <b>{coin.upper()}</b> {trend_icon} "
                    f"— {result.get('label_ru','WATCH')} ({score}/100) {rsi_str}"
                )

        except Exception as e:
            logger.error("OpReport %s: %s", coin, e)

    now_str = __import__("datetime").datetime.now().strftime("%H:%M %d.%m")
    longs.sort(key=lambda x: x["score"], reverse=True)
    shorts.sort(key=lambda x: x["score"], reverse=True)

    # ── Нет сигналов ────────────────────────────────────────────────────
    if not longs and not shorts:
        text = (
            f"⏰ <b>Обзор рынка {now_str}</b>\n\n"
            "😴 Чётких сигналов (LONG/SHORT) нет.\n"
            f"Монеты на наблюдении: {len(watch)}\n\n"
        )
        if watch:
            text += "\n".join(watch[:5]) + "\n\n"
        text += "<i>Следующий анализ через 30 минут.</i>"
        await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
        return

    # ── Строим сообщение ─────────────────────────────────────────────────
    lines = [
        f"📊 <b>АНАЛИЗ РЫНКА — {now_str}</b>\n"
        f"🟢 LONG: <b>{len(longs)}</b>  🔴 SHORT: <b>{len(shorts)}</b>  из 30 монет\n"
    ]

    # ── AI комментарий от Perplexity ─────────────────────────────────────
    if longs or shorts:
        try:
            from data.perplexity import get_market_analysis
            top_coins = [o["coin"] for o in (longs + shorts)[:3]]
            coins_str = ", ".join(top_coins)
            ai_resp = await asyncio.to_thread(
                get_market_analysis, coins_str
            )
            if ai_resp.get("analysis"):
                ai_text = ai_resp["analysis"].strip()[:350]
                lines.append(f"\n🤖 <b>AI (Perplexity):</b>\n<i>{ai_text}</i>\n")
        except Exception as e:
            logger.debug("Perplexity AI skip: %s", e)

    # ── LONG блок ────────────────────────────────────────────────────────
    if longs:
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🟢 <b>LONG — ПОКУПАТЬ</b>")
        for o in longs:
            em = (o['entry_l'] + o['entry_h']) / 2
            bar = "█" * round(o['score']/10) + "░" * (10 - round(o['score']/10))
            block = (
                f"\n{o['emoji']} <b>{o['coin']}</b> {o['trend_icon']} "
                f"[<code>{bar}</code>] {o['score']}/100"
                f"\n  🎯 {o['action']}"
                f" | {o['rsi_str']}"
                f"\n  📥 Вход:  <code>{fp(o['entry_l'])} — {fp(o['entry_h'])}</code>"
                f"\n  🛑 SL:    <code>{fp(o['sl'])}</code> {pct(em, o['sl'])}"
                f"\n  ✅ TP1:   <code>{fp(o['tp1'])}</code> {pct(em, o['tp1'])}"
            )
            if o['tp2']:
                block += f"\n  🚀 TP2:   <code>{fp(o['tp2'])}</code> {pct(em, o['tp2'])}"
            block += f"\n  ⚖️ R/R    <code>1:{o['rr']:.1f}</code>"
            lines.append(block)

    # ── SHORT блок ───────────────────────────────────────────────────────
    if shorts:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔴 <b>SHORT — ПРОДАВАТЬ</b>")
        for o in shorts:
            em = (o['entry_l'] + o['entry_h']) / 2
            bar = "█" * round(o['score']/10) + "░" * (10 - round(o['score']/10))
            block = (
                f"\n{o['emoji']} <b>{o['coin']}</b> {o['trend_icon']} "
                f"[<code>{bar}</code>] {o['score']}/100"
                f"\n  🎯 {o['action']}"
                f" | {o['rsi_str']}"
                f"\n  📤 Шорт:  <code>{fp(o['entry_l'])} — {fp(o['entry_h'])}</code>"
                f"\n  🛑 SL:    <code>{fp(o['sl'])}</code> {pct(em, o['sl'])}"
                f"\n  ✅ TP1:   <code>{fp(o['tp1'])}</code> {pct(em, o['tp1'])}"
            )
            if o['tp2']:
                block += f"\n  🚀 TP2:   <code>{fp(o['tp2'])}</code> {pct(em, o['tp2'])}"
            block += f"\n  ⚖️ R/R    <code>1:{o['rr']:.1f}</code>"
            lines.append(block)

    # ── Наблюдение ───────────────────────────────────────────────────────
    if watch:
        lines.append("\n━━━━━━━━━━━━━━━━━━━━")
        lines.append("👀 <b>На наблюдении:</b>")
        lines.extend(watch[:4])

    lines.append("\n<i>Данные: Binance 1h+4h+1d + CoinGecko + Perplexity AI</i>")
    lines.append("<i>Следующий обзор через 30 минут.</i>")

    text = "\n".join(lines)
    if len(text) > 4050:
        text = text[:4000] + "\n\n<i>...ещё монеты. Используй /scan</i>"

    await context.bot.send_message(chat_id=user_id, text=text, parse_mode="HTML")
    logger.info("OpReport: %d LONG, %d SHORT отправлено", len(longs), len(shorts))



# ── Авто-отчёт ──────────────────────────────────────────────────────────────
async def _send_market_report(context):
    """Полный авто-отчёт по всем монетам с setup_score."""
    from data.coingecko import get_price
    from data.feargreed import get_fear_greed
    from analysis.signals import generate_signal

    user_id = context.job.data["user_id"]

    try:
        fg = await asyncio.to_thread(get_fear_greed)
        fg_line = f"{fg['emoji']} Fear &amp; Greed: <b>{fg['value']}/100</b> — {fg['label_ru']}\n"
    except Exception:
        fg_line = "Fear &amp; Greed: нет данных\n"

    lines = ["⏰ <b>Авто-отчёт рынка</b>\n", fg_line]

    for coin in TRACKED_COINS:
        try:
            price_data = await asyncio.to_thread(get_price, coin)
            result = await asyncio.to_thread(generate_signal, coin)
            ind = result["indicators"]
            ch = price_data["change_24h"]
            setup_score = result.get("setup_score", 0)
            label_ru = result.get("label_ru", result["signal"])

            # Прогресс-бар setup score (5 символов)
            filled = round(setup_score / 20)
            bar = "█" * filled + "░" * (5 - filled)

            lines.append(
                f"{result['emoji']} <b>{coin.upper()}</b> — {label_ru} "
                f"<code>[{bar}]</code> {setup_score}/100\n"
                f"   💵 <code>${price_data['price_usd']:,.2f}</code> "
                f"({'📈' if ch >= 0 else '📉'} {ch:+.1f}%)\n"
                f"   RSI: <code>{ind['rsi']:.1f}</code> | "
                f"Тренд: {_trend_emoji(result.get('trend', 'unknown'))}\n"
            )
        except Exception as e:
            logger.error("Авто-отчёт: ошибка для %s: %s", coin, e)
            lines.append(f"⚠️ <b>{coin.upper()}</b>: ошибка данных\n")

    await context.bot.send_message(
        chat_id=user_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


def _trend_emoji(trend: str) -> str:
    return {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(trend, "❓")


# ── Алерты (из БД, не из RAM) ───────────────────────────────────────────────
async def _check_signal_alerts(context):
    """
    Каждые 30 минут проверяет сигналы по всем монетам.
    Алерт отправляется только при:
      1. Статус попал в ALERT_STATUSES
      2. Этот статус не был отправлен за последние ALERT_COOLDOWN секунд
    Состояние хранится в БД — переживает рестарты.
    """
    from data.coingecko import get_price
    from analysis.signals import generate_signal
    from analysis.leverage import recommend_leverage, format_trade_signal
    from db.database import alert_already_sent, record_alert, signal_trade_open

    user_id = context.job.data["user_id"]

    for coin in TRACKED_COINS:
        try:
            result = await asyncio.to_thread(generate_signal, coin)
            status = result.get("status", "NO_EDGE")

            if status not in ALERT_STATUSES:
                continue

            from datetime import datetime, timezone
            hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
            idem_key = f"{coin}:{status}:{hour_key}"

            if alert_already_sent(idem_key, cooldown_seconds=ALERT_COOLDOWN):
                logger.debug("Алерт %s уже отправлен, пропускаем", idem_key)
                continue

            # Цена и торговый план
            price_data = await asyncio.to_thread(get_price, coin)
            price      = price_data["price_usd"]
            ch         = price_data.get("change_24h", 0.0)
            setup_score = result.get("setup_score", 0)
            plan = result.get("trading_plan")

            # Строим сообщение с плечом
            if plan and plan.setup_valid:
                entry_low  = float(plan.entry_low)
                entry_high = float(plan.entry_high)
                stop       = float(plan.stop_loss)
                t1         = float(plan.target1)
                t2         = float(plan.target2) if plan.target2 else None
                entry_mid  = (entry_low + entry_high) / 2

                lev_data = recommend_leverage(setup_score, entry_mid, stop, t1, t2)

                # Краткие причины
                reasons_short = result["reasons"][:4]

                text = format_trade_signal(
                    coin=coin,
                    setup_score=setup_score,
                    signal_status=result.get("label_ru", status),
                    emoji=result.get("emoji", "📊"),
                    entry_low=entry_low,
                    entry_high=entry_high,
                    stop=stop,
                    target1=t1,
                    target2=t2,
                    lev_data=lev_data,
                    notes=reasons_short,
                )

                # Сохраняем в БД для трекинга
                trade_id = signal_trade_open(
                    coin=coin,
                    signal_type=status,
                    setup_score=setup_score,
                    entry_price=entry_mid,
                    stop_loss=stop,
                    target1=t1,
                    target2=t2,
                    leverage=lev_data["leverage"],
                )
                logger.info("Signal trade saved: id=%d %s %s lev=%dx",
                            trade_id, coin.upper(), status, lev_data["leverage"])
            else:
                # Нет плана — упрощённый алерт
                reasons_html = "\n".join(f"  • {r}" for r in result["reasons"][:5])
                text = (
                    f"🚨 <b>АЛЕРТ: {coin.upper()} → {result['emoji']} {result.get('label_ru', status)}</b>\n"
                    f"Score: <code>{setup_score}/100</code>\n\n"
                    f"💵 <code>${price:,.4f}</code> "
                    f"({'📈' if ch >= 0 else '📉'} {ch:+.1f}%)\n\n"
                    f"{reasons_html}"
                )

            await context.bot.send_message(
                chat_id=user_id, text=text, parse_mode=ParseMode.HTML,
            )
            record_alert(idem_key, coin, status, price)
            logger.info("Алерт отправлен: %s → %s (score=%d)", coin.upper(), status, setup_score)

        except Exception as e:
            logger.error("Алерт-ошибка для %s: %s", coin, e)



# ── Еженедельная авто-оптимизация ────────────────────────────────────────────
async def _weekly_optimize(context):
    """
    Запускается каждое воскресенье 03:00 UTC.
    Прогоняет grid search + indicator analysis для всех монет.
    Результаты сохраняются в БД и применяются автоматически к сигналам.
    """
    from analysis.optimizer import full_optimize

    user_id = context.job.data["user_id"]
    logger.info("=== Еженедельная авто-оптимизация STARTED ===")

    results = []
    for coin in TRACKED_COINS:
        try:
            res = await asyncio.to_thread(full_optimize, coin, "1d", 180)
            line = res["summary"].split("\n")[1] if res["summary"] else "OK"
            results.append(f"✅ {coin.upper()}: {line}")
            logger.info("Weekly optimize %s: OK", coin.upper())
        except Exception as e:
            results.append(f"❌ {coin.upper()}: {e}")
            logger.error("Weekly optimize %s: %s", coin.upper(), e)

    report = (
        "🎓 <b>Авто-оптимизация завершена</b>\n\n" +
        "\n".join(results) +
        "\n\n<i>Параметры применяются к следующим сигналам. /params BTC для деталей.</i>"
    )
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=report,
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        logger.error("Weekly optimize report send: %s", e)

    logger.info("=== Еженедельная авто-оптимизация DONE ===")


# ── Трекинг результатов сигналов ─────────────────────────────────────────────
async def _check_signal_outcomes(context):
    """
    Запускается каждый час.
    Для каждого открытого сигнала проверяет текущую цену:
      - Достигнута цель 1 → WIN  → уведомление + закрытие
      - Задет стоп       → LOSS → уведомление + закрытие
      - Прошло > 15 дней → TIMEOUT → тихое закрытие
    """
    from data.coingecko import get_price
    from db.database import signal_trades_get_open, signal_trade_close
    from datetime import datetime, timezone

    user_id      = context.job.data["user_id"]
    open_signals = signal_trades_get_open()
    if not open_signals:
        return

    logger.info("Outcome check: %d открытых сигналов", len(open_signals))

    for sig in open_signals:
        coin     = sig["coin"]
        trade_id = sig["id"]
        entry    = float(sig["entry_price"])
        stop     = float(sig["stop_loss"])
        t1       = float(sig["target1"])
        t2       = float(sig["target2"]) if sig.get("target2") else None
        leverage = int(sig.get("leverage", 1))
        sig_type = sig["signal_type"]
        created_at = sig["created_at"]

        try:
            price_data = await asyncio.to_thread(get_price, coin)
            cur_price  = float(price_data["price_usd"])
            pnl_pct     = (cur_price - entry) / entry
            pnl_lev_pct = pnl_pct * leverage

            outcome = None; exit_price = None; note = ""

            if cur_price <= stop:
                outcome = "loss"; exit_price = cur_price
                note = f"Стоп задет: ${cur_price:,.4f}"
            elif cur_price >= t1:
                outcome = "win"; exit_price = cur_price
                hit = "T2" if t2 and cur_price >= t2 else "T1"
                note = f"{hit} достигнута: ${cur_price:,.4f}"
            else:
                try:
                    if isinstance(created_at, str):
                        created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    else:
                        created_dt = created_at
                    if (datetime.now(timezone.utc) - created_dt).days >= 15:
                        outcome = "timeout"; exit_price = cur_price
                        note = f"Таймаут: ${cur_price:,.4f}"
                except Exception:
                    pass

            if outcome is None:
                continue

            signal_trade_close(trade_id, outcome, exit_price,
                               pnl_pct * 100, pnl_lev_pct * 100, note)

            if outcome in ("win", "loss"):
                sign = "+" if pnl_lev_pct >= 0 else ""
                icon = "✅" if outcome == "win" else "❌"
                text = (
                    f"{icon} <b>Результат: {coin.upper()}</b>\n\n"
                    f"{'🎯' if outcome=='win' else '🛑'} {note}\n\n"
                    f"📊 Сигнал: <b>{sig_type}</b>\n"
                    f"  Вход:  <code>${entry:,.4f}</code>\n"
                    f"  Выход: <code>${exit_price:,.4f}</code>\n\n"
                    f"💰 P&L без плеча:   <code>{'+' if pnl_pct>=0 else ''}{pnl_pct*100:.2f}%</code>\n"
                    f"💥 P&L с плечом ×{leverage}: <code>{sign}{pnl_lev_pct*100:.2f}%</code>\n\n"
                    f"<i>/results — вся статистика сигналов</i>"
                )
                await context.bot.send_message(
                    chat_id=user_id, text=text, parse_mode=ParseMode.HTML,
                )
                logger.info("Outcome %s: %s %s pnl=%.1f%% lev×%d=%.1f%%",
                            outcome, coin.upper(), sig_type,
                            pnl_pct*100, leverage, pnl_lev_pct*100)

        except Exception as e:
            logger.error("Outcome check error %s #%d: %s", coin, trade_id, e)
