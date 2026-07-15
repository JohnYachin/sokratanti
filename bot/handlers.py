import os
import logging
from functools import wraps

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from data.coingecko import get_price, get_ohlcv
from data.feargreed import get_fear_greed
from data.cryptopanic import get_news
from data.perplexity import search_crypto_news, get_market_analysis
from analysis.technical import calculate_indicators
from analysis.signals import generate_signal
from analysis.sentiment import analyze_sentiment
from db.database import save_signal, get_history

logger = logging.getLogger(__name__)

# ── Монеты по умолчанию ─────────────────────────────────────────────────────
TRACKED_COINS = ["btc", "eth", "sol", "bnb", "doge"]
COIN_NAMES = {
    "btc": "Bitcoin",
    "eth": "Ethereum",
    "sol": "Solana",
    "bnb": "BNB",
    "doge": "Dogecoin",
}

# ── Авторизация ──────────────────────────────────────────────────────────────
def _get_allowed_id() -> int:
    uid = os.getenv("TELEGRAM_USER_ID", "0")
    try:
        return int(uid)
    except ValueError:
        return 0


def auth_required(func):
    """Разрешает доступ только владельцу бота."""
    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        allowed = _get_allowed_id()
        if allowed and update.effective_user.id != allowed:
            await update.message.reply_text("⛔ Доступ запрещён.")
            return
        return await func(update, context)
    return wrapper


# ── /start, /help ────────────────────────────────────────────────────────────
@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>Sokratanti</b> — твой крипто-ассистент\n\n"
        "📋 <b>Команды:</b>\n"
        "/price <code>BTC</code> — текущая цена\n"
        "/signal <code>BTC</code> — торговый сигнал (RSI + MACD + BB + F&G)\n"
        "/sentiment <code>BTC</code> — настроение рынка (AI)\n"
        "/analyze <code>BTC</code> — анализ от Perplexity (реальный интернет)\n"
        "/news <code>BTC</code> — свежие новости с CryptoPanic\n"
        "/feargreed — индекс страха и жадности рынка\n"
        "/history — история последних сигналов\n"
        "/coins — список отслеживаемых монет\n\n"
        "💡 <b>Монеты:</b> BTC, ETH, SOL, BNB, DOGE\n\n"
        "⏰ Авто-отчёты каждые 4 ч. | 🚨 Алерты при BUY/SELL"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /coins ───────────────────────────────────────────────────────────────────
@auth_required
async def cmd_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = "\n".join([f"• <b>{k.upper()}</b> — {v}" for k, v in COIN_NAMES.items()])
    await update.message.reply_text(
        f"📊 <b>Отслеживаемые монеты:</b>\n\n{lines}",
        parse_mode=ParseMode.HTML,
    )


# ── /price ───────────────────────────────────────────────────────────────────
@auth_required
async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(f"⏳ Получаю данные по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML)

    try:
        data = get_price(coin)
        ch = data["change_24h"]
        ch7 = data["change_7d"]

        text = (
            f"💰 <b>{data['name']} ({data['symbol']})</b>\n\n"
            f"Цена:       <code>${data['price_usd']:>15,.2f}</code>\n"
            f"{'📈' if ch >= 0 else '📉'} 24ч:       <code>{ch:>+14.2f}%</code>\n"
            f"{'📈' if ch7 >= 0 else '📉'} 7д:        <code>{ch7:>+14.2f}%</code>\n"
            f"⬆️ Макс 24ч: <code>${data['high_24h']:>14,.2f}</code>\n"
            f"⬇️ Мин 24ч:  <code>${data['low_24h']:>14,.2f}</code>\n"
            f"💎 Капитал:  <code>${data['market_cap']:>14,.0f}</code>\n"
            f"📦 Объём:    <code>${data['volume_24h']:>14,.0f}</code>"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("price error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /signal ──────────────────────────────────────────────────────────────────
@auth_required
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"⏳ Анализирую <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )

    try:
        result = generate_signal(coin)
        ind = result["indicators"]

        reasons_html = "\n".join(f"  • {r}" for r in result["reasons"])
        text = (
            f"{result['emoji']} <b>Сигнал {coin.upper()}: {result['signal']}</b>\n\n"
            f"📊 <b>Индикаторы:</b>\n"
            f"  RSI:      <code>{ind['rsi']:.1f}</code>\n"
            f"  MACD diff:<code>{ind['macd_diff']:.5f}</code>\n"
            f"  BB позиция:<code>{ind['bb_pband']:.2f}</code> (0=низ, 1=верх)\n\n"
            f"📝 <b>Анализ:</b>\n{reasons_html}\n\n"
            f"🎯 Итоговая оценка: <code>{result['score']:+d}</code>"
        )

        # Сохраняем в историю
        save_signal(
            coin=coin,
            signal=result["signal"],
            score=result["score"],
            rsi=ind["rsi"],
            macd_diff=ind["macd_diff"],
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("signal error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /sentiment ───────────────────────────────────────────────────────────────
@auth_required
async def cmd_sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"⏳ Анализирую настроения по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )

    try:
        result = analyze_sentiment(coin)
        score = result["score"]

        if score >= 0.3:
            emoji, mood = "🟢", "Бычье (BULLISH)"
        elif score <= -0.3:
            emoji, mood = "🔴", "Медвежье (BEARISH)"
        else:
            emoji, mood = "🟡", "Нейтральное"

        headlines_html = "\n".join(f"  • {h}" for h in result.get("headlines", [])[:5])
        key_factor = result.get("key_factor", "")
        key_factor_line = f"\n🔑 <b>Ключевой фактор:</b> {key_factor}" if key_factor and key_factor != "—" else ""

        text = (
            f"{emoji} <b>Настроение {coin.upper()}: {mood}</b>\n"
            f"Индекс: <code>{score:+.2f}</code> (от -1 до +1)\n\n"
            f"📝 {result['summary']}"
            f"{key_factor_line}\n\n"
            f"📰 <b>Свежие новости:</b>\n{headlines_html}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("sentiment error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /history ─────────────────────────────────────────────────────────────────
@auth_required
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = get_history(10)

    if not rows:
        await update.message.reply_text("📭 История сигналов пуста. Сначала выполни /signal.")
        return

    lines = []
    for coin, sig, score, created_at in rows:
        emoji = "🟢" if sig == "BUY" else "🔴" if sig == "SELL" else "🟡"
        lines.append(f"{emoji} <b>{coin}</b> — {sig} (<code>{score:+.1f}</code>)  {created_at[:16]}")

    text = "📜 <b>История сигналов (последние 10):</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /feargreed ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_feargreed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_fear_greed()
    value = data["value"]

    # Прогресс-бар
    filled = round(value / 10)
    bar = "█" * filled + "░" * (10 - filled)

    text = (
        f"{data['emoji']} <b>Fear &amp; Greed Index</b>\n\n"
        f"<code>[{bar}]</code> <b>{value}/100</b>\n"
        f"Состояние: <b>{data['label_ru']}</b>\n\n"
        f"📖 <i>0–20  Экстремальный страх → покупай\n"
        f"20–40 Страх\n"
        f"40–60 Нейтрально\n"
        f"60–80 Жадность\n"
        f"80–100 Экстремальная жадность → продавай</i>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /news ─────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    coin = args[0].lower() if args else "btc"

    COIN_MAP = {
        "bitcoin": "btc", "ethereum": "eth", "solana": "sol",
        "binancecoin": "bnb", "dogecoin": "doge",
    }
    coin = COIN_MAP.get(coin, coin)

    await update.message.reply_text(f"🔍 Ищу новости по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML)

    news = get_news(coin, limit=8)
    if not news:
        await update.message.reply_text("❌ Новости не найдены. Проверь CRYPTOPANIC_API_KEY в .env")
        return

    lines = [f"📰 <b>Новости {coin.upper()} (CryptoPanic)</b>\n"]
    for i, n in enumerate(news, 1):
        title = n["title"][:90] + ("…" if len(n["title"]) > 90 else "")
        pos = n["votes_positive"]
        neg = n["votes_negative"]
        panic = n["panic_score"]

        votes_str = f"+{pos}👍 -{neg}👎" if (pos + neg) > 0 else ""
        panic_str = f" 🔥{panic}" if panic > 0 else ""

        lines.append(
            f"{i}. <a href='{n['url']}'>{title}</a>\n"
            f"   {votes_str}{panic_str}"
        )

    text = "\n\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                    disable_web_page_preview=True)
