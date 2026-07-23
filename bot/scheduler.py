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
ALERT_COOLDOWN = 4 * 3600  # 4 часа


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
        interval=3600,   # каждый час
        first=120,
        data={"user_id": user_id},
        name="signal_alerts",
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
