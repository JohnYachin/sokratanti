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
from analysis.scanner import scan_all_coins, get_top_opportunity
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


# ── /analyze (Perplexity) ─────────────────────────────────────────────────────
@auth_required
async def cmd_analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    coin = args[0].lower() if args else "btc"

    msg = await update.message.reply_text(
        f"🌐 Ищу в интернете по <b>{coin.upper()}</b> через Perplexity...",
        parse_mode=ParseMode.HTML,
    )

    try:
        # Параллельно: аналитика от sonar + свежие новости из поиска
        analysis = get_market_analysis(coin)
        news_results = search_crypto_news(coin, max_results=4)

        if not analysis:
            await msg.edit_text("❌ Perplexity недоступен. Проверь PERPLEXITY_API_KEY в .env")
            return

        # Форматируем новости из поиска
        news_lines = []
        for n in news_results:
            title = n["title"][:80] + ("…" if len(n["title"]) > 80 else "")
            date = f" <i>({n['date'][:10]})</i>" if n.get("date") else ""
            news_lines.append(f"  • <a href='{n['url']}'>{title}</a>{date}")

        news_block = "\n".join(news_lines) if news_lines else "  (нет результатов)"

        # Источники
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

        await msg.edit_text(text, parse_mode=ParseMode.HTML,
                            disable_web_page_preview=True)

    except Exception as e:
        logger.error("analyze error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /scan ────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🔍 <b>Анализирую рынок как трейдер...</b>\n⏳ ~40 секунд",
        parse_mode=ParseMode.HTML
    )

    try:
        import os, json
        from openai import OpenAI
        from data.coingecko import get_price
        from data.feargreed import get_fear_greed
        from analysis.trader_knowledge import TRADER_SYSTEM_PROMPT, get_coin_analysis_prompt

        results = scan_all_coins()
        fg = get_fear_greed()

        # Цены (уже кешированы после scan_all_coins)
        prices = {}
        for r in results:
            try:
                prices[r["coin"]] = get_price(r["coin"].lower())
            except Exception as e:
                logger.warning("Цена для %s: %s", r["coin"], e)

        # Собираем данные для GPT
        coins_summary = []
        for r in results:
            ind = r["indicators"]
            p   = prices.get(r["coin"], {})
            coins_summary.append({
                "coin":       r["coin"],
                "signal":     r["signal"],
                "probability": r["probability"],
                "score":      r["score"],
                "price_usd":  p.get("price_usd", 0),
                "change_24h": round(p.get("change_24h", 0), 2),
                "volume_24h": p.get("volume_24h", 0),
                "rsi":        round(ind["rsi"], 1),
                "macd_trend": "растёт" if ind["macd_diff"] > 0 else "падает",
                "bb_position": (
                    "у дна канала" if ind["bb_pband"] < 0.2
                    else "у верха канала" if ind["bb_pband"] > 0.8
                    else "в середине канала"
                ),
                "fear_greed": fg["value"],
            })

        fg_text = f"Fear & Greed Index: {fg['value']}/100 ({fg['label_ru']})"

        # ── GPT с трейдерскими знаниями ──────────────────────────────────────
        api_key = os.getenv("OPENAI_API_KEY", "")
        gpt_data: dict[str, dict] = {}

        if api_key:
            try:
                client_gpt = OpenAI(api_key=api_key)
                user_prompt = get_coin_analysis_prompt(coins_summary, fg_text)
                resp = client_gpt.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": TRADER_SYSTEM_PROMPT},
                        {"role": "user",   "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.2,
                    max_tokens=800,
                )
                gpt_data = json.loads(resp.choices[0].message.content)
            except Exception as e:
                logger.warning("GPT trader analysis error: %s", e)

        # ── Формируем вывод ───────────────────────────────────────────────────
        top = get_top_opportunity(results)
        lines = [
            f"📊 <b>Трейдерский анализ рынка</b>\n"
            f"{fg['emoji']} Настроение рынка: <b>{fg['label_ru']}</b> {fg['value']}/100\n"
        ]

        RISK_EMOJI = {"низкий": "🟢", "средний": "🟡", "высокий": "🔴"}

        for r in results:
            coin  = r["coin"]
            sig   = r["signal"]
            prob  = r["probability"]
            p     = prices.get(coin, {})
            price = p.get("price_usd", 0)
            ch    = p.get("change_24h", 0)
            ch_icon = "📈" if ch >= 0 else "📉"

            gpt_coin = gpt_data.get(coin, {})
            gpt_action = gpt_coin.get("action", sig)
            gpt_reason = gpt_coin.get("reason", " | ".join(r["reasons"][:2]))
            gpt_risk   = gpt_coin.get("risk", "средний")
            risk_e     = RISK_EMOJI.get(gpt_risk, "🟡")

            # Нормализуем action из GPT к одному из трёх
            if "ПОКУПАТЬ" in gpt_action.upper() or sig == "BUY":
                header = f"🟢 <b>{coin} — ПОКУПАТЬ</b>"
                bar = "🟩" * round(prob / 20) + "⬜" * (5 - round(prob / 20))
            elif "ПРОДАВАТЬ" in gpt_action.upper() or sig == "SELL":
                header = f"🔴 <b>{coin} — ПРОДАВАТЬ</b>"
                bar = "🟥" * round(prob / 20) + "⬜" * (5 - round(prob / 20))
            else:
                header = f"🟡 <b>{coin} — ЖДАТЬ</b>"
                bar = "🟨🟨🟨⬜⬜"

            price_str = f"<code>${price:,.2f}</code>  {ch_icon}{ch:+.1f}%" if price else ""

            lines.append(
                f"{header}  {price_str}\n"
                f"{bar} <b>{prob}%</b> вероятность  |  {risk_e} Риск: {gpt_risk}\n"
                f"<i>{gpt_reason}</i>"
            )

        # Итог
        if top:
            action_word = "КУПИТЬ" if top["signal"] == "BUY" else "ПРОДАТЬ"
            lines.append(
                f"\n🎯 <b>Лучшая возможность: {action_word} {top['coin']}</b>\n"
                f"Вероятность удачи: <b>{top['probability']}%</b>"
            )
        else:
            lines.append("\n🟡 <b>Явных возможностей нет сейчас. Жди алерта.</b>")

        lines.append("\n<i>⚠️ Не финансовый совет. Всегда управляй риском.</i>")

        await msg.edit_text("\n\n".join(lines), parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("scan error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)



    try:
        import os, json
        from openai import OpenAI

