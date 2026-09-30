import os
import logging
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler

load_dotenv()

from bot.handlers import (
    cmd_start,
    cmd_price,
    cmd_futures,
    cmd_signal,
    cmd_sentiment,
    cmd_history,
    cmd_coins,
    cmd_feargreed,
    cmd_news,
    cmd_analyze,
    cmd_scan,
    cmd_backtest,
    cmd_optimize,
    cmd_params,
    cmd_results,
    cmd_account,
    cmd_myhistory,
    cmd_strategy,
    cmd_report,
)
from bot.portfolio_handlers import (
    cmd_portfolio,
    cmd_add,
    cmd_remove,
    cmd_remove_id,
)
from bot.scheduler import schedule_jobs
from db.database import init_db

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


async def error_handler(update, context):
    """Логирует все необработанные ошибки."""
    logger.error("Unhandled exception in handler:", exc_info=context.error)
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка при обработке команды. Попробуй ещё раз."
            )
        except Exception:
            pass


def main():
    """Точка входа — запуск Telegram-бота."""
    init_db()
    logger.info("База данных инициализирована.")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("❌ TELEGRAM_BOT_TOKEN не найден в .env файле!")

    app = Application.builder().token(token).build()

    # --- Обработчик ошибок ---
    app.add_error_handler(error_handler)

    # --- Команды ---
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("futures", cmd_futures))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("sentiment", cmd_sentiment))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("coins", cmd_coins))
    app.add_handler(CommandHandler("feargreed", cmd_feargreed))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("scan", cmd_scan))
    # Portfolio
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("remove_id", cmd_remove_id))
    # Backtest
    app.add_handler(CommandHandler("backtest", cmd_backtest))
    # Adaptive Learning
    app.add_handler(CommandHandler("optimize", cmd_optimize))
    app.add_handler(CommandHandler("params", cmd_params))
    app.add_handler(CommandHandler("results", cmd_results))
    # Binance Account API
    app.add_handler(CommandHandler("account", cmd_account))
    app.add_handler(CommandHandler("myhistory", cmd_myhistory))
    # Персональная стратегия
    app.add_handler(CommandHandler("strategy", cmd_strategy))
    # Полный отчёт (история + рынок сейчас)
    app.add_handler(CommandHandler("report", cmd_report))

    # --- Автоматические отчёты ---
    schedule_jobs(app)

    logger.info("🤖 Sokratanti запущен. Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=False)
