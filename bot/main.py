import os
import logging
from dotenv import load_dotenv
from telegram.ext import Application, CommandHandler

load_dotenv()

from bot.handlers import (
    cmd_start,
    cmd_price,
    cmd_signal,
    cmd_sentiment,
    cmd_history,
    cmd_coins,
    cmd_feargreed,
    cmd_news,
    cmd_analyze,
    cmd_scan,
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


def main():
    """Точка входа — запуск Telegram-бота."""
    init_db()
    logger.info("База данных инициализирована.")

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("❌ TELEGRAM_BOT_TOKEN не найден в .env файле!")

    app = Application.builder().token(token).build()

    # --- Команды ---
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("price", cmd_price))
    app.add_handler(CommandHandler("signal", cmd_signal))
    app.add_handler(CommandHandler("sentiment", cmd_sentiment))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("coins", cmd_coins))
    app.add_handler(CommandHandler("feargreed", cmd_feargreed))
    app.add_handler(CommandHandler("news", cmd_news))
    app.add_handler(CommandHandler("analyze", cmd_analyze))
    app.add_handler(CommandHandler("scan", cmd_scan))

    # --- Автоматические отчёты ---
    schedule_jobs(app)

    logger.info("🤖 Sokratanti запущен. Ctrl+C для остановки.")
    app.run_polling(drop_pending_updates=True)
