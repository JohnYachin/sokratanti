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

from data.feargreed import get_fear_greed
from analysis.signals import generate_signal
from analysis.sentiment import analyze_sentiment
from db.database import save_signal, get_history

logger = logging.getLogger(__name__)

# ── Монеты по умолчанию ──────────────────────────────────────────────────────
# Монеты с Binance USDT-M Futures — синхронизировано с scheduler.py
TRACKED_COINS = [
    "btc", "eth", "bnb", "sol", "xrp",
    "doge", "ada", "avax", "link", "dot",
    "ltc", "atom", "near", "uni", "trx",
    "bch", "aave", "apt", "arb", "op",
]
COIN_NAMES = {
    "btc":  "Bitcoin",      "eth":  "Ethereum",    "bnb":  "BNB",
    "sol":  "Solana",       "xrp":  "Ripple",       "doge": "Dogecoin",
    "ada":  "Cardano",      "avax": "Avalanche",    "link": "Chainlink",
    "dot":  "Polkadot",     "ltc":  "Litecoin",     "atom": "Cosmos",
    "near": "NEAR",        "uni":  "Uniswap",      "trx":  "TRON",
    "bch":  "Bitcoin Cash", "aave": "Aave",         "apt":  "Aptos",
    "arb":  "Arbitrum",     "op":   "Optimism",
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
        "/signal <code>BTC</code> — торговый сигнал + точка входа + SL + TP1 + TP2\n"
        "/futures <code>BTC</code> — Futures: Mark Price, Funding Rate, OI\n"
        "/signal <code>BTC</code> — сигнал + вход + SL + TP1 + TP2 + плечо\n"
        "/scan — все 20 монет (LONG/SHORT + торговые планы)\n"
        "/price <code>BTC</code> — цена (Futures + Spot)\n"
        "/sentiment <code>BTC</code> — настроение рынка (AI)\n"
        "/analyze <code>BTC</code> — анализ от Perplexity\n"
        "/news <code>BTC</code> — свежие новости\n"
        "/feargreed — индекс страха и жадности\n"
        "/history — история последних сигналов\n"
        "/results — статистика сделок (win/loss)\n"
        "/coins — все 20 монет\n\n"
        "📦 <b>Портфель:</b>\n"
        "/portfolio — мои позиции + P&L + сигналы\n"
        "/add <code>BTC 0.1 63500</code> — добавить позицию\n"
        "/remove <code>BTC</code> — удалить позицию\n\n"
        "⏰ <b>Авто-алерты каждые 30 мин</b> по 20 монетам\n"
        "🚨 LONG/SHORT сетап → Mark Price + Funding + плечо"
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
        # Futures Mark Price — основной курс
        futures_line = ""
        try:
            from market_data.binance_futures import get_futures_ticker
            ft  = await asyncio.to_thread(get_futures_ticker, coin)
            mk  = ft["mark_price"]
            lp  = ft["last_price"]
            fch = ft["price_change_pct"]
            fr  = ft["last_funding_rate_pct"]
            fr_icon = "🔴" if fr > 0.05 else ("🟢" if fr < -0.02 else "⚪")
            futures_line = (
                f"📈 <b>Binance Futures:</b>\n"
                f"  Mark Price: <code>${mk:,.4f}</code>\n"
                f"  Last Price: <code>${lp:,.4f}</code>\n"
                f"  24ч изм: <code>{fch:+.2f}%</code>\n"
                f"  {fr_icon} Funding Rate: <code>{fr:+.4f}%</code>\n\n"
            )
        except Exception:
            pass

        # Spot данные (market cap, volume)
        data = await asyncio.to_thread(get_price, coin)
        ch  = data["change_24h"]
        ch7 = data["change_7d"]
        ch_icon  = "📈" if ch  >= 0 else "📉"
        ch7_icon = "📈" if ch7 >= 0 else "📉"
        text = (
            f"💰 <b>{data['name']} ({data['symbol']})</b>\n\n"
            f"{futures_line}"
            f"📉 <b>Spot (CoinGecko):</b>\n"
            f"  Цена:      <code>${data['price_usd']:,.4f}</code>\n"
            f"  {ch_icon} 24ч:    <code>{ch:+.2f}%</code>\n"
            f"  {ch7_icon} 7д:     <code>{ch7:+.2f}%</code>\n"
            f"  ⬆️ Макс 24ч: <code>${data['high_24h']:,.4f}</code>\n"
            f"  ⬇️ Мин 24ч:  <code>${data['low_24h']:,.4f}</code>\n"
            f"  💸 Капитал:  <code>${data['market_cap']:,.0f}</code>\n"
            f"  📦 Объём:    <code>${data['volume_24h']:,.0f}</code>"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error("price error: %s", e)
        await msg.edit_text(f"❌ Ошибка: <code>{e}</code>", parse_mode=ParseMode.HTML)


# ── /futures ───────────────────────────────────────────────────────────────────────────
@auth_required
async def cmd_futures(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /futures BTC — полные данные по Binance Futures:
    Mark Price, Last Price, Funding Rate, Open Interest,
    следующее время фандинга, интерпретация.
    """
    coin = (context.args[0] if context.args else "btc").lower()
    msg  = await update.message.reply_text(
        f"⏳ Загружаю Futures данные по <b>{coin.upper()}</b>...", parse_mode=ParseMode.HTML
    )
    try:
        from market_data.binance_futures import (
            get_futures_ticker, get_funding_rate, get_open_interest, get_futures_context
        )
        from analysis.trading_rules import interpret_funding_rate

        ticker, funding, oi, ctx = await asyncio.gather(
            asyncio.to_thread(get_futures_ticker, coin),
            asyncio.to_thread(get_funding_rate, coin),
            asyncio.to_thread(get_open_interest, coin),
            asyncio.to_thread(get_futures_context, coin),
        )

        mk  = ticker["mark_price"]
        lp  = ticker["last_price"]
        ch  = ticker["price_change_pct"]
        h24 = ticker["high_24h"]
        l24 = ticker["low_24h"]
        vol = ticker["volume_24h"]

        fr_cur = funding["current_rate_pct"]
        fr_nxt = funding["predicted_rate_pct"]
        next_t = funding.get("next_funding_time", "")
        fr_sig, fr_adj, fr_desc = interpret_funding_rate(fr_cur)

        oi_val = oi.get("open_interest_usd", 0)
        oi_str = f"${oi_val/1e9:.2f}B" if oi_val >= 1e9 else f"${oi_val/1e6:.1f}M"

        fr_icon = "🔴" if fr_cur > 0.05 else ("🟢" if fr_cur < -0.02 else "⚪")
        ch_icon = "📈" if ch >= 0 else "📉"

        # Интерпретация Funding Rate
        fr_interp = (
            "🔴 Лонги перегреты — риск коррекции вниз" if fr_cur > 0.05
            else "🟢 Шорты перегреты — возможен шорт-сквиз" if fr_cur < -0.02
            else "⚪ Фандинг нейтральный — нет перекоса"
        )

        # Заметки из Futures контекста
        ctx_notes = ctx.get("notes", [])
        notes_str = ("\n" + "\n".join(f"  • {n}" for n in ctx_notes)) if ctx_notes else ""

        text = (
            f"📊 <b>Binance Futures: {coin.upper()}USDT</b>\n\n"

            f"🎯 <b>Mark Price:</b>  <code>${mk:,.4f}</code>  "
            f"({ch_icon}{ch:+.2f}%)\n"
            f"🔄 <b>Last Price:</b>  <code>${lp:,.4f}</code>\n"
            f"⬆️ <b>Макс 24ч:</b>  <code>${h24:,.4f}</code>\n"
            f"⬇️ <b>Мин 24ч:</b>   <code>${l24:,.4f}</code>\n"
            f"📦 <b>Объём 24ч:</b>  <code>${vol:,.0f}</code>\n\n"

            f"💰 <b>Funding Rate:</b>\n"
            f"  {fr_icon} Текущий:    <code>{fr_cur:+.4f}%</code>\n"
            f"  🔮 Прогноз:     <code>{fr_nxt:+.4f}%</code>\n"
            f"  ⏰ Следующий:  <code>{next_t or '—'}</code>\n"
            f"  {fr_interp}\n\n"

            f"📊 <b>Open Interest:</b> <code>{oi_str}</code>\n\n"

            f"🧠 <b>Интерпретация:</b>{notes_str if notes_str else ' нейтральная картина'}\n\n"

            f"<i>Данные: fapi.binance.com — те же что в Binance Futures</i>"
        )
        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("futures cmd error: %s", e)
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

        # ── Цена — Binance FUTURES (Last Price = то что видишь на бирже) ─────
        futures_ticker = None
        try:
            from market_data.binance_futures import get_futures_ticker, get_funding_rate
            futures_ticker = await asyncio.to_thread(get_futures_ticker, coin)
            mark_p  = futures_ticker["mark_price"]
            last_p  = futures_ticker["last_price"]
            ch      = futures_ticker["price_change_pct"]
            fr_pct  = futures_ticker["last_funding_rate_pct"]
            # Last Price — именно то что видишь на Binance Futures
            cur_price = last_p if last_p > 0 else mark_p
            fr_icon = "🔴" if fr_pct > 0.05 else ("🟢" if fr_pct < -0.02 else "⚪")
            diff_pct = abs(mark_p - last_p) / last_p * 100 if last_p > 0 else 0
            price_line = (
                f"💵 <b>Цена (Last):</b> <code>${last_p:,.4f}</code>  "
                f"{'📈' if ch>=0 else '📉'}{ch:+.2f}%\n"
                f"   Mark Price: <code>${mark_p:,.4f}</code>  "
                f"{fr_icon} Фандинг: <code>{fr_pct:+.4f}%</code>"
            )
        except Exception:
            cur_price = result.get("indicators", {}).get("price", 0)
            price_line = f"💵 <code>${cur_price:,.4f}</code>"

        # ── Форматирование цены ───────────────────────────────────────────────
        def fp(v):
            if v is None: return "—"
            return f"${v:,.2f}" if v >= 1 else f"${v:.5f}"

        # ── ACTION — главная строка ───────────────────────────────────────────
        action      = result.get("action", "НЕТ СИГНАЛА")
        action_icon = result.get("action_icon", "⚪")
        setup_score = result.get("setup_score", 0)
        short_score = result.get("short_score", 0)
        timing      = result.get("entry_timing", "neutral")
        trend       = result.get("trend", "unknown")
        status      = result.get("status", "NO_EDGE")
        direction   = result.get("direction", "LONG")
        plan        = result.get("trading_plan") if direction != "SHORT" else result.get("short_plan")
        USER_LEV    = 10  # Плечо пользователя

        def fp(v):
            if v is None: return "—"
            if v >= 1000: return f"${v:,.0f}"
            if v >= 1:    return f"${v:,.2f}"
            return f"${v:.5f}"

        # ── Таймфреймы 5m / 30m / 1h / 4h / 1d ─────────────────────────────────
        ind       = result.get("indicators", {})
        rsi_1d    = ind.get("rsi")
        rsi_4h    = ind.get("rsi_4h")
        rsi_1h    = ind.get("rsi_1h")
        rsi_30m   = ind.get("rsi_30m")
        rsi_5m    = ind.get("rsi_5m")

        def _tf_line(label, rsi_val):
            if rsi_val is None: return ""
            if rsi_val <= 30:   mood = "📉 сильно перепродан"
            elif rsi_val <= 42: mood = "🔽 слабость"
            elif rsi_val <= 58: mood = "➡️ нейтрально"
            elif rsi_val <= 70: mood = "🔼 сила"
            else:               mood = "📈 перекуплен"
            return f"  {label}: RSI <code>{rsi_val:.0f}</code> — {mood}\n"

        tf_vals = [rsi_5m, rsi_30m, rsi_1h, rsi_4h, rsi_1d]
        if any(v is not None for v in tf_vals):
            tf_block = (
                f"\n📊 <b>Таймфреймы:</b>\n"
                f"{_tf_line('5m  (скальп)', rsi_5m)}"
                f"{_tf_line('30m (тайминг)', rsi_30m)}"
                f"{_tf_line('1h  (вход)   ', rsi_1h)}"
                f"{_tf_line('4h  (тренд)  ', rsi_4h)}"
                f"{_tf_line('1d  (общий)  ', rsi_1d)}"
            )
        else:
            tf_block = ""

        # ── СИЛЬНЫЙ СИГНАЛ ────────────────────────────────────────────────────
        IS_STRONG  = status in ("STRONG_SETUP", "BUY_ZONE") and setup_score >= 55
        IS_SHORT   = status in ("STRONG_SHORT", "SHORT_ZONE") and short_score >= 55
        IS_ACTIVE  = IS_STRONG or IS_SHORT

        if IS_ACTIVE and plan and plan.setup_valid:
            dir_label  = "ШОРТ" if IS_SHORT else "ЛОНГ"
            dir_icon   = "🔴" if IS_SHORT else "🟢"
            urgency    = "СРОЧНО " if timing == "now" else ""

            entry_mid  = (plan.entry_low + plan.entry_high) / 2
            sl         = plan.stop_loss
            tp1        = plan.target_1
            tp2        = plan.target_2

            # % от цены (без плеча)
            if IS_SHORT:
                sl_pct   = (sl - entry_mid) / entry_mid * 100
                tp1_pct  = (entry_mid - tp1) / entry_mid * 100
                tp2_pct  = (entry_mid - tp2) / entry_mid * 100 if tp2 else 0
            else:
                sl_pct   = (entry_mid - sl) / entry_mid * 100
                tp1_pct  = (tp1 - entry_mid) / entry_mid * 100
                tp2_pct  = (tp2 - entry_mid) / entry_mid * 100 if tp2 else 0

            # x10 расчёт
            risk_10x    = sl_pct  * USER_LEV
            gain1_10x   = tp1_pct * USER_LEV
            gain2_10x   = tp2_pct * USER_LEV if tp2 else 0

            personal_wr = result.get("personal_wr") or 0.60
            ev  = personal_wr * gain1_10x - (1 - personal_wr) * risk_10x
            rr  = tp1_pct / sl_pct if sl_pct > 0 else 0
            ev_icon  = "✅" if ev > 0 else "⚠️"
            rr_str   = f"1:{rr:.1f}" if rr > 0 else "—"

            # Ликвидация
            try:
                from analysis.leverage import calc_liquidation_price
                liq = calc_liquidation_price(entry_mid, USER_LEV, direction)
                liq_str = fp(liq)
                liq_pct = abs((liq - entry_mid) / entry_mid * 100)
            except Exception:
                liq_str = "—"
                liq_pct = 100 / USER_LEV

            import datetime as _dt
            now_str = _dt.datetime.utcnow().strftime("%H:%M UTC")

            text = (
                f"{dir_icon} <b>{urgency}ВХОДИТЬ В {dir_label} — {coin.upper()}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 <b>Binance Futures:</b> <code>{fp(cur_price)}</code>  {now_str}\n"
                f"<i>⏱ Цена меняется каждую секунду</i>\n"
                f"{tf_block}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"📍 <b>Зона входа:</b> <code>{fp(plan.entry_low)} — {fp(plan.entry_high)}</code>\n"
                f"⚡ Плечо: <b>×{USER_LEV}</b>  |  Скор: {setup_score}/100\n\n"
                f"🎯 <b>TP1:</b> <code>{fp(tp1)}</code>  "
                f"(+{tp1_pct:.1f}% → <b>+{gain1_10x:.0f}%</b>)\n"
            )
            if tp2:
                text += (
                    f"🎯 <b>TP2:</b> <code>{fp(tp2)}</code>  "
                    f"(+{tp2_pct:.1f}% → <b>+{gain2_10x:.0f}%</b>)\n"
                )
            text += (
                f"🛑 <b>Стоп:</b> <code>{fp(sl)}</code>  "
                f"(-{sl_pct:.1f}% → <b>-{risk_10x:.0f}%</b>)\n"
                f"💀 <b>Ликвидация:</b> <code>{liq_str}</code>  (-{liq_pct:.1f}%)\n\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"{ev_icon} <b>Матожидание: {ev:+.0f}%</b>  |  R/R {rr_str}\n\n"
                f"<i>⚠️ Ставь стоп-лосс СРАЗУ при открытии.</i>"
            )

        elif IS_ACTIVE:
            import datetime as _dt
            now_str = _dt.datetime.utcnow().strftime("%H:%M UTC")
            text = (
                f"{action_icon} <b>{coin.upper()}: {action}</b>\n\n"
                f"💵 Сейчас: <code>{fp(cur_price)}</code>  {now_str}\n"
                f"{tf_block}\n"
                f"Скор: {setup_score}/100 — дождись чёткого уровня входа\n\n"
                f"<i>Используй /signal {coin.upper()} через 5 минут</i>"
            )

        else:
            import datetime as _dt
            now_str = _dt.datetime.utcnow().strftime("%H:%M UTC")
            if trend == "bearish":
                note = "📉 Нисходящий тренд — в лонг не входить"
            elif setup_score >= 40:
                note = f"⏳ Почти сигнал (скор {setup_score}/100) — жди подтверждения"
            else:
                note = f"😴 Нет чёткого входа (скор {setup_score}/100)"

            text = (
                f"⚪ <b>{coin.upper()}</b> — нет сигнала  <i>{now_str}</i>\n\n"
                f"💵 Сейчас: <code>{fp(cur_price)}</code>\n"
                f"{tf_block}\n"
                f"{note}\n\n"
                f"<i>Буду следить. Как появится вход — пришлю алерт.</i>"
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
        "🔍 <b>Анализирую 30 монет...</b>\n⏳ Подождите 30-60 секунд",
        parse_mode=ParseMode.HTML,
    )
    try:
        # Fear & Greed
        try:
            fg = await asyncio.to_thread(get_fear_greed)
            fg_line = f"{fg['emoji']} F&G: <b>{fg['value']}/100</b> — {fg['label_ru']}"
        except Exception:
            fg_line = "F&G: нет данных"

        # Запускаем все 30 монет параллельно
        tasks = [asyncio.to_thread(generate_signal, coin) for coin in TRACKED_COINS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        import datetime as _dt
        now_str = _dt.datetime.now().strftime("%H:%M %d.%m")

        longs, shorts, neutral, errors = [], [], [], []

        def fp(v):
            if v is None: return "—"
            if v >= 1000: return f"${v:,.0f}"
            if v >= 1:    return f"${v:,.2f}"
            return f"${v:.5f}"

        def pct(entry, target):
            if not entry or not target: return ""
            p = (target - entry) / entry * 100
            return f"({'+'if p>=0 else ''}{p:.1f}%)"

        for coin, result in zip(TRACKED_COINS, results):
            if isinstance(result, Exception):
                errors.append(f"⚠️ {coin.upper()}: ошибка")
                continue

            status    = result.get("status", "NO_EDGE")
            direction = result.get("direction", "NEUTRAL")
            score     = result.get("setup_score", 0)
            short_score = result.get("short_score", 0)
            emoji_s   = result.get("emoji", "📊")
            action    = result.get("action", "НЕТ СИГНАЛА")
            trend     = result.get("trend", "unknown")
            ind       = result.get("indicators", {})
            rsi       = ind.get("rsi")
            plan      = result.get("trading_plan")
            short_plan = result.get("short_plan")

            trend_icon = {"bullish": "📈", "bearish": "📉", "neutral": "➡️"}.get(trend, "❓")
            rsi_str    = f"RSI {rsi:.0f}" if rsi else ""

            if direction == "LONG" and plan and plan.setup_valid:
                em = (plan.entry_low + plan.entry_high) / 2
                longs.append(
                    f"\n🟢 <b>{coin.upper()}</b> {trend_icon} {score}/100 | {rsi_str}"
                    f"\n  🎯 {action}"
                    f"\n  📥 Вход: <code>{fp(plan.entry_low)} — {fp(plan.entry_high)}</code>"
                    f"\n  🛑 SL: <code>{fp(plan.stop_loss)}</code> {pct(em, plan.stop_loss)}"
                    f"\n  ✅ TP1: <code>{fp(plan.target_1)}</code> {pct(em, plan.target_1)}"
                    + (f"\n  🚀 TP2: <code>{fp(plan.target_2)}</code> {pct(em, plan.target_2)}" if plan.target_2 else "")
                    + f"\n  ⚖️ R/R: 1:{plan.risk_reward:.1f}" if plan.risk_reward else ""
                )

            elif direction == "SHORT" and short_plan and short_plan.setup_valid:
                em = (short_plan.entry_low + short_plan.entry_high) / 2
                shorts.append(
                    f"\n🔴 <b>{coin.upper()}</b> {trend_icon} {short_score}/100 | {rsi_str}"
                    f"\n  🎯 {action}"
                    f"\n  📤 Шорт: <code>{fp(short_plan.entry_low)} — {fp(short_plan.entry_high)}</code>"
                    f"\n  🛑 SL: <code>{fp(short_plan.stop_loss)}</code> {pct(em, short_plan.stop_loss)}"
                    f"\n  ✅ TP1: <code>{fp(short_plan.target_1)}</code> {pct(em, short_plan.target_1)}"
                    + (f"\n  🚀 TP2: <code>{fp(short_plan.target_2)}</code> {pct(em, short_plan.target_2)}" if short_plan.target_2 else "")
                    + f"\n  ⚖️ R/R: 1:{short_plan.risk_reward:.1f}" if short_plan.risk_reward else ""
                )

            else:
                # Монета без чёткого сигнала
                bar = "█" * round(score/20) + "░" * (5 - round(score/20))
                neutral.append(f"  {emoji_s} <b>{coin.upper()}</b> [{bar}] {score}/100 {trend_icon} {rsi_str}")

        # ── Шлём сообщение 1: шапка + LONG ───────────────────────────────────
        header = (
            f"📊 <b>АНАЛИЗ 30 МОНЕТ — {now_str}</b>\n"
            f"{fg_line}\n"
            f"🟢 LONG: <b>{len(longs)}</b>  🔴 SHORT: <b>{len(shorts)}</b>  "
            f"⚪ Нейтральных: <b>{len(neutral)}</b>"
        )

        await msg.edit_text(header, parse_mode=ParseMode.HTML)

        if longs:
            long_text = "🟢 <b>LONG — ТОЧКИ ВХОДА:</b>\n" + "\n".join(longs)
            # Режем если > 4096
            if len(long_text) > 4000:
                long_text = long_text[:3950] + "\n<i>...ещё монеты</i>"
            await update.message.reply_text(long_text, parse_mode=ParseMode.HTML)

        if shorts:
            short_text = "🔴 <b>SHORT — ТОЧКИ ВХОДА:</b>\n" + "\n".join(shorts)
            if len(short_text) > 4000:
                short_text = short_text[:3950] + "\n<i>...ещё монеты</i>"
            await update.message.reply_text(short_text, parse_mode=ParseMode.HTML)

        if neutral:
            neutral_text = "⚪ <b>Без чёткого сигнала:</b>\n" + "\n".join(neutral)
            if len(neutral_text) > 4000:
                neutral_text = neutral_text[:3950] + "\n<i>...ещё монеты</i>"
            await update.message.reply_text(neutral_text, parse_mode=ParseMode.HTML)

        # Итог
        summary = "<i>Данные: Binance 1h+4h+1d | Не финансовый совет.</i>"
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

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


# ── /account ──────────────────────────────────────────────────────────────────
@auth_required
async def cmd_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/account — Баланс и открытые позиции Binance Futures."""
    msg = await update.message.reply_text(
        "⏳ Загружаю данные аккаунта из Binance...", parse_mode=ParseMode.HTML
    )
    try:
        from market_data.binance_account import get_balance, get_open_positions, check_api_connection

        ok, conn_msg = await asyncio.to_thread(check_api_connection)
        if not ok:
            await msg.edit_text(
                f"{conn_msg}\n\n"
                "Добавь в <code>.env</code> на VPS:\n"
                "<code>BINANCE_API_KEY=твой_ключ</code>\n"
                "<code>BINANCE_API_SECRET=твой_секрет</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        balance, positions = await asyncio.gather(
            asyncio.to_thread(get_balance),
            asyncio.to_thread(get_open_positions),
        )

        upnl = balance["unrealized_pnl"]
        upnl_icon = "📈" if upnl >= 0 else "📉"
        upnl_sign = "+" if upnl >= 0 else ""

        text = (
            f"💼 <b>Binance Futures Account</b>\n\n"
            f"💰 Баланс:         <code>${balance['wallet']:,.2f}</code> USDT\n"
            f"✅ Доступно:        <code>${balance['available']:,.2f}</code> USDT\n"
            f"{upnl_icon} Незакрытый P&L:  <code>{upnl_sign}${upnl:,.2f}</code>\n\n"
        )

        if not positions:
            text += "📭 <i>Открытых позиций нет</i>"
        else:
            text += f"📊 <b>Открытые позиции ({len(positions)}):</b>\n"
            for p in positions:
                side_icon = "🟢" if p["side"] == "LONG" else "🔴"
                pnl       = p["unrealized_pnl"]
                pnl_sign  = "+" if pnl >= 0 else ""
                pnl_icon  = "📈" if pnl >= 0 else "📉"

                def _fp(v):
                    return f"${v:,.2f}" if v >= 1 else f"${v:.4f}"

                text += (
                    f"\n{side_icon} <b>{p['coin'].upper()}</b> ×{p['leverage']} ({p['side']})\n"
                    f"  Вход: <code>{_fp(p['entry_price'])}</code>  Mark: <code>{_fp(p['mark_price'])}</code>\n"
                    f"  P&L:  {pnl_icon} <code>{pnl_sign}${pnl:,.2f}</code> (<code>{pnl_sign}{p['pnl_pct']:.1f}%</code>)\n"
                    f"  Маржа: <code>${p['margin']:,.2f}</code>  💀 Liq: <code>{_fp(p['liq_price'])}</code>\n"
                )

        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("cmd_account error: %s", e)
        await msg.edit_text(
            f"❌ Ошибка: <code>{e}</code>\n\n"
            "Проверь BINANCE_API_KEY и BINANCE_API_SECRET в .env на VPS.",
            parse_mode=ParseMode.HTML,
        )


# ── /myhistory ────────────────────────────────────────────────────────────────
@auth_required
async def cmd_myhistory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myhistory [дней] — Анализ личной истории сделок Binance Futures."""
    try:
        days = int(context.args[0]) if context.args else 90
        days = max(7, min(180, days))
    except (ValueError, IndexError):
        days = 90

    msg = await update.message.reply_text(
        f"⏳ Анализирую твои сделки за {days} дней...",
        parse_mode=ParseMode.HTML,
    )
    try:
        from analysis.trade_analyzer import analyze_my_trades
        analysis = await asyncio.to_thread(analyze_my_trades, days)

        if "error" in analysis:
            await msg.edit_text(
                f"⚠️ {analysis['error']}\n\nПроверь BINANCE_API_KEY на VPS.",
                parse_mode=ParseMode.HTML,
            )
            return

        total     = analysis["total_trades"]
        total_pnl = analysis["total_pnl"]
        wr        = analysis["win_rate"]
        pnl_icon  = "📈" if total_pnl >= 0 else "📉"
        pnl_sign  = "+" if total_pnl >= 0 else ""

        text = (
            f"🧠 <b>Анализ твоих сделок — {days} дней</b>\n\n"
            f"📊 Всего сделок: <b>{total}</b>\n"
            f"🎯 Win rate:     <b>{wr*100:.0f}%</b>\n"
            f"{pnl_icon} Итоговый P&L: <code>{pnl_sign}${total_pnl:,.2f}</code>\n\n"
        )

        by_coin = analysis.get("by_coin", {})
        if by_coin:
            sorted_coins = sorted(by_coin.values(), key=lambda x: x.total_pnl, reverse=True)
            profitable   = [s for s in sorted_coins if s.total_pnl > 0][:5]
            losers       = [s for s in sorted_coins if s.total_pnl < 0][:3]

            if profitable:
                text += "🏆 <b>Прибыльные монеты:</b>\n"
                for s in profitable:
                    sign = "+" if s.total_pnl >= 0 else ""
                    text += (
                        f"  <b>{s.coin.upper()}</b>  Win {s.win_rate*100:.0f}%  "
                        f"({s.wins}W/{s.losses}L)  <code>{sign}${s.total_pnl:.0f}</code>\n"
                    )
                text += "\n"

            if losers:
                text += "❌ <b>Убыточные монеты:</b>\n"
                for s in losers:
                    text += (
                        f"  <b>{s.coin.upper()}</b>  Win {s.win_rate*100:.0f}%  "
                        f"({s.wins}W/{s.losses}L)  <code>${s.total_pnl:.0f}</code>\n"
                    )
                text += "\n"

        best_hours = analysis.get("best_hours", [])
        if best_hours:
            hours_str = "  ".join(f"{h:02d}:00" for h in best_hours[:4])
            text += f"⏰ <b>Лучшее время:</b> {hours_str} UTC\n\n"

        for ins in analysis.get("insights", [])[:5]:
            text += f"{ins}\n"

        recs = analysis.get("recommendations", [])
        if recs:
            text += "\n🎯 <b>Рекомендации:</b>\n"
            for r in recs[:4]:
                text += f"  {r}\n"

        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("cmd_myhistory error: %s", e)
        await msg.edit_text(
            f"❌ Ошибка: <code>{e}</code>\n\n"
            "Убедись что BINANCE_API_KEY и BINANCE_API_SECRET есть в .env на VPS.",
            parse_mode=ParseMode.HTML,
        )


# ── /strategy — Персональная стратегия ───────────────────────────────────────
@auth_required
async def cmd_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Анализ личной истории + текущие совпадения с рынком."""
    msg = await update.message.reply_text("🧠 Анализирую твою стратегию...", parse_mode=ParseMode.HTML)

    try:
        def _analyze():
            from analysis.trade_analyzer import analyze_my_trades, get_coin_personal_score
            from market_data.binance_futures import get_futures_ticker
            import datetime

            # Загружаем историю
            analysis = analyze_my_trades(days=90)
            if "error" in analysis:
                return None, analysis["error"]

            best_coins  = analysis.get("best_coins", [])
            worst_coins = analysis.get("worst_coins", [])
            best_hours  = analysis.get("best_hours", [])
            by_coin     = analysis.get("by_coin", {})

            # Текущий час UTC
            now_hour = datetime.datetime.utcnow().hour
            is_good_time = now_hour in best_hours

            # Проверяем лучшие монеты на текущем рынке
            opportunities = []
            for coin in best_coins[:5]:
                try:
                    ticker = get_futures_ticker(coin)
                    price  = ticker.get("price", 0)
                    chg    = ticker.get("change_pct_24h", 0)
                    stats  = by_coin.get(coin)
                    wr_str = f"{stats.win_rate*100:.0f}%" if stats else "?"
                    pnl_str = f"+${stats.total_pnl:.0f}" if stats and stats.total_pnl > 0 else ""
                    direction = "📈" if chg > 0 else "📉"
                    opportunities.append(
                        f"  {direction} <b>{coin.upper()}</b> "
                        f"${price:,.4g} ({chg:+.1f}%) "
                        f"| твоя WR: {wr_str} {pnl_str}"
                    )
                except Exception:
                    opportunities.append(f"  📊 <b>{coin.upper()}</b> | данные недоступны")

            return analysis, opportunities, is_good_time, now_hour

        result = await asyncio.to_thread(_analyze)
        if result[0] is None:
            await msg.edit_text(f"❌ {result[1]}", parse_mode=ParseMode.HTML)
            return

        analysis, opportunities, is_good_time, now_hour = result
        best_coins  = analysis.get("best_coins", [])
        worst_coins = analysis.get("worst_coins", [])
        best_hours  = analysis.get("best_hours", [])
        by_coin     = analysis.get("by_coin", {})
        win_rate    = analysis.get("win_rate", 0)
        total_pnl   = analysis.get("total_pnl", 0)
        total_trades = analysis.get("total_trades", 0)
        days        = analysis.get("days_analyzed", 90)

        # Форматируем время
        time_icon = "✅" if is_good_time else "⏳"
        best_hours_str = ", ".join(f"{h:02d}:00" for h in best_hours[:4]) if best_hours else "нет данных"

        text = (
            f"🧠 <b>Твоя персональная стратегия</b>\n"
            f"<i>(на основе {total_trades} сделок за {days} дней)</i>\n\n"

            f"📊 <b>Общая статистика:</b>\n"
            f"  Win Rate: <b>{win_rate*100:.0f}%</b>\n"
            f"  Итог P&L: <b>{'+'if total_pnl>0 else ''}{total_pnl:.0f}$</b>\n\n"
        )

        # Лучшие монеты
        if best_coins:
            text += "🏆 <b>Твои лучшие монеты:</b>\n"
            for c in best_coins[:3]:
                s = by_coin.get(c)
                if s:
                    text += f"  ✅ <b>{c.upper()}</b> — WR {s.win_rate*100:.0f}%, P&L ${s.total_pnl:+.0f}\n"
            text += "\n"

        # Худшие монеты
        if worst_coins:
            text += "🚫 <b>Избегай этих монет:</b>\n"
            for c in worst_coins[:3]:
                s = by_coin.get(c)
                if s:
                    text += f"  ❌ <b>{c.upper()}</b> — WR {s.win_rate*100:.0f}%, P&L ${s.total_pnl:+.0f}\n"
            text += "\n"

        # Время
        text += (
            f"⏰ <b>Лучшее время входа (UTC):</b>\n"
            f"  {best_hours_str}\n"
            f"  {time_icon} Сейчас {now_hour:02d}:00 UTC — "
            f"{'ХОРОШИЙ момент!' if is_good_time else 'не лучшее время'}\n\n"
        )

        # Текущие возможности
        if opportunities:
            text += "🎯 <b>Текущие возможности (твои монеты):</b>\n"
            for opp in opportunities[:5]:
                text += opp + "\n"
            text += "\n"

        # Инсайты
        insights = analysis.get("insights", [])
        if insights:
            text += "💡 <b>Ключевые выводы:</b>\n"
            for ins in insights[:3]:
                text += f"  {ins}\n"
            text += "\n"

        # Рекомендации
        recs = analysis.get("recommendations", [])
        if recs:
            text += "📋 <b>Что делать:</b>\n"
            for r in recs[:3]:
                text += f"  {r}\n"

        text += "\n<i>Используй /signal &lt;монета&gt; для детального анализа.\nСтратегия обновляется автоматически.</i>"

        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("cmd_strategy error: %s", e)
        await msg.edit_text(
            f"❌ Ошибка анализа: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )


# ── /report — Полный отчёт: история + рынок сейчас ───────────────────────────
@auth_required
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Разбор твоих сделок + текущий рынок по главным монетам. Простым языком."""
    msg = await update.message.reply_text(
        "📊 Собираю отчёт... (~30 сек)", parse_mode=ParseMode.HTML
    )

    # Монеты для анализа рынка
    MAIN_COINS = ["btc", "eth", "bnb", "sol", "doge", "ton"]

    def _simple_verdict(sig: dict) -> tuple[str, str]:
        """Переводит технический сигнал в понятный вывод."""
        status = sig.get("status", "")
        score  = sig.get("setup_score", 0)
        short  = sig.get("short_score", 0)
        timing = sig.get("entry_timing", "neutral")
        trend  = sig.get("trend", "neutral")

        if status in ("STRONG_SETUP",) and timing == "now":
            return "🟢", "ВХОДИТЬ В ЛОНГ сейчас"
        elif status == "STRONG_SETUP":
            return "🟡", "Ожидается РОСТ — готовься к входу"
        elif status == "BUY_ZONE" and timing == "now":
            return "🟡", "Можно открывать лонг"
        elif status == "BUY_ZONE":
            return "🟡", "Вероятен РОСТ — жди подтверждения"
        elif status in ("STRONG_SHORT",):
            return "🔴", "Ожидается ПАДЕНИЕ — не входи в лонг"
        elif status == "SHORT_ZONE":
            return "🔴", "Скорее всего ПАДЕНИЕ — жди"
        elif status == "EVENT_RISK":
            return "⛔", "Высокий риск — не входить"
        elif trend == "bearish" or score < 30:
            return "🔴", "Нисходящий тренд — ПАДЕНИЕ"
        elif score >= 45:
            return "🟡", "Нейтрально — нет чёткого входа"
        else:
            return "⚪", "Жди — нет сигнала"

    def _get_price_str(sig: dict) -> str:
        price = sig.get("indicators", {}).get("price")
        if price:
            if price >= 1000:
                return f"${price:,.0f}"
            elif price >= 1:
                return f"${price:,.2f}"
            else:
                return f"${price:.4f}"
        return ""

    try:
        def _run():
            from analysis.trade_analyzer import analyze_my_trades
            from analysis.signals import generate_signal
            import datetime

            # ── 1. История сделок ─────────────────────────────────────────────
            history = analyze_my_trades(days=90)
            trade_error = "error" in history

            # ── 2. Рынок сейчас (все 6 монет параллельно) ────────────────────
            market = {}
            for coin in MAIN_COINS:
                try:
                    market[coin] = generate_signal(coin)
                except Exception as e:
                    logger.error("Report signal %s: %s", coin, e)
                    market[coin] = None

            return history, market, trade_error

        history, market, trade_error = await asyncio.to_thread(_run)

        # ════════════════════════════════════════════════════════
        # БЛОК 1: РАЗБОР ТВОИХ СДЕЛОК
        # ════════════════════════════════════════════════════════
        text = "📋 <b>ПОЛНЫЙ ОТЧЁТ</b>\n\n"

        if not trade_error:
            wr      = history.get("win_rate", 0)
            pnl     = history.get("total_pnl", 0)
            trades  = history.get("total_trades", 0)
            best    = history.get("best_coins", [])
            worst   = history.get("worst_coins", [])
            hours   = history.get("best_hours", [])
            by_coin = history.get("by_coin", {})
            days    = history.get("days_analyzed", 90)

            text += (
                f"🔍 <b>Анализ твоих сделок за {days} дней</b>\n"
                f"Всего сделок: <b>{trades}</b> | Win Rate: <b>{wr*100:.0f}%</b>\n"
                f"Итог: <b>{'+'if pnl>0 else ''}{pnl:.0f}$</b>\n\n"
            )

            # Что сделал не так
            text += "❗ <b>Что пошло не так:</b>\n"
            mistakes = []

            # Убыточные монеты
            for c in worst[:3]:
                s = by_coin.get(c)
                if s and s.total_pnl < 0:
                    mistakes.append(
                        f"  • Торговал <b>{c.upper()}</b> — {s.total_trades} сделок, "
                        f"убыток {s.total_pnl:.0f}$ (WR {s.win_rate*100:.0f}%). "
                        f"Эту монету стоит исключить."
                    )

            # Плохое соотношение риск/прибыль
            bad_rr = [s for s in by_coin.values()
                      if s.avg_win_pct > 0 and s.avg_loss_pct < 0
                      and abs(s.avg_loss_pct) > s.avg_win_pct * 1.5 and s.total_trades >= 3]
            if bad_rr:
                coins_rr = ", ".join(s.coin.upper() for s in bad_rr[:3])
                mistakes.append(
                    f"  • По {coins_rr} — убытки больше прибылей. "
                    f"Нужно ставить стоп-лосс раньше."
                )

            # Ночная торговля
            by_hour = history.get("by_hour", {})
            night_hours = [h for h, s in by_hour.items()
                           if h in (22, 23, 0, 1, 2) and s["win_rate"] < 0.4
                           and (s["wins"] + s["losses"]) >= 2]
            if night_hours:
                mistakes.append(
                    f"  • Торговля ночью ({', '.join(f'{h:02d}:00' for h in sorted(night_hours)[:3])} UTC) "
                    f"— убыточна. Лучше не торговать ночью."
                )

            if pnl < 0 and wr > 0.5:
                mistakes.append(
                    "  • Win rate высокий, но итог отрицательный — "
                    "убыточные сделки слишком большие. Режь убытки раньше!"
                )

            if not mistakes:
                mistakes.append("  • По истории явных системных ошибок не выявлено.")

            text += "\n".join(mistakes) + "\n\n"

            # Что улучшить
            text += "✅ <b>Что нужно делать:</b>\n"
            if best:
                text += f"  • Торгуй только: <b>{', '.join(c.upper() for c in best[:3])}</b> — тут ты зарабатываешь\n"
            if worst:
                text += f"  • Не трогай: <b>{', '.join(c.upper() for c in worst[:3])}</b> — убыточно\n"
            if hours:
                text += f"  • Лучшее время входа: <b>{', '.join(f'{h:02d}:00' for h in hours[:3])} UTC</b>\n"
            text += "  • Ставь стоп-лосс сразу при входе\n"
            text += "  • Не усредняй убыточную позицию\n\n"
        else:
            text += "⚠️ История сделок недоступна (нет ключей Binance)\n\n"

        # ════════════════════════════════════════════════════════
        # БЛОК 2: РЫНОК СЕЙЧАС
        # ════════════════════════════════════════════════════════
        text += "━━━━━━━━━━━━━━━━━━━━\n"
        text += "📈 <b>Рынок сейчас</b>\n\n"

        coin_names = {
            "btc": "Bitcoin",  "eth": "Ethereum",
            "bnb": "BNB",      "sol": "Solana",
            "doge": "Dogecoin","ton": "TON",
        }

        for coin in MAIN_COINS:
            sig = market.get(coin)
            name = coin_names.get(coin, coin.upper())

            if not sig:
                text += f"⚪ <b>{name}</b> — нет данных\n\n"
                continue

            icon, verdict = _simple_verdict(sig)
            price_str = _get_price_str(sig)
            score = sig.get("setup_score", 0)

            # Главная причина (первые 2 факта)
            reasons = sig.get("reasons", [])
            key_fact = ""
            for r in reasons[:4]:
                if any(k in r for k in ("RSI", "MACD", "тренд", "Тренд", "фандинг", "Funding")):
                    clean = r.replace("[1d] ", "").replace("[4h] ", "").replace("[1h] ", "")
                    key_fact = clean[:60]
                    break

            text += (
                f"{icon} <b>{name}</b> {price_str}\n"
                f"   {verdict}\n"
            )
            if key_fact:
                text += f"   <i>{key_fact}</i>\n"
            text += "\n"

        text += (
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>⚠️ Это аналитика, не финансовый совет. "
            "Решение всегда за тобой.\n"
            "/signal BTC — подробный анализ монеты</i>"
        )

        await msg.edit_text(text, parse_mode=ParseMode.HTML)

    except Exception as e:
        logger.error("cmd_report error: %s", e, exc_info=True)
        await msg.edit_text(
            f"❌ Ошибка: <code>{e}</code>",
            parse_mode=ParseMode.HTML,
        )
