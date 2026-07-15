import os
import logging
from telegram.ext import Application
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

TRACKED_COINS = ["btc", "eth", "sol", "bnb", "doge"]

# Хранит последний сигнал по каждой монете чтобы не спамить повторами
_last_signals: dict[str, str] = {}


def schedule_jobs(app: Application):
    """Регистрирует расписание авто-отчётов и алертов."""
    user_id_str = os.getenv("TELEGRAM_USER_ID")
    if not user_id_str:
        logger.warning("TELEGRAM_USER_ID не задан — авто-задачи отключены.")
        return

    user_id = int(user_id_str)
    interval_hours = float(os.getenv("REPORT_INTERVAL_HOURS", "4"))

    # Большой отчёт каждые N часов
    app.job_queue.run_repeating(
        _send_market_report,
        interval=interval_hours * 3600,
        first=60,
        data={"user_id": user_id},
        name="market_report",
    )

    # Алерты каждые 30 минут
    app.job_queue.run_repeating(
        _check_signal_alerts,
        interval=1800,
        first=120,  # первый прогон через 2 минуты после старта
        data={"user_id": user_id},
        name="signal_alerts",
    )

    logger.info(
        "Запланирован авто-отчёт каждые %.1f ч. и алерты каждые 30 мин.",
        interval_hours,
    )


# ── Авто-отчёт ────────────────────────────────────────────────────────────────
async def _send_market_report(context):
    """Полный авто-отчёт по всем монетам."""
    from data.coingecko import get_price
    from data.feargreed import get_fear_greed
    from analysis.signals import generate_signal

    user_id = context.job.data["user_id"]

    fg = get_fear_greed()
    lines = [
        "⏰ <b>Авто-отчёт рынка</b>\n",
        f"{fg['emoji']} Fear &amp; Greed: <b>{fg['value']}/100</b> — {fg['label_ru']}\n",
    ]

    for coin in TRACKED_COINS:
        try:
            price_data = get_price(coin)
            result = generate_signal(coin)
            ind = result["indicators"]
            ch = price_data["change_24h"]

            lines.append(
                f"{result['emoji']} <b>{coin.upper()}</b> — {result['signal']}\n"
                f"   💵 <code>${price_data['price_usd']:,.2f}</code> "
                f"({'📈' if ch >= 0 else '📉'} {ch:+.1f}%)\n"
                f"   RSI: <code>{ind['rsi']:.1f}</code> | "
                f"MACD: <code>{'▲' if ind['macd_diff'] > 0 else '▼'}</code>\n"
            )
        except Exception as e:
            logger.error("Ошибка для %s: %s", coin, e)
            lines.append(f"⚠️ <b>{coin.upper()}</b>: ошибка данных\n")

    await context.bot.send_message(
        chat_id=user_id,
        text="\n".join(lines),
        parse_mode=ParseMode.HTML,
    )


# ── Алерты ────────────────────────────────────────────────────────────────────
async def _check_signal_alerts(context):
    """
    Каждые 30 минут проверяет сигналы.
    Отправляет уведомление только если сигнал сменился на BUY или SELL.
    """
    from data.coingecko import get_price
    from analysis.signals import generate_signal

    user_id = context.job.data["user_id"]

    for coin in TRACKED_COINS:
        try:
            result = generate_signal(coin)
            signal = result["signal"]
            prev = _last_signals.get(coin)

            # Отправляем только при смене на BUY или SELL
            if signal != prev and signal in ("BUY", "SELL"):
                price_data = get_price(coin)
                price = price_data["price_usd"]
                ch = price_data["change_24h"]
                ind = result["indicators"]

                action = "🟢 ПОКУПАТЬ" if signal == "BUY" else "🔴 ПРОДАВАТЬ"
                reasons_html = "\n".join(f"  • {r}" for r in result["reasons"])

                text = (
                    f"🚨 <b>АЛЕРТ: {coin.upper()} → {action}</b>\n\n"
                    f"💵 Цена: <code>${price:,.2f}</code> "
                    f"({'📈' if ch >= 0 else '📉'} {ch:+.1f}%)\n\n"
                    f"📊 <b>Причины:</b>\n{reasons_html}\n\n"
                    f"🎯 Оценка: <code>{result['score']:+d}</code>\n"
                    f"⏰ RSI: <code>{ind['rsi']:.1f}</code>"
                )

                await context.bot.send_message(
                    chat_id=user_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                )
                logger.info("Алерт отправлен: %s → %s", coin.upper(), signal)

            _last_signals[coin] = signal

        except Exception as e:
            logger.error("Алерт-ошибка для %s: %s", coin, e)
