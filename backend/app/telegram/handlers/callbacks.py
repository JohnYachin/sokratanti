import logging
from telegram import Update
from telegram.ext import ContextTypes
from app.telegram.handlers import commands

logger = logging.getLogger(__name__)

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard callbacks."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "cmd_top":
        await commands.top_signals(update, context)
    elif data == "cmd_agents":
        await commands.agents_status(update, context)
    elif data == "cmd_portfolio":
        await commands.portfolio(update, context)
    elif data == "cmd_status":
        await commands.system_status(update, context)
    else:
        logger.warning(f"Unknown callback data: {data}")
        await query.message.reply_text("⚠️ Unknown command.")
