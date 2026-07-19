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
        f"⏳ Анализирую <b>{coin.upper()}</b> (1h + 4h + 1d)...", parse_mode=ParseMode.HTML
    )
    try:
        result = await asyncio.to_thread(generate_signal, coin)
        ind    = result["indicators"]
        plan   = result.get("trading_plan")

        # ── Цена — Binance FUTURES (Mark Price) ──────────────────────────────
        futures_ticker = None
        funding_data   = None
        try:
            from market_data.binance_futures import get_futures_ticker, get_funding_rate
            futures_ticker = await asyncio.to_thread(get_futures_ticker, coin)
            funding_data   = await asyncio.to_thread(get_funding_rate, coin)
            mark_p  = futures_ticker["mark_price"]
            last_p  = futures_ticker["last_price"]
            ch      = futures_ticker["price_change_pct"]
            fr_pct  = futures_ticker["last_funding_rate_pct"]
            cur_price = mark_p if mark_p > 0 else last_p
            fr_icon = "🔴" if fr_pct > 0.05 else ("🟢" if fr_pct < -0.02 else "⚪")
            price_line = (
                f"📊 <b>Futures Mark:</b> <code>${mark_p:,.4f}</code>  "
                f"{'📈' if ch>=0 else '📉'}{ch:+.2f}%\n"
                f"   Last Price: <code>${last_p:,.4f}</code>  "
                f"{fr_icon} Фандинг: <code>{fr_pct:+.4f}%</code>"
            )
        except Exception:
            try:
                pd_ = await asyncio.to_thread(get_price, coin)
                cur_price = pd_["price_usd"]
                ch = pd_.get("change_24h", 0.0)
                price_line = f"💵 <code>${cur_price:,.4f}</code>  {'📈' if ch>=0 else '📉'}{ch:+.1f}%"
            except Exception:
                cur_price = ind.get("price", 0)
                price_line = f"💵 <code>${cur_price:,.4f}</code>"

        # ── Форматирование цены ───────────────────────────────────────────────
        def fp(v):
            if v is None: return "—"
            return f"${v:,.2f}" if v >= 1 else f"${v:.5f}"

        # ── ACTION — главная строка ───────────────────────────────────────────
        action      = result.get("action", "НЕТ СИГНАЛА")
        action_icon = result.get("action_icon", "⚪")
        action_desc = result.get("action_desc", "")
        setup_score = result.get("setup_score", 0)
        timing      = result.get("entry_timing", "neutral")
        trend       = result.get("trend", "unknown")
        status      = result.get("status", "NO_EDGE")

        filled = round(setup_score / 10)
        bar    = "█" * filled + "░" * (10 - filled)

        trend_icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(trend, "❓")
        timing_icons = {
            "now":        "🟢 СЕЙЧАС",
            "wait":       "🕐 ЖДАТЬ",
            "overbought": "⛔ ПЕРЕКУПЛЕН",
            "neutral":    "🔘 НЕЙТРАЛЬНО",
        }
        timing_str = timing_icons.get(timing, "🔘")

        # ── Торговый план ─────────────────────────────────────────────────────
        plan_block = ""
        lev_block  = ""
        if plan and plan.setup_valid:
            entry_mid = (plan.entry_low + plan.entry_high) / 2
            # Атрибуты могут называться target_1 или target1
            t1 = getattr(plan, "target_1", None) or getattr(plan, "target1", None)
            t2 = getattr(plan, "target_2", None) or getattr(plan, "target2", None)

            rr_str = f"1:{plan.risk_reward:.1f}" if plan.risk_reward else "—"

            plan_block = (
                f"\n💰 <b>ТОРГОВЫЙ ПЛАН:</b>\n"
                f"  📥 Вход:    <code>{fp(plan.entry_low)} — {fp(plan.entry_high)}</code>\n"
                f"  🛑 Стоп:    <code>{fp(plan.stop_loss)}</code>  "
                f"(<code>-{plan.risk_pct:.1f}%</code>)\n"
                f"  🎯 Цель 1:  <code>{fp(t1)}</code>  (R/R {rr_str})\n"
            )
            if t2:
                plan_block += f"  🎯 Цель 2:  <code>{fp(t2)}</code>\n"
            if getattr(plan, "chase_limit", None):
                plan_block += f"  ⛔ Не входить выше: <code>{fp(plan.chase_limit)}</code>\n"

            # Плечо
            try:
                from analysis.leverage import recommend_leverage
                lev_data = recommend_leverage(setup_score, entry_mid, plan.stop_loss, t1 or entry_mid*1.05, t2)
                lev = lev_data["leverage"]
                lev_icon = "🟢" if lev <= 2 else ("🟡" if lev <= 3 else "🔴")
                lev_block = (
                    f"\n{lev_icon} <b>ПЛЕЧО: {lev}x</b>  |  {lev_data['confidence']}\n"
                    f"  Риск со стопом:  <code>-{lev_data['risk_lev_pct']:.1f}%</code>\n"
                    f"  Доход T1:        <code>+{lev_data['reward1_lev']:.1f}%</code>\n"
                    f"  Маржа (2% риск): ~<code>{lev_data['margin_pct']:.0f}%</code> депо\n"
                )
            except Exception:
                lev_block = ""
        else:
            plan_block = (
                f"\n💰 <b>УРОВНИ:</b>\n"
                f"  EMA20: <code>{fp(ind.get('ema20'))}</code>  "
                f"  EMA50: <code>{fp(ind.get('ema50'))}</code>\n"
                f"  BB нижняя: <code>{fp(ind.get('bb_low'))}</code>  "
                f"  BB верхняя: <code>{fp(ind.get('bb_high'))}</code>\n"
            )

        # ── Индикаторы коротко ────────────────────────────────────────────────
        rsi_1d  = ind.get("rsi")
        rsi_4h  = ind.get("rsi_4h")
        rsi_1h  = ind.get("rsi_1h")
        macd_d  = ind.get("macd_diff")
        adx_v   = ind.get("adx")

        def rsi_color(v):
            if v is None: return "—"
            if v <= 30: return f"🔵{v:.0f}"  # перепродан
            if v >= 70: return f"🔴{v:.0f}"  # перекуплен
            return f"{v:.0f}"

        ind_block = (
            f"\n📊 <b>Индикаторы:</b>\n"
            f"  RSI   1d/4h/1h: <code>{rsi_color(rsi_1d)} / {rsi_color(rsi_4h)} / {rsi_color(rsi_1h)}</code>\n"
            f"  MACD  1d: <code>{'▲' if (macd_d or 0)>0 else '▼'} {macd_d:+.5f}</code>\n"
            f"  ADX:  <code>{adx_v:.1f}</code>  {trend_icon} тренд: {trend}\n"
        ) if macd_d is not None else ""

        # ── Ключевой вывод ────────────────────────────────────────────────────
        if status in ("STRONG_SETUP", "BUY_ZONE"):
            when_block = (
                f"\n⏰ <b>КОГДА ВХОДИТЬ:</b>\n"
                f"  1h тайминг: {timing_str}\n"
                f"  <i>{result.get('timing_reason', '')}</i>\n"
            )
        elif status in ("EVENT_RISK",):
            when_block = f"\n⚠️ <b>Дождись окончания события перед входом</b>\n"
        else:
            when_block = f"\n⏳ <b>Пока нет сигнала</b> — наблюдаем. Score нужно {55-setup_score} баллов до BUY_ZONE.\n"

        # ── Сборка ────────────────────────────────────────────────────────────
        text = (
            f"{action_icon} <b>{coin.upper()}: {action}</b>\n"
            f"<code>[{bar}]</code> {setup_score}/100  |  {price_line}\n"
            f"<i>{action_desc}</i>\n"
            f"{plan_block}"
            f"{lev_block}"
            f"{when_block}"
            f"{ind_block}"
            f"\n<i>⚠️ Аналитика, не торговый совет. Ставь стоп-лосс.</i>"
        )

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


# ── /backtest ─────────────────────────────────────────────────────────────────
@auth_required
async def cmd_backtest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /backtest [монета] [дни] [таймфрейм]
    Примеры:
      /backtest btc
      /backtest eth 365
      /backtest sol 90 4h
    """
    args = context.args or []
    coin = args[0].lower() if len(args) >= 1 else "btc"
    try:
        lookback = int(args[1]) if len(args) >= 2 else 180
        lookback = max(30, min(lookback, 365))
    except ValueError:
        lookback = 180
    interval = args[2].lower() if len(args) >= 3 else "1d"
    if interval not in ("1d", "4h"):
        interval = "1d"

    msg = await update.message.reply_text(
        f"⏳ Бэктест <b>{coin.upper()}</b> ({interval}, {lookback} дн.)...\n"
        f"<i>Анализирую историю без look-ahead bias — займёт 10-30 сек</i>",
        parse_mode=ParseMode.HTML,
    )
    try:
        from analysis.backtest import run_backtest, format_backtest
        result = await asyncio.to_thread(run_backtest, coin, interval, lookback)
        if result is None:
            await msg.edit_text(
                f"❌ Недостаточно данных для бэктеста <b>{coin.upper()}</b>.\n"
                f"Попробуй уменьшить период или использовать 1d интервал.",
                parse_mode=ParseMode.HTML,
            )
            return
        text = format_backtest(result)
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("backtest error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /optimize ─────────────────────────────────────────────────────────────────
@auth_required
async def cmd_optimize(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /optimize [монета|all]
    Запускает адаптивную оптимизацию: grid search параметров + анализ надёжности индикаторов.

    /optimize btc      — только BTC
    /optimize all      — все монеты (5-10 мин)
    """
    from analysis.optimizer import full_optimize
    from analysis.scanner import COINS_TO_WATCH

    args = context.args or []
    target = args[0].lower() if args else "btc"

    if target == "all":
        coins = COINS_TO_WATCH
        msg = await update.message.reply_text(
            f"🧠 Запускаю полную оптимизацию для {len(coins)} монет...\n"
            f"<i>Это займёт 5-10 минут. Результаты сразу применятся к сигналам.</i>",
            parse_mode=ParseMode.HTML,
        )
        results = []
        for coin in coins:
            try:
                res = await asyncio.to_thread(full_optimize, coin, "1d", 180)
                results.append(f"✅ {coin.upper()}: {res['summary'].split(chr(10))[1]}")
            except Exception as e:
                results.append(f"❌ {coin.upper()}: {e}")
            # Обновляем прогресс
            done = len(results)
            await msg.edit_text(
                f"🧠 Оптимизация: {done}/{len(coins)}...\n" +
                "\n".join(results[-3:]),
                parse_mode=ParseMode.HTML,
            )

        await msg.edit_text(
            f"🎓 <b>Оптимизация завершена ({len(coins)} монет)</b>\n\n" +
            "\n".join(results) +
            "\n\n<i>Новые веса применяются при следующем /signal или /scan</i>",
            parse_mode=ParseMode.HTML,
        )
    else:
        coin = target
        msg = await update.message.reply_text(
            f"🧠 Оптимизирую <b>{coin.upper()}</b>...\n"
            f"<i>Grid search 36 комбинаций + анализ надёжности индикаторов</i>",
            parse_mode=ParseMode.HTML,
        )
        try:
            res = await asyncio.to_thread(full_optimize, coin, "1d", 180)
            await msg.edit_text(
                f"🎓 <b>Оптимизация {coin.upper()} завершена</b>\n\n"
                f"{res['summary']}\n\n"
                f"<i>Новые веса применяются при следующем /signal</i>\n"
                f"Параметры: /params {coin}",
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error("optimize error: %s", e)
            await msg.edit_text(f"❌ Ошибка оптимизации: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /params ───────────────────────────────────────────────────────────────────
@auth_required
async def cmd_params(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/params BTC — показывает оптимальные параметры и надёжность индикаторов."""
    from analysis.optimizer import format_params_report
    coin = (context.args[0] if context.args else "btc").lower()
    text = format_params_report(coin)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


# ── /results ─────────────────────────────────────────────────────────────────
@auth_required
async def cmd_results(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /results — статистика результатов всех сигналов бота.
    Показывает последние 20 сделок: вход, выход, P&L с плечом, win rate.
    """
    from db.database import signal_trades_get_recent

    trades = signal_trades_get_recent(limit=20)

    if not trades:
        await update.message.reply_text(
            "📊 <b>Статистика сигналов</b>\n\n"
            "Нет закрытых сделок. Сигналы отслеживаются автоматически — "
            "результат придёт когда цена достигнет цели или стопа.",
            parse_mode=ParseMode.HTML,
        )
        return

    wins     = [t for t in trades if t["status"] == "win"]
    losses   = [t for t in trades if t["status"] == "loss"]
    timeouts = [t for t in trades if t["status"] == "timeout"]
    win_rate = len(wins) / (len(wins) + len(losses)) * 100 if (wins or losses) else 0.0

    total_lev_pnl = sum(float(t["pnl_lev_pct"] or 0) for t in trades if t["status"] in ("win","loss"))

    header = (
        f"📊 <b>Статистика сигналов</b> (последние {len(trades)})\n\n"
        f"✅ Побед:    {len(wins)}\n"
        f"❌ Потерь:   {len(losses)}\n"
        f"⏱ Таймаут:  {len(timeouts)}\n"
        f"🎯 Win Rate: <b>{win_rate:.0f}%</b>\n"
        f"💰 Суммарный P&L (с плечом): <code>{total_lev_pnl:+.1f}%</code>\n\n"
        f"<b>Последние сделки:</b>\n"
    )

    rows = []
    for t in trades[:10]:
        icon   = "✅" if t["status"]=="win" else ("❌" if t["status"]=="loss" else "⏱")
        ep     = float(t.get("entry_price") or 0)
        xp     = float(t.get("exit_price") or 0)
        pnl    = float(t.get("pnl_lev_pct") or 0)
        lev    = int(t.get("leverage") or 1)
        sign   = "+" if pnl >= 0 else ""
        rows.append(
            f"{icon} <b>{t['coin'].upper()}</b> ×{lev}  "
            f"<code>${ep:,.2f}→${xp:,.2f}</code>  "
            f"<code>{sign}{pnl:.1f}%</code>"
        )

    text = header + "\n".join(rows)
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
