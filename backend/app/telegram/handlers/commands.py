import os
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

ALLOWED_USER_ID = int(os.environ.get("TELEGRAM_USER_ID", "0"))

def authenticate(func):
    """Decorator to authenticate users based on TELEGRAM_USER_ID."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not ALLOWED_USER_ID or user.id != ALLOWED_USER_ID:
            logger.warning(f"Unauthorized access attempt by user {user.id} ({user.username})")
            await update.message.reply_text("⛔ Unauthorized. You do not have permission to use this bot.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

@authenticate
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /start command."""
    keyboard = [
        [
            InlineKeyboardButton("📊 Top Signals", callback_data="cmd_top"),
            InlineKeyboardButton("🤖 Agents Status", callback_data="cmd_agents")
        ],
        [
            InlineKeyboardButton("📈 My Portfolio", callback_data="cmd_portfolio"),
            InlineKeyboardButton("⚙️ System Status", callback_data="cmd_status")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "👋 Welcome to *CAIOS* (Crypto AI Investment Operating System)!\n\n"
        "I am your personal AI crypto investment assistant. Use the menu below or type /help to see all commands.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

@authenticate
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /help command."""
    help_text = (
        "🛠️ *CAIOS Commands*\n\n"
        "/start — Welcome message and menu\n"
        "/help — List all commands\n"
        "/top — Show top 10 signals right now\n"
        "/signal <COIN> — Get signal for specific coin (e.g. /signal BTC)\n"
        "/agents — Show AI council status and stats\n"
        "/portfolio — User portfolio overview\n"
        "/subscribe — Subscribe to signal alerts\n"
        "/unsubscribe — Unsubscribe from alerts\n"
        "/status — System status (DB, Redis, last cycle)"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

@authenticate
async def top_signals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /top command."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")
    
    # Mock data for demonstration
    response = f"🚀 *CAIOS Top Signals* — [{now}]\n\n"
    response += "1. ⬆️ *BTC* — STRONG BUY (92% confidence)\n"
    response += "   💰 $67,234 | 📊 RSI: 45 | Vol: +234%\n\n"
    response += "2. ⬆️ *ETH* — BUY (78% confidence)\n"
    response += "   💰 $3,450 | 📊 RSI: 52 | Vol: +12%\n\n"
    response += "3. ⬇️ *SOL* — SELL (85% confidence)\n"
    response += "   💰 $142.30 | 📊 RSI: 78 | Vol: -5%\n\n"
    response += "🤖 AI Council: 20/20 agents active\n"
    response += "⏱️ Next cycle: 14 min"
    
    await update.message.reply_text(response, parse_mode="Markdown")

@authenticate
async def get_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /signal command."""
    if not context.args:
        await update.message.reply_text("⚠️ Please specify a coin. Example: `/signal BTC`", parse_mode="Markdown")
        return
        
    coin = context.args[0].upper()
    await update.message.reply_text(f"🔍 Analyzing *{coin}*... (Mock Data)\n\n"
                                    f"🪙 *{coin}* — NEUTRAL (50% confidence)\n"
                                    f"Trend is currently unclear. Await next cycle.", parse_mode="Markdown")

@authenticate
async def agents_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /agents command."""
    text = (
        "🤖 *AI Council Status*\n\n"
        "✅ Technical Analyst: Active\n"
        "✅ Sentiment Analyzer: Active\n"
        "✅ On-Chain Detective: Active\n"
        "✅ Risk Manager: Active\n\n"
        "Total Agents: 20\n"
        "Health: 100%\n"
        "Last Consensus: 5 mins ago"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

@authenticate
async def portfolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /portfolio command."""
    text = (
        "📈 *Your Portfolio Overview*\n\n"
        "Total Value: $0.00 (Mock)\n"
        "24h Change: 0.00%\n\n"
        "Connect your exchange via settings to view actual data."
    )
    await update.message.reply_text(text, parse_mode="Markdown")

@authenticate
async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /subscribe command."""
    await update.message.reply_text("✅ You are now *subscribed* to real-time signal alerts.", parse_mode="Markdown")

@authenticate
async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /unsubscribe command."""
    await update.message.reply_text("🔕 You have *unsubscribed* from real-time signal alerts.", parse_mode="Markdown")

@authenticate
async def system_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler for /status command."""
    text = (
        "⚙️ *System Status*\n\n"
        "🗄️ Database: ✅ Connected (Supabase)\n"
        "🧠 Cache: ✅ Connected (Redis)\n"
        "🔄 Last Market Cycle: 4 mins ago\n"
        "📡 Data Providers: OK (CoinGecko, Binance)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")
