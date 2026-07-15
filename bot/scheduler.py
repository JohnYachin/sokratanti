import os
import logging
from telegram.ext import Application
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)

TRACKED_COINS = ["btc", "eth", "sol", "bnb", "doge"]


def schedule_jobs(app: Application):
    """Регистрирует расписание авто-отчётов."""
    user_id_str = os.getenv("TELEGRAM_USER_ID")
    if not user_id_str:
        logger.warning("TELEGRAM_USER_ID не задан — авто-отчёты отключены.")
        return

    interval_hours = float(os.getenv("REPORT_INTERVAL_HOURS", "4"))
    interval_seconds = interval_hours * 3600

    app.job_queue.run_repeating(
        _send_market_report,
        interval=interval_seconds,
        first=60,  # первый запуск через 1 минуту после старта
        data={"user_id": int(user_id_str)},
        name="market_report",
    )
    logger.info("Запланирован авто-отчёт каждые %.1f ч.", interval_hours)


async def _send_market_report(context):
    """Автоматический отчёт по всем монетам."""
    from data.coingecko import get_price
    from data.feargreed import get_fear_greed
    from analysis.signals import generate_signal

    user_id = context.job.data["user_id"]

    # Fear & Greed в шапке
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
