"""
bot/handlers.py — Обработчики команд Telegram v2.0

Ключевые изменения v2:
  - Все блокирующие вызовы (requests, pandas, ta) обёрнуты в asyncio.to_thread()
    → event loop не блокируется, бот отвечает на все команды параллельно
  - /news использует RSS (бесплатно, без CryptoPanic)
  - /signal показывает setup_score + торговый план
  - /scan использует новый Signal Engine v2
"""
import asyncio
import os
import logging
from functools import wraps, partial

from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from data.coingecko import get_price
from data.feargreed import get_fear_greed
from data.perplexity import search_crypto_news, get_market_analysis
from analysis.signals import generate_signal
from analysis.sentiment import analyze_sentiment
from db.database import save_signal, get_history

logger = logging.getLogger(__name__)

# ── Монеты по умолчанию ──────────────────────────────────────────────────────
TRACKED_COINS = ["btc", "eth", "sol", "bnb", "doge"]
COIN_NAMES = {
    "btc": "Bitcoin", "eth": "Ethereum", "sol": "Solana",
    "bnb": "BNB", "doge": "Dogecoin",
}


# ── Авторизация ──────────────────────────────────────────────────────────────
def _get_allowed_id() -> int:
    try:
        return int(os.getenv("TELEGRAM_USER_ID", "0"))
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


# ── /start, /help ─────────────────────────────────────────────────────────────
@auth_required
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 <b>Sokratanti</b> — твой крипто-ассистент v2.0\n\n"
        "📋 <b>Команды:</b>\n"
        "/price <code>BTC</code> — текущая цена\n"
        "/signal <code>BTC</code> — торговый сигнал + зона входа + SL + цели\n"
        "/scan — анализ всех монет (setup score + торговые планы)\n"
        "/sentiment <code>BTC</code> — настроение рынка (AI)\n"
        "/analyze <code>BTC</code> — анализ от Perplexity\n"
        "/news <code>BTC</code> — свежие новости (CoinDesk, CoinTelegraph и др.)\n"
        "/feargreed — индекс страха и жадности\n"
        "/history — история последних сигналов\n"
        "/coins — список отслеживаемых монет\n\n"
        "📦 <b>Портфель:</b>\n"
        "/portfolio — мои позиции + P&L + сигналы\n"
        "/add <code>BTC 0.1 63500</code> — добавить позицию\n"
        "/remove <code>BTC</code> — удалить позицию\n\n"
        "💡 <b>Монеты:</b> BTC, ETH, SOL, BNB, DOGE\n\n"
        "⏰ Авто-отчёты каждые 4 ч. | 🚨 Алерты при BUY_ZONE / STRONG_SETUP"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /coins ────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_coins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = "\n".join([f"• <b>{k.upper()}</b> — {v}" for k, v in COIN_NAMES.items()])
    await update.message.reply_text(
        f"📊 <b>Отслеживаемые монеты:</b>\n\n{lines}",
        parse_mode=ParseMode.HTML,
    )


# ── /price ────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"⏳ Получаю данные по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )
    try:
        # asyncio.to_thread: requests.get не блокирует event loop
        data = await asyncio.to_thread(get_price, coin)
        ch  = data["change_24h"]
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


# ── /signal ───────────────────────────────────────────────────────────────────
@auth_required
async def cmd_signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"⏳ Анализирую <b>{coin.upper()}</b> (1d + 4h)...", parse_mode=ParseMode.HTML
    )
    try:
        # Вся тяжёлая работа в thread — event loop свободен
        result = await asyncio.to_thread(generate_signal, coin)
        ind = result["indicators"]
        setup_score = result.get("setup_score", 0)
        label_ru = result.get("label_ru", "")
        trend = result.get("trend", "unknown")

        filled = round(setup_score / 10)
        bar = "█" * filled + "░" * (10 - filled)

        rsi_str   = f"{ind['rsi']:.1f}"       if ind.get("rsi")       is not None else "—"
        macd_str  = f"{ind['macd_diff']:+.5f}" if ind.get("macd_diff") is not None else "—"
        bb_str    = f"{ind['bb_pband']:.2f}"   if ind.get("bb_pband")  is not None else "—"
        ema20_str = f"${ind['ema20']:,.2f}"    if ind.get("ema20")     is not None else "—"
        ema50_str = f"${ind['ema50']:,.2f}"    if ind.get("ema50")     is not None else "—"
        atr_str   = f"{ind['atr_pct']:.1f}%"  if ind.get("atr_pct")   is not None else "—"
        adx_str   = f"{ind['adx']:.1f}"       if ind.get("adx")       is not None else "—"

        trend_map = {
            "bullish": "📈 восходящий", "bearish": "📉 нисходящий",
            "neutral": "➡️ боковой",   "unknown": "❓ не определён",
        }
        reasons_html = "\n".join(f"  • {r}" for r in result["reasons"])

        text = (
            f"{result['emoji']} <b>{coin.upper()}: {label_ru}</b>\n"
            f"<code>[{bar}]</code> <b>{setup_score}/100</b>\n\n"
            f"📊 <b>Индикаторы (1d):</b>\n"
            f"  RSI:      <code>{rsi_str}</code>\n"
            f"  MACD:     <code>{macd_str}</code>\n"
            f"  BB поз.:  <code>{bb_str}</code> (0=низ, 1=верх)\n"
            f"  EMA20:    <code>{ema20_str}</code>\n"
            f"  EMA50:    <code>{ema50_str}</code>\n"
            f"  ATR:      <code>{atr_str}</code>\n"
            f"  ADX:      <code>{adx_str}</code>\n"
            f"  Тренд:    {trend_map.get(trend, trend)}\n\n"
            f"📝 <b>Анализ:</b>\n{reasons_html}\n"
        )

        plan = result.get("trading_plan")
        if plan is not None:
            from analysis.risk import format_trading_plan
            text += "\n" + format_trading_plan(plan, coin)
        elif result.get("status") in ("NO_EDGE", "DATA_INSUFFICIENT"):
            text += "\n⏳ <i>Нет чёткого торгового преимущества — жди сигнала</i>"

        try:
            save_signal(
                coin=coin, signal=result["signal"], score=result["score"],
                rsi=ind.get("rsi") or 0.0, macd_diff=ind.get("macd_diff") or 0.0,
            )
        except Exception as db_err:
            logger.warning("save_signal error: %s", db_err)

        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("signal error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /scan ─────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 <b>Анализирую рынок...</b> (может занять 30-60 сек)",
        parse_mode=ParseMode.HTML,
    )
    try:
        # Параллельно запрашиваем Fear&Greed и прогоняем все монеты
        fg_task = asyncio.to_thread(get_fear_greed)
        fg = await fg_task

        # Запускаем анализ всех монет конкурентно
        tasks = [asyncio.to_thread(generate_signal, coin) for coin in TRACKED_COINS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        parts = [
            f"📊 <b>Анализ рынка</b>\n"
            f"{fg['emoji']} Fear &amp; Greed: <b>{fg['value']}/100</b> — {fg['label_ru']}"
        ]

        best_coin = None
        best_score = -1

        for coin, result in zip(TRACKED_COINS, results):
            if isinstance(result, Exception):
                parts.append(f"⚠️ <b>{coin.upper()}</b>: ошибка — {result}")
                continue

            setup_score = result.get("setup_score", 0)
            label_ru = result.get("label_ru", result["signal"])
            emoji = result["emoji"]
            trend = result.get("trend", "unknown")

            # Прогресс-бар 5 блоков
            filled5 = round(setup_score / 20)
            bar5 = "█" * filled5 + "░" * (5 - filled5)

            trend_icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(trend, "❓")

            # Цена
            try:
                price_data = await asyncio.to_thread(get_price, coin)
                price = price_data["price_usd"]
                ch = price_data["change_24h"]
                price_line = f"💵 <code>${price:,.4f}</code>  {'📈' if ch >= 0 else '📉'}{ch:+.1f}%\n"
            except Exception:
                price_line = ""

            block = (
                f"\n{emoji} <b>{coin.upper()} — {label_ru}</b>\n"
                f"<code>[{bar5}]</code> {setup_score}/100  {trend_icon}\n"
                f"{price_line}"
            )

            # Торговый план если есть
            plan = result.get("trading_plan")
            if plan and plan.setup_valid:
                def fmt(v):
                    if v is None: return "—"
                    return f"${v:,.2f}" if v >= 1 else f"${v:.5f}"
                rr = f"1:{plan.risk_reward:.1f}" if plan.risk_reward else "—"
                block += (
                    f"  📐 Вход: <code>{fmt(plan.entry_low)}–{fmt(plan.entry_high)}</code>\n"
                    f"  🚫 SL: <code>{fmt(plan.stop_loss)}</code>  "
                    f"🎯 T1 ({rr}): <code>{fmt(plan.target_1)}</code>\n"
                )
            elif result.get("reasons"):
                top_reason = result["reasons"][0]
                block += f"  <i>{top_reason}</i>\n"

            parts.append(block)

            if setup_score > best_score and result.get("status") in ("BUY_ZONE", "STRONG_SETUP"):
                best_score = setup_score
                best_coin = coin

        # Итог
        if best_coin:
            parts.append(
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🎯 <b>Лучший сетап: {best_coin.upper()}</b> ({best_score}/100)"
            )
        else:
            parts.append("━━━━━━━━━━━━━━━━━━\n🟡 <b>Нет чёткого сигнала — наблюдаем</b>")

        parts.append("<i>Не финансовый совет. Управляй риском.</i>")
        await msg.edit_text("\n".join(parts), parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("scan error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /sentiment ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"⏳ Анализирую настроения по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )
    try:
        result = await asyncio.to_thread(analyze_sentiment, coin)
        score = result["score"]

        if score >= 0.3:
            emoji, mood = "🟢", "Бычье (BULLISH)"
        elif score <= -0.3:
            emoji, mood = "🔴", "Медвежье (BEARISH)"
        else:
            emoji, mood = "🟡", "Нейтральное"

        headlines_html = "\n".join(f"  • {h[:100]}" for h in result.get("headlines", [])[:5])
        key_factor = result.get("key_factor", "")
        key_factor_line = f"\n🔑 <b>Ключевой фактор:</b> {key_factor}" if key_factor and key_factor != "—" else ""

        text = (
            f"{emoji} <b>Настроение {coin.upper()}: {mood}</b>\n"
            f"Индекс: <code>{score:+.2f}</code> (от -1 до +1)\n\n"
            f"📝 {result['summary']}"
            f"{key_factor_line}\n\n"
            f"📰 <b>Новости:</b>\n{headlines_html}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("sentiment error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /news ─────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_news(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    coin_map = {
        "bitcoin": "btc", "ethereum": "eth", "solana": "sol",
        "binancecoin": "bnb", "dogecoin": "doge",
    }
    coin = coin_map.get(coin, coin)

    msg = await update.message.reply_text(
        f"🔍 Ищу новости по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )
    try:
        from news.rss import get_news_for_coin
        items = await asyncio.to_thread(get_news_for_coin, coin, 8)

        if not items:
            await msg.edit_text(
                f"📭 Нет новостей по <b>{coin.upper()}</b> за последние 48 часов.",
                parse_mode=ParseMode.HTML,
            )
            return

        lines = [f"📰 <b>Новости {coin.upper()}</b>\n"]
        for i, item in enumerate(items, 1):
            title = item.title[:90] + ("…" if len(item.title) > 90 else "")
            age_h = int((
                __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
                - item.published_at
            ).total_seconds() / 3600)
            age_str = f"{age_h}ч назад" if age_h < 24 else f"{age_h // 24}д назад"

            crit = "⚠️ " if item.is_critical else ""
            sent = "🟢" if item.sentiment_score > 0.1 else "🔴" if item.sentiment_score < -0.1 else "⚪"

            lines.append(
                f"{i}. {crit}<a href='{item.url}'>{title}</a>\n"
                f"   {sent} {item.source} · {age_str}"
            )

        await msg.edit_text(
            "\n\n".join(lines), parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as e:
        logger.error("news error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /analyze (Perplexity) ─────────────────────────────────────────────────────
@auth_required
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    coin = (context.args[0] if context.args else "btc").lower()
    msg = await update.message.reply_text(
        f"🌐 Ищу в интернете по <b>{coin.upper()}</b> через Perplexity...",
        parse_mode=ParseMode.HTML,
    )
    try:
        analysis, news_results = await asyncio.gather(
            asyncio.to_thread(get_market_analysis, coin),
            asyncio.to_thread(search_crypto_news, coin, 4),
        )

        if not analysis:
            await msg.edit_text("❌ Perplexity недоступен. Проверь PERPLEXITY_API_KEY в .env")
            return

        news_lines = []
        for n in news_results:
            title = n["title"][:80] + ("…" if len(n["title"]) > 80 else "")
            date = f" <i>({n['date'][:10]})</i>" if n.get("date") else ""
            news_lines.append(f"  • <a href='{n['url']}'>{title}</a>{date}")

        news_block = "\n".join(news_lines) if news_lines else "  (нет результатов)"
        sources = analysis.get("sources", [])
        src_block = ""
        if sources:
            src_links = " | ".join(
                f"<a href='{s}'>🔗</a>" if isinstance(s, str) else ""
                for s in sources[:3]
            )
            src_block = f"\n\n📎 <i>Источники: {src_links}</i>"

        text = (
            f"🌐 <b>Perplexity анализ {coin.upper()}</b>\n\n"
            f"🧠 <b>Что происходит:</b>\n{analysis['analysis']}\n\n"
            f"📰 <b>Найдено в интернете:</b>\n{news_block}"
            f"{src_block}"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        logger.error("analyze error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /feargreed ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_feargreed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        data = await asyncio.to_thread(get_fear_greed)
        value = data["value"]
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
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /history ──────────────────────────────────────────────────────────────────
@auth_required
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await asyncio.to_thread(get_history, 10)
    if not rows:
        await update.message.reply_text("📭 История сигналов пуста. Сначала выполни /signal.")
        return
    lines = []
    for coin, sig, score, created_at in rows:
        emoji = "🟢" if sig == "BUY" else "🔴" if sig == "SELL" else "🟡"
        lines.append(f"{emoji} <b>{coin}</b> — {sig} (<code>{score:+.1f}</code>)  {created_at[:16]}")
    text = "📜 <b>История сигналов (последние 10):</b>\n\n" + "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
