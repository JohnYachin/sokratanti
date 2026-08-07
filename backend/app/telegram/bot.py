"""
CAIOS Telegram Bot — МИНИМАЛЬНЫЙ подход.
Только Bot класс, НИКАКОГО Application/Updater/APScheduler.
Один getUpdates за раз — 409 невозможен.
"""
import os, asyncio, logging, json
from datetime import datetime, timezone
from typing import Optional

import httpx
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("caios")
logging.getLogger("httpx").setLevel(logging.WARNING)

# ─── CONFIG ─────────────────────────────
TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "8937697751:AAFiTO-AnEowrT-XuSVlKZNs8d6BOVGoPXc")
ADMIN   = int(os.getenv("TELEGRAM_USER_ID", "634964003"))
SB_URL  = os.getenv("SUPABASE_URL", "https://zrvsuwdlhnnfvqxxohex.supabase.co")
SB_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY")
OAI_KEY = os.getenv("OPENAI_API_KEY", "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")
TG_API  = f"https://api.telegram.org/bot{TOKEN}"

TOP_COINS = [
    {"symbol":"BTC","name":"Bitcoin",   "id":"bitcoin"},
    {"symbol":"ETH","name":"Ethereum",  "id":"ethereum"},
    {"symbol":"BNB","name":"BNB",       "id":"binancecoin"},
    {"symbol":"SOL","name":"Solana",    "id":"solana"},
    {"symbol":"XRP","name":"XRP",       "id":"ripple"},
    {"symbol":"ADA","name":"Cardano",   "id":"cardano"},
    {"symbol":"AVAX","name":"Avalanche","id":"avalanche-2"},
    {"symbol":"DOT","name":"Polkadot",  "id":"polkadot"},
    {"symbol":"MATIC","name":"Polygon", "id":"matic-network"},
    {"symbol":"LINK","name":"Chainlink","id":"chainlink"},
]
SYM = {c["symbol"]: c for c in TOP_COINS}
subscribers: set[int] = {ADMIN}

try:
    sb = create_client(SB_URL, SB_KEY)
except Exception:
    sb = None


# ─── RAW TELEGRAM HTTP ───────────────────
async def tg(method: str, **params) -> dict:
    """Raw Telegram API call via httpx."""
    async with httpx.AsyncClient(timeout=45) as c:
        r = await c.post(f"{TG_API}/{method}", json={k: v for k, v in params.items() if v is not None})
        return r.json()


async def send(chat_id: int, text: str, keyboard=None, edit_message_id=None):
    params = dict(chat_id=chat_id, text=text, parse_mode="Markdown")
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    if edit_message_id:
        return await tg("editMessageText", message_id=edit_message_id, **params)
    return await tg("sendMessage", **params)


# ─── COINGECKO ────────────────────────────
async def get_prices(ids: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","ids":",".join(ids),"order":"market_cap_desc","sparkline":"false"}
        )
        return {d["symbol"].upper(): d for d in r.json()}


# ─── GPT-4o 5 AGENTS ─────────────────────
async def analyze(symbol: str, name: str, price: float, ch24: float, vol: float, mcap: float) -> dict:
    agents = [
        ("Trend Analyst",     "Focus on moving averages, momentum, price action, RSI, MACD."),
        ("Sentiment Analyst", "Focus on market sentiment, fear/greed, social trends."),
        ("On-Chain Analyst",  "Focus on whale activity, exchange flows, holder distribution."),
        ("Macro Analyst",     "Focus on DXY, S&P500, Fed rates, BTC dominance, risk appetite."),
        ("Risk Manager",      "Devil's advocate — downside risks, stop-loss levels, red flags."),
    ]
    h = {"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"}
    votes = []
    async with httpx.AsyncClient(timeout=60) as c:
        tasks = [c.post("https://api.openai.com/v1/chat/completions", headers=h, json={
            "model": "gpt-4o", "temperature": 0.3, "max_tokens": 150,
            "messages": [{"role": "user", "content":
                f"You are a crypto {role}. {prompt}\n\n"
                f"Analyze {name} ({symbol}): Price=${price:,.2f}, 24h={ch24:+.1f}%, Vol=${vol:,.0f}, MCap=${mcap:,.0f}\n"
                f'Respond ONLY with JSON: {{"signal":"BUY"|"SELL"|"HOLD","confidence":0.0-1.0,"reasoning":"2 sentences"}}'
            }]}) for role, prompt in agents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for res, (role, _) in zip(results, agents):
        try:
            raw = res.json()["choices"][0]["message"]["content"].strip()
            raw = raw.replace("```json","").replace("```","").strip()
            v = json.loads(raw); v["agent"] = role; votes.append(v)
        except Exception as e:
            votes.append({"agent": role, "signal": "HOLD", "confidence": 0.3, "reasoning": f"Parse error"})

    w = {"BUY": 1, "HOLD": 0, "SELL": -1}
    score = sum(w.get(v.get("signal","HOLD"),0) * v.get("confidence",0.5) for v in votes) / max(len(votes),1)
    conf  = sum(v.get("confidence",0) for v in votes) / max(len(votes),1)
    sig   = "BUY" if score >= 0.25 else ("SELL" if score <= -0.25 else "HOLD")
    emoji = "🟢" if sig == "BUY" else ("🔴" if sig == "SELL" else "🟡")
    return {"signal": sig, "emoji": emoji, "confidence": conf, "score": score, "votes": votes}


def fmt_analysis(symbol, name, price, ch24, a) -> str:
    conf_pct = int(a["confidence"]*100)
    bar = "█"*(conf_pct//10) + "░"*(10-conf_pct//10)
    votes_txt = ""
    for v in a.get("votes", []):
        ve = "🟢" if v["signal"]=="BUY" else ("🔴" if v["signal"]=="SELL" else "🟡")
        votes_txt += f"\n  {ve} *{v['agent']}*: {v['signal']} ({int(v.get('confidence',0)*100)}%)"
    best = a["votes"][0]["reasoning"] if a.get("votes") else "N/A"
    ts = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{a['emoji']} *{symbol} ({name}) — {a['signal']}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 *${price:,.2f}*  {'📈' if ch24>=0 else '📉'} {ch24:+.1f}%\n"
        f"🎯 Уверенность: *{conf_pct}%*  `{bar}`\n\n"
        f"🤖 *5 AI агентов:*{votes_txt}\n\n"
        f"💡 _{best}_\n"
        f"⏱ _{ts}_"
    )


# ─── KEYBOARDS ────────────────────────────
def coins_kb():
    return [
        [{"text": c["symbol"], "callback_data": f"s_{c['symbol']}"} for c in TOP_COINS[:5]],
        [{"text": c["symbol"], "callback_data": f"s_{c['symbol']}"} for c in TOP_COINS[5:]],
    ]

def signal_kb(symbol):
    return [[
        {"text": "🔄 Обновить", "callback_data": f"s_{symbol}"},
        {"text": "◀️ Топ 10",   "callback_data": "top"},
    ]]


# ─── HANDLERS ─────────────────────────────
async def on_start(chat_id: int, user_id: int):
    subscribers.add(user_id)
    await send(chat_id,
        "🚀 *CAIOS — Crypto AI Investment OS*\n\n"
        "5 AI агентов анализируют топ-10 монет.\n\n"
        "📌 *Команды:*\n"
        "`/signal BTC` — AI анализ\n"
        "`/top` — обзор цен\n"
        "`/subscribe` — часовые алерты\n"
        "`/status` — статус системы\n\n"
        "⚡ *Выберите монету:*",
        keyboard=coins_kb()
    )


async def on_top(chat_id: int):
    msg = await send(chat_id, "⏳ Загружаю данные...")
    msg_id = msg.get("result", {}).get("message_id")
    try:
        prices = await get_prices([c["id"] for c in TOP_COINS])
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        lines = [f"📊 *CAIOS — Топ 10*\n_{now}_\n"]
        for i, coin in enumerate(TOP_COINS, 1):
            d = prices.get(coin["symbol"], {})
            p = d.get("current_price", 0)
            ch = d.get("price_change_percentage_24h", 0)
            lines.append(f"{i}. *{coin['symbol']}* ${p:,.2f} {'📈' if ch>=0 else '📉'} {ch:+.1f}%")
        lines.append("\n_Нажмите монету для AI анализа:_")
        await send(chat_id, "\n".join(lines), keyboard=coins_kb(), edit_message_id=msg_id)
    except Exception as e:
        await send(chat_id, f"❌ Ошибка: {e}", edit_message_id=msg_id)


async def on_signal(chat_id: int, symbol: str):
    coin = SYM.get(symbol.upper())
    if not coin:
        await send(chat_id, f"❌ `{symbol}` не найдена.\nДоступные: {', '.join(SYM)}")
        return
    sym = coin["symbol"]

    # Try DB cache first (fast)
    db_sigs = get_db_signals()
    cached  = db_sigs.get(sym)

    if cached:
        # Show cached signal from last AI Council run
        sig   = cached["signal"]
        conf  = int(cached.get("confidence", 0) * 100)
        score = float(cached.get("score", 0))
        cach_price = float(cached.get("price_at_signal", 0))
        sem   = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}.get(sig,"❓")
        bar   = "█"*(conf//10) + "░"*(10-conf//10)
        # Get current price too
        try:
            prices = await get_prices([coin["id"]])
            d = prices.get(sym, {})
            curr_price = d.get("current_price", cach_price)
            ch = d.get("price_change_percentage_24h", 0)
        except Exception:
            curr_price, ch = cach_price, 0.0
        ts = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")
        text = (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{sem} *{sym} ({coin['name']}) — {sig}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *${curr_price:,.4f}*  {'📈' if ch>=0 else '📉'} {ch:+.1f}%\n"
            f"🎯 Уверенность: *{conf}%*  `{bar}`\n"
            f"📊 AI Score: `{score:+.3f}`\n\n"
            f"🤖 _Данные от AI Council (20 агентов)_\n"
            f"⏱ _{ts}_"
        )
        kb = [
            [{"text": "🔄 Обновить анализ", "callback_data": f"rf_{sym}"},
             {"text": "◀️ Топ 10", "callback_data": "top"}]
        ]
        await send(chat_id, text, keyboard=kb)
        return

    # No cache — run real-time AI (slower but fresh)
    msg = await send(chat_id, f"🔍 Анализирую *{sym}*...\n_20 AI агентов (~30 сек)_")
    msg_id = msg.get("result", {}).get("message_id")
    try:
        prices = await get_prices([coin["id"]])
        d = prices.get(sym, {})
        p, ch, vol, mc = (d.get("current_price",0), d.get("price_change_percentage_24h",0),
                          d.get("total_volume",0), d.get("market_cap",0))
        a = await analyze(sym, coin["name"], p, ch, vol, mc)
        text = fmt_analysis(sym, coin["name"], p, ch, a)
        await send(chat_id, text, keyboard=signal_kb(sym), edit_message_id=msg_id)
    except Exception as e:
        await send(chat_id, f"❌ Ошибка: {e}", edit_message_id=msg_id)



async def on_status(chat_id: int):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.coingecko.com/api/v3/ping")
            cg = "✅ Online" if r.status_code == 200 else "⚠️ Degraded"
    except:
        cg = "❌ Offline"
    db_status = "❌ Offline"
    if sb:
        try:
            sb.table("coins").select("id").limit(1).execute()
            db_status = "✅ Online"
        except:
            pass
    await send(chat_id,
        "📡 *CAIOS — Статус*\n\n"
        f"🗄️ Supabase: {db_status}\n"
        f"📊 CoinGecko: {cg}\n"
        f"🤖 GPT-4o: ✅ Ready\n"
        f"🔔 Подписчиков: {len(subscribers)}\n"
        f"💰 Монет: {len(TOP_COINS)}\n"
        f"⏱ Оповещения: каждый час\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"
    )


# ─── UPDATE DISPATCHER ────────────────────
async def dispatch(update: dict):
    """Route an update to the appropriate handler."""
    try:
        # Callback query (button press)
        if "callback_query" in update:
            cq = update["callback_query"]
            await tg("answerCallbackQuery", callback_query_id=cq["id"])
            data = cq.get("data", "")
            chat_id = cq["message"]["chat"]["id"]
            user_id = cq["from"]["id"]
            if data == "top":
                await on_top(chat_id)
            elif data == "subscribe":
                subscribers.add(user_id)
                await send(chat_id, "✅ Подписка активирована! Оповещения каждый час.")
            elif data.startswith("s_"):
                await on_signal(chat_id, data[2:])
            elif data.startswith("rf_"):
                # Force refresh — skip DB cache, run real-time AI
                sym = data[3:]
                coin = SYM.get(sym)
                if not coin:
                    await send(chat_id, f"❌ {sym} не найдена")
                    return
                loading = await send(chat_id, f"🔄 Обновляю анализ *{sym}*...\n_20 AI агентов_")
                load_id = loading.get("result", {}).get("message_id")
                try:
                    prices = await get_prices([coin["id"]])
                    d = prices.get(sym, {})
                    p, ch, vol, mc = (d.get("current_price",0), d.get("price_change_percentage_24h",0),
                                      d.get("total_volume",0), d.get("market_cap",0))
                    a = await analyze(sym, coin["name"], p, ch, vol, mc)
                    text = fmt_analysis(sym, coin["name"], p, ch, a)
                    await send(chat_id, text, keyboard=signal_kb(sym), edit_message_id=load_id)
                except Exception as e:
                    await send(chat_id, f"❌ Ошибка: {e}", edit_message_id=load_id)
            return

        # Message
        if "message" not in update:
            return
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            await on_start(chat_id, user_id)
        elif text.startswith("/top"):
            await on_top(chat_id)
        elif text.startswith("/signal"):
            parts = text.split()
            sym = parts[1].upper() if len(parts) > 1 else ""
            if sym:
                await on_signal(chat_id, sym)
            else:
                await send(chat_id, f"⚠️ Укажите монету: `/signal BTC`\nДоступные: {', '.join(SYM)}")
        elif text.startswith("/subscribe"):
            subscribers.add(user_id)
            await send(chat_id, "✅ *Подписка активирована!*\n\n🔔 Дайджест каждый час. Отписка: /unsubscribe")
        elif text.startswith("/unsubscribe"):
            subscribers.discard(user_id)
            await send(chat_id, "🔕 Отписан от уведомлений.")
        elif text.startswith("/alert"):
            await on_alert(chat_id, user_id, text)
        elif text.startswith("/alerts"):
            await on_alerts_list(chat_id, user_id)
        elif text.startswith("/delalert"):
            parts = text.split()
            aid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            await on_del_alert(chat_id, user_id, aid)
        elif text.startswith("/buy"):
            await on_trade(chat_id, user_id, text, 'buy')
        elif text.startswith("/sell"):
            await on_trade(chat_id, user_id, text, 'sell')
        elif text.startswith("/portfolio"):
            await on_portfolio(chat_id, user_id)
        elif text.startswith("/help"):
            coins_str = " | ".join(c["symbol"] for c in TOP_COINS)
            await send(chat_id,
                "📖 *CAIOS — Справка*\n\n"
                f"`/signal <МОНЕТА>` — AI анализ\n  Монеты: `{coins_str}`\n\n"
                "`/top` — цены топ-10\n"
                "`/subscribe` — подписка\n"
                "`/status` — статус\n\n"
                "🔔 *Алерты по цене:*\n"
                "`/alert BTC 100000` — уведомить когда BTC > $100k\n"
                "`/alert ETH 2000 below` — когда ETH < $2000\n"
                "`/alerts` — мои активные алерты\n"
                "`/delalert <id>` — удалить алерт\n\n"
                "💼 *Портфель:*\n"
                "`/buy BTC 0.1` — записать покупку по текущей цене\n"
                "`/buy BTC 0.1 95000` — по конкретной цене\n"
                "`/sell ETH 1.5` — записать продажу\n"
                "`/portfolio` — мой портфель и P&L"
            )
        elif text.startswith("/status"):
            await on_status(chat_id)

    except Exception as e:
        logger.error(f"Dispatch error: {e}")


# ─── DB SIGNALS ──────────────────────────
def get_db_signals() -> dict:
    """Fetch latest active signals from Supabase. Returns {symbol: row}."""
    if not sb:
        return {}
    try:
        rows = sb.table("signals").select(
            "signal,confidence,score,price_at_signal,coins(symbol)"
        ).eq("is_active", True).execute().data
        return {r["coins"]["symbol"]: r for r in rows if r.get("coins")}
    except Exception as e:
        logger.warning(f"DB signals fetch error: {e}")
        return {}


# ─── PRICE ALERTS ────────────────────────
async def on_alert(chat_id: int, user_id: int, text: str):
    """Handle /alert BTC 100000 [above|below]"""
    parts = text.split()
    if len(parts) < 3:
        await send(chat_id,
            "⚠️ *Формат:* `/alert <МОНЕТА> <ЦЕНА> [above|below]`\n\n"
            "Примеры:\n"
            "`/alert BTC 100000` — уведомить когда BTC > $100k\n"
            "`/alert ETH 2000 below` — когда ETH упадёт ниже $2000"
        )
        return
    sym = parts[1].upper()
    if sym not in SYM:
        await send(chat_id, f"❌ Монета `{sym}` не найдена.\nДоступные: {', '.join(SYM)}")
        return
    try:
        target = float(parts[2].replace(',', ''))
    except ValueError:
        await send(chat_id, "❌ Неверная цена. Пример: `/alert BTC 100000`")
        return
    direction = 'below' if len(parts) > 3 and parts[3].lower() == 'below' else 'above'

    if not sb:
        await send(chat_id, "❌ База данных недоступна")
        return
    try:
        sb.table("price_alerts").insert({
            "user_id": user_id,
            "coin_symbol": sym,
            "target_price": target,
            "direction": direction,
            "is_active": True,
        }).execute()
        dir_txt = "вырастет выше" if direction == 'above' else "упадёт ниже"
        await send(chat_id,
            f"✅ *Алерт создан!*\n\n"
            f"📌 {sym} — когда цена {dir_txt} *${target:,.2f}*\n"
            f"🔔 Проверка каждые 15 минут.\n\n"
            f"Список алертов: /alerts"
        )
    except Exception as e:
        logger.error(f"Alert create error: {e}")
        await send(chat_id, f"❌ Ошибка создания алерта: {e}")


async def on_alerts_list(chat_id: int, user_id: int):
    """Handle /alerts — list active alerts for user."""
    if not sb:
        await send(chat_id, "❌ База данных недоступна")
        return
    try:
        rows = sb.table("price_alerts").select("*").eq(
            "user_id", user_id
        ).eq("is_active", True).order("created_at", desc=True).execute().data
        if not rows:
            await send(chat_id,
                "📭 *Нет активных алертов*\n\n"
                "Создайте алерт:\n`/alert BTC 100000`\n`/alert ETH 2000 below`"
            )
            return
        lines = ["🔔 *Ваши активные алерты:*\n"]
        for r in rows:
            dir_icon = "📈" if r['direction'] == 'above' else "📉"
            dir_txt  = "выше" if r['direction'] == 'above' else "ниже"
            lines.append(
                f"{dir_icon} `#{r['id']}` *{r['coin_symbol']}* — {dir_txt} *${float(r['target_price']):,.2f}*"
            )
        lines.append("\n_/delalert <id> — удалить_")
        await send(chat_id, "\n".join(lines))
    except Exception as e:
        await send(chat_id, f"❌ Ошибка: {e}")


async def on_del_alert(chat_id: int, user_id: int, alert_id: Optional[int]):
    """Handle /delalert <id>"""
    if alert_id is None:
        await send(chat_id, "⚠️ Укажите ID: `/delalert 5`\nСписок: /alerts")
        return
    if not sb:
        await send(chat_id, "❌ База данных недоступна")
        return
    try:
        sb.table("price_alerts").update({"is_active": False}).eq(
            "id", alert_id
        ).eq("user_id", user_id).execute()
        await send(chat_id, f"🗑️ Алерт `#{alert_id}` удалён.")
    except Exception as e:
        await send(chat_id, f"❌ Ошибка: {e}")


async def check_price_alerts(prices: dict):
    """Check all active alerts and notify users."""
    if not sb:
        return
    try:
        rows = sb.table("price_alerts").select("*").eq("is_active", True).execute().data
        if not rows:
            return
        triggered = []
        for r in rows:
            sym = r["coin_symbol"]
            p = prices.get(sym, {}).get("current_price", 0)
            if not p:
                continue
            target = float(r["target_price"])
            hit = (r["direction"] == "above" and p >= target) or \
                  (r["direction"] == "below"  and p <= target)
            if hit:
                triggered.append((r, p, target))
        if not triggered:
            return
        logger.info(f"🔔 {len(triggered)} price alerts triggered!")
        from datetime import datetime, timezone as tz
        now = datetime.now(tz.utc).isoformat()
        for r, cur_price, target in triggered:
            # Deactivate alert
            sb.table("price_alerts").update({
                "is_active": False,
                "triggered_at": now,
            }).eq("id", r["id"]).execute()
            sym = r["coin_symbol"]
            dir_icon = "📈" if r["direction"] == "above" else "📉"
            dir_txt  = "вырос выше" if r["direction"] == "above" else "упал ниже"
            msg = (
                f"🔔 *CAIOS Алерт сработал!*\n\n"
                f"{dir_icon} *{sym}* {dir_txt} *${target:,.2f}*\n"
                f"💰 Текущая цена: *${cur_price:,.2f}*\n\n"
                f"_Получить AI анализ: /signal {sym}_"
            )
            try:
                await send(int(r["user_id"]), msg)
            except Exception as e:
                logger.warning(f"Alert send error to {r['user_id']}: {e}")
    except Exception as e:
        logger.error(f"check_price_alerts error: {e}")


# ─── PORTFOLIO ────────────────────────────
async def on_trade(chat_id: int, user_id: int, text: str, action: str):
    """Handle /buy BTC 0.1 [price] and /sell BTC 0.1 [price]"""
    parts = text.split()
    action_ru = "покупку" if action == 'buy' else "продажу"
    if len(parts) < 3:
        await send(chat_id,
            f"⚠️ *Формат:* `/{action} <МОНЕТА> <КОЛ-ВО> [цена]`\n\n"
            f"Примеры:\n"
            f"`/{action} BTC 0.1` — по текущей цене\n"
            f"`/{action} BTC 0.1 95000` — по указанной цене"
        )
        return
    sym = parts[1].upper()
    if sym not in SYM:
        await send(chat_id, f"❌ Монета `{sym}` не найдена.\nДоступные: {', '.join(SYM)}")
        return
    try:
        amount = float(parts[2])
        if amount <= 0:
            raise ValueError
    except ValueError:
        await send(chat_id, f"❌ Неверное количество. Пример: `/{action} BTC 0.1`")
        return

    # Get price
    if len(parts) >= 4:
        try:
            price = float(parts[3].replace(',', ''))
        except ValueError:
            await send(chat_id, "❌ Неверная цена.")
            return
    else:
        # Fetch current price
        try:
            coin = SYM[sym]
            prices = await get_prices([coin["id"]])
            price = prices.get(sym, {}).get("current_price", 0)
            if not price:
                await send(chat_id, f"❌ Не удалось получить цену {sym}")
                return
        except Exception as e:
            await send(chat_id, f"❌ Ошибка получения цены: {e}")
            return

    if not sb:
        await send(chat_id, "❌ База данных недоступна")
        return
    try:
        sb.table("portfolio").insert({
            "user_id": user_id,
            "coin_symbol": sym,
            "action": action,
            "amount": amount,
            "price": price,
        }).execute()
        total = amount * price
        action_icon = "🟢" if action == 'buy' else "🔴"
        await send(chat_id,
            f"{action_icon} *{action_ru.capitalize()} записана!*\n\n"
            f"💰 {sym}: {amount} × ${price:,.2f} = *${total:,.2f}*\n\n"
            f"📊 Портфель: /portfolio"
        )
    except Exception as e:
        logger.error(f"Trade error: {e}")
        await send(chat_id, f"❌ Ошибка записи: {e}")


async def on_portfolio(chat_id: int, user_id: int):
    """Handle /portfolio — show holdings and P&L."""
    if not sb:
        await send(chat_id, "❌ База данных недоступна")
        return
    try:
        rows = sb.table("portfolio").select("*").eq(
            "user_id", user_id
        ).order("created_at", desc=True).execute().data
        if not rows:
            await send(chat_id,
                "💼 *Портфель пуст*\n\n"
                "Добавьте сделку:\n`/buy BTC 0.1`\n`/buy ETH 2 3000`"
            )
            return

        # Calculate holdings per coin
        holdings: dict = {}
        for r in rows:
            sym = r["coin_symbol"]
            if sym not in holdings:
                holdings[sym] = {"amount": 0.0, "cost": 0.0, "trades": 0}
            mult = 1 if r["action"] == 'buy' else -1
            holdings[sym]["amount"] += mult * float(r["amount"])
            holdings[sym]["cost"]   += mult * float(r["total_usd"] or 0)
            holdings[sym]["trades"] += 1

        # Remove zero/negative holdings
        holdings = {k: v for k, v in holdings.items() if v["amount"] > 1e-10}

        if not holdings:
            await send(chat_id, "💼 Нет открытых позиций (все проданы).")
            return

        # Fetch current prices
        ids = [SYM[s]["id"] for s in holdings if s in SYM]
        prices = await get_prices(ids)

        total_cost   = 0.0
        total_value  = 0.0
        lines = ["💼 *Ваш портфель CAIOS*\n"]
        for sym, h in holdings.items():
            cur = prices.get(sym, {}).get("current_price", 0)
            value = h["amount"] * cur
            pnl   = value - h["cost"]
            pnl_pct = (pnl / h["cost"] * 100) if h["cost"] else 0
            pnl_icon = "📈" if pnl >= 0 else "📉"
            total_cost  += h["cost"]
            total_value += value
            lines.append(
                f"{pnl_icon} *{sym}*: {h['amount']:.6g} ед.\n"
                f"   Цена: ${cur:,.2f} | Стоимость: ${value:,.2f}\n"
                f"   P&L: {'+'if pnl>=0 else ''}{pnl:,.2f}$ ({pnl_pct:+.1f}%)"
            )

        total_pnl = total_value - total_cost
        total_pct = (total_pnl / total_cost * 100) if total_cost else 0
        pnl_icon  = "📈" if total_pnl >= 0 else "📉"
        lines.append(
            f"\n{'─'*28}\n"
            f"{pnl_icon} *Итого:* ${total_value:,.2f}\n"
            f"   Вложено: ${total_cost:,.2f}\n"
            f"   P&L: {'+'if total_pnl>=0 else ''}{total_pnl:,.2f}$ ({total_pct:+.1f}%)"
        )
        await send(chat_id, "\n".join(lines))
    except Exception as e:
        logger.error(f"Portfolio error: {e}")
        await send(chat_id, f"❌ Ошибка: {e}")


async def alert_check_loop():
    """Check price alerts every 15 minutes."""
    await asyncio.sleep(120)  # initial delay
    while True:
        try:
            prices = await get_prices([c["id"] for c in TOP_COINS])
            await check_price_alerts(prices)
        except Exception as e:
            logger.error(f"Alert loop error: {e}")
        await asyncio.sleep(900)  # 15 minutes


async def run_ai_council_bg():
    """Run AI Council as background subprocess — saves results to DB."""
    import subprocess, sys
    script = __file__.replace("app/telegram/bot.py", "run_ai_council.py").replace(
        "app\\telegram\\bot.py", "run_ai_council.py"
    )
    logger.info(f"🤖 Starting AI Council background run...")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, script,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=300)
        logger.info(f"✅ AI Council done (exit={proc.returncode})")
    except asyncio.TimeoutError:
        logger.warning("⚠️ AI Council timeout after 5min")
    except Exception as e:
        logger.error(f"AI Council error: {e}")


# ─── HOURLY DIGEST ────────────────────────
async def hourly_loop():
    await asyncio.sleep(60)
    while True:
        try:
            # 1. Run AI Council to refresh signals
            asyncio.create_task(run_ai_council_bg())

            if subscribers:
                logger.info(f"Sending hourly digest to {len(subscribers)} subscribers...")
                # Fetch prices
                prices = await get_prices([c["id"] for c in TOP_COINS])
                # Fetch signals from DB
                db_sigs = get_db_signals()
                now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
                sig_emoji = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}
                lines = [f"⏰ *CAIOS — Часовой дайджест*\n_{now}_\n"]
                for coin in TOP_COINS:
                    d    = prices.get(coin["symbol"], {})
                    p    = d.get("current_price", 0)
                    ch   = d.get("price_change_percentage_24h", 0)
                    sig  = db_sigs.get(coin["symbol"], {})
                    sem  = sig_emoji.get(sig.get("signal",""), "⬜")
                    conf = int(sig.get("confidence", 0) * 100) if sig else 0
                    price_arrow = "📈" if ch >= 0 else "📉"
                    sig_part = f" {sem}{conf}%" if sig else ""
                    lines.append(f"{price_arrow} *{coin['symbol']}* ${p:,.2f} ({ch:+.1f}%){sig_part}")
                lines.append("\n_Нажмите монету для AI анализа:_")
                text = "\n".join(lines)
                for uid in list(subscribers):
                    try:
                        await send(uid, text, keyboard=coins_kb())
                    except Exception as e:
                        logger.warning(f"Can't send to {uid}: {e}")
                        subscribers.discard(uid)
        except Exception as e:
            logger.error(f"Hourly loop error: {e}")
        await asyncio.sleep(3600)


# ─── POLLING LOOP ─────────────────────────
async def polling_loop():
    """Single-threaded polling. ONE getUpdates at a time. No 409 possible."""
    offset = 0
    backoff = 1
    while True:
        try:
            async with httpx.AsyncClient(timeout=40) as c:
                r = await c.post(f"{TG_API}/getUpdates", json={
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": ["message", "callback_query"],
                })
            data = r.json()
            if not data.get("ok"):
                desc = data.get("description", "")
                if "409" in str(data.get("error_code","")) or "Conflict" in desc:
                    logger.warning(f"409 Conflict — retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, 60)
                    continue
                logger.warning(f"Telegram error: {data}")
                await asyncio.sleep(5)
                continue
            backoff = 1  # reset backoff on success
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                asyncio.create_task(dispatch(upd))
        except asyncio.TimeoutError:
            pass  # normal — no updates in 30s
        except Exception as e:
            logger.warning(f"Poll error: {e} — retrying in 3s")
            await asyncio.sleep(3)


# ─── MAIN ─────────────────────────────────
async def main():
    # Clear any webhook and steal session
    logger.info("Clearing webhook and stealing Telegram session...")
    for _ in range(3):
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.post(f"{TG_API}/deleteWebhook", json={"drop_pending_updates": True})
                # Call getUpdates with timeout=0 to break any existing long-poll
                await c.post(f"{TG_API}/getUpdates", json={"timeout": 0, "limit": 1})
        except Exception:
            pass
        await asyncio.sleep(2)

    logger.info("✅ CAIOS Bot starting (raw HTTP polling — NO Application, NO APScheduler)")
    logger.info(f"   Coins: {', '.join(c['symbol'] for c in TOP_COINS)}")
    logger.info(f"   Subscribers: {subscribers}")

    # Run polling, hourly digest and alert checks concurrently
    await asyncio.gather(
        polling_loop(),
        hourly_loop(),
        alert_check_loop(),
    )


if __name__ == "__main__":
    asyncio.run(main())
