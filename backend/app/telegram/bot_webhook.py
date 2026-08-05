"""
CAIOS Webhook Bot — работает через localhost.run SSH туннель.
НЕТ polling, НЕТ 409 Conflict.
Telegram пушит обновления на HTTPS URL.
"""
import os, asyncio, logging, json, subprocess, sys
from datetime import datetime, timezone

import httpx
from aiohttp import web
from supabase import create_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("caios_wh")
logging.getLogger("httpx").setLevel(logging.WARNING)

TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN", "8937697751:AAFiTO-AnEowrT-XuSVlKZNs8d6BOVGoPXc")
ADMIN   = int(os.getenv("TELEGRAM_USER_ID", "634964003"))
SB_URL  = os.getenv("SUPABASE_URL", "https://zrvsuwdlhnnfvqxxohex.supabase.co")
SB_KEY  = os.getenv("SUPABASE_SERVICE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY")
OAI_KEY = os.getenv("OPENAI_API_KEY", "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")
TG_API  = f"https://api.telegram.org/bot{TOKEN}"
PORT    = 8088  # local webhook port
subscribers: set[int] = {ADMIN}

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

try:
    sb = create_client(SB_URL, SB_KEY)
except Exception:
    sb = None

# ─────────────────────────────────────────────
# Telegram & CoinGecko helpers (shared with bot.py)
# ─────────────────────────────────────────────
async def tg(method: str, **params) -> dict:
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{TG_API}/{method}", json={k: v for k, v in params.items() if v is not None})
        return r.json()

async def send(chat_id: int, text: str, keyboard=None, edit_message_id=None):
    params = dict(chat_id=chat_id, text=text, parse_mode="Markdown")
    if keyboard:
        params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
    if edit_message_id:
        return await tg("editMessageText", message_id=edit_message_id, **params)
    return await tg("sendMessage", **params)

async def get_prices(ids: list[str]) -> dict:
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency":"usd","ids":",".join(ids),"order":"market_cap_desc","sparkline":"false"}
        )
        return {d["symbol"].upper(): d for d in r.json()}

def get_db_signals() -> dict:
    if not sb:
        return {}
    try:
        rows = sb.table("signals").select(
            "signal,confidence,score,price_at_signal,coins(symbol)"
        ).eq("is_active", True).execute().data
        return {r["coins"]["symbol"]: r for r in rows if r.get("coins")}
    except Exception as e:
        logger.warning(f"DB signals: {e}")
        return {}

def coins_kb():
    return [
        [{"text": c["symbol"], "callback_data": f"s_{c['symbol']}"} for c in TOP_COINS[:5]],
        [{"text": c["symbol"], "callback_data": f"s_{c['symbol']}"} for c in TOP_COINS[5:]],
    ]

# ─────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────
async def handle_start(chat_id, user_id):
    subscribers.add(user_id)
    await send(chat_id,
        "🚀 *CAIOS — Crypto AI Investment OS*\n\n"
        "20 AI агентов анализируют топ-10 монет.\n\n"
        "📌 *Команды:*\n"
        "`/signal BTC` — AI сигнал\n"
        "`/top` — топ-10 цен\n"
        "`/subscribe` — часовые алерты\n"
        "`/status` — статус\n\n"
        "⚡ *Выберите монету:*",
        keyboard=coins_kb()
    )

async def handle_top(chat_id):
    msg = await send(chat_id, "⏳ Загружаю данные...")
    mid = msg.get("result", {}).get("message_id")
    try:
        prices = await get_prices([c["id"] for c in TOP_COINS])
        db_sigs = get_db_signals()
        now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")
        sig_emoji = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}
        lines = [f"📊 *CAIOS — Топ 10*\n_{now}_\n"]
        for i, coin in enumerate(TOP_COINS, 1):
            d = prices.get(coin["symbol"], {})
            p = d.get("current_price", 0)
            ch = d.get("price_change_percentage_24h", 0)
            sig = db_sigs.get(coin["symbol"], {})
            sem = sig_emoji.get(sig.get("signal",""), "")
            arrow = "📈" if ch >= 0 else "📉"
            sig_part = f" {sem}" if sem else ""
            lines.append(f"{i}. *{coin['symbol']}* ${p:,.2f} {arrow} {ch:+.1f}%{sig_part}")
        lines.append("\n_Нажмите монету для AI анализа:_")
        await send(chat_id, "\n".join(lines), keyboard=coins_kb(), edit_message_id=mid)
    except Exception as e:
        await send(chat_id, f"❌ {e}", edit_message_id=mid)

async def handle_signal(chat_id, symbol):
    coin = SYM.get(symbol.upper())
    if not coin:
        await send(chat_id, f"❌ `{symbol}` не найдена.\nДоступные: {', '.join(SYM)}")
        return
    sym = coin["symbol"]
    db_sigs = get_db_signals()
    cached = db_sigs.get(sym)
    if cached:
        sig   = cached["signal"]
        conf  = int(cached.get("confidence", 0) * 100)
        score = float(cached.get("score", 0))
        price = float(cached.get("price_at_signal", 0))
        sem   = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}.get(sig,"❓")
        bar   = "█"*(conf//10) + "░"*(10-conf//10)
        try:
            prices = await get_prices([coin["id"]])
            d = prices.get(sym, {})
            price = d.get("current_price", price)
            ch = d.get("price_change_percentage_24h", 0)
        except Exception:
            ch = 0
        ts = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")
        text = (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{sem} *{sym} ({coin['name']}) — {sig}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 *${price:,.4f}*  {'📈' if ch>=0 else '📉'} {ch:+.1f}%\n"
            f"🎯 Уверенность: *{conf}%*  `{bar}`\n"
            f"📊 AI Score: `{score:+.3f}`\n\n"
            f"🤖 _AI Council (20 агентов)_\n"
            f"⏱ _{ts}_"
        )
        kb = [[{"text":"🔄 Обновить","callback_data":f"s_{sym}"},{"text":"◀️ Топ","callback_data":"top"}]]
        await send(chat_id, text, keyboard=kb)
    else:
        await send(chat_id, f"⏳ Нет кеша для {sym}. AI Council запустится через час.\n\nИспользуйте /top для обзора.")

async def handle_status(chat_id):
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get("https://api.coingecko.com/api/v3/ping")
            cg = "✅ Online" if r.status_code == 200 else "❌ Offline"
    except:
        cg = "❌ Offline"
    db = "✅ Online" if sb else "❌ Offline"
    db_sigs = get_db_signals()
    await send(chat_id,
        "📡 *CAIOS — Статус*\n\n"
        f"🗄️ Supabase: {db}\n"
        f"📊 CoinGecko: {cg}\n"
        f"🤖 GPT-4o: ✅ Ready\n"
        f"📡 Режим: Webhook (без 409)\n"
        f"🔔 Подписчиков: {len(subscribers)}\n"
        f"💰 Монет: {len(TOP_COINS)}\n"
        f"📊 AI сигналов в DB: {len(db_sigs)}\n"
        f"⏱ Оповещения: каждый час\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%d.%m.%Y %H:%M UTC')}"
    )

# ─────────────────────────────────────────────
# Webhook handler (aiohttp)
# ─────────────────────────────────────────────
async def webhook_handler(request: web.Request) -> web.Response:
    try:
        update = await request.json()
        asyncio.create_task(dispatch(update))
    except Exception as e:
        logger.error(f"Webhook parse error: {e}")
    return web.Response(text="ok")

async def dispatch(update: dict):
    try:
        if "callback_query" in update:
            cq = update["callback_query"]
            await tg("answerCallbackQuery", callback_query_id=cq["id"])
            data = cq.get("data", "")
            chat_id = cq["message"]["chat"]["id"]
            user_id = cq["from"]["id"]
            if data == "top":
                await handle_top(chat_id)
            elif data == "subscribe":
                subscribers.add(user_id)
                await send(chat_id, "✅ Подписка! Оповещения каждый час.")
            elif data.startswith("s_"):
                await handle_signal(chat_id, data[2:])
            return

        if "message" not in update:
            return
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        user_id = msg["from"]["id"]
        text = msg.get("text", "")

        if text.startswith("/start"):
            await handle_start(chat_id, user_id)
        elif text.startswith("/top"):
            await handle_top(chat_id)
        elif text.startswith("/signal"):
            parts = text.split()
            if len(parts) > 1:
                await handle_signal(chat_id, parts[1])
            else:
                await send(chat_id, f"⚠️ Пример: `/signal BTC`\nМонеты: {', '.join(SYM)}")
        elif text.startswith("/subscribe"):
            subscribers.add(user_id)
            await send(chat_id, "✅ *Подписка активирована!*\n🔔 Каждый час — дайджест. /unsubscribe")
        elif text.startswith("/unsubscribe"):
            subscribers.discard(user_id)
            await send(chat_id, "🔕 Отписан.")
        elif text.startswith("/status"):
            await handle_status(chat_id)
        elif text.startswith("/help"):
            await send(chat_id,
                "📖 *CAIOS — Справка*\n\n"
                "`/signal BTC` — AI сигнал\n"
                "`/top` — топ-10 цен\n"
                "`/subscribe` — часовые алерты\n"
                "`/status` — статус"
            )
    except Exception as e:
        logger.error(f"Dispatch error: {e}")

# ─────────────────────────────────────────────
# Hourly digest + AI Council
# ─────────────────────────────────────────────
async def hourly_loop():
    await asyncio.sleep(60)
    while True:
        try:
            # Trigger AI Council
            script = os.path.join(os.path.dirname(__file__).replace("app/telegram","").replace("app\\telegram",""), "run_ai_council.py")
            logger.info("🤖 Starting AI Council...")
            proc = await asyncio.create_subprocess_exec(
                sys.executable, script,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
            )
            asyncio.create_task(proc.wait())  # don't block

            if subscribers:
                prices = await get_prices([c["id"] for c in TOP_COINS])
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
                    arrow = "📈" if ch >= 0 else "📉"
                    sig_part = f" {sem}" if sig else ""
                    lines.append(f"{arrow} *{coin['symbol']}* ${p:,.2f} ({ch:+.1f}%){sig_part}")
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

# ─────────────────────────────────────────────
# SSH Tunnel via localhost.run
# ─────────────────────────────────────────────
async def start_tunnel() -> tuple:
    """Start localhost.run SSH tunnel. Returns (public_url, proc)."""
    logger.info(f"Starting SSH tunnel for port {PORT}...")
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-o", "StrictHostKeyChecking=no",
        "-o", "ServerAliveInterval=30",
        "-R", f"80:localhost:{PORT}",
        "localhost.run",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    url = None
    import re
    for _ in range(40):  # read more lines
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=15)
        except asyncio.TimeoutError:
            break
        line_str = line.decode("utf-8", errors="ignore").strip()
        if line_str:
            logger.info(f"Tunnel: {line_str}")
        # localhost.run URL formats: .lhr.life or .localhost.run
        m = re.search(r"https://[\w\-\.]+\.(lhr\.life|localhost\.run)", line_str)
        if m:
            url = m.group(0)
            break
    if not url:
        raise Exception("Could not get tunnel URL from localhost.run")
    return url, proc

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
async def main():
    # Start local aiohttp webhook server
    app_web = web.Application()
    app_web.router.add_post(f"/webhook/{TOKEN}", webhook_handler)
    runner = web.AppRunner(app_web)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", PORT)
    await site.start()
    logger.info(f"✅ Local webhook server running on port {PORT}")

    # Start SSH tunnel
    try:
        public_url, tunnel_proc = await start_tunnel()
        webhook_url = f"{public_url}/webhook/{TOKEN}"
        logger.info(f"✅ Tunnel URL: {public_url}")
    except Exception as e:
        logger.error(f"Tunnel failed: {e}")
        logger.info("Falling back to polling mode...")
        # Fallback: import and run polling bot
        from app.telegram import bot as polling_bot
        await polling_bot.main_async() if hasattr(polling_bot, "main_async") else None
        return

    # Register webhook with Telegram
    r = await tg("setWebhook", url=webhook_url, drop_pending_updates=True,
                 allowed_updates=["message", "callback_query"])
    if r.get("ok"):
        logger.info(f"✅ Webhook registered: {webhook_url}")
    else:
        logger.error(f"Webhook registration failed: {r}")

    logger.info("🚀 CAIOS Bot running via Webhook (NO polling, NO 409!)")
    logger.info(f"   Coins: {', '.join(c['symbol'] for c in TOP_COINS)}")
    logger.info(f"   Subscribers: {subscribers}")

    # Start hourly digest
    asyncio.create_task(hourly_loop())

    # Keep running
    try:
        await asyncio.Event().wait()
    finally:
        await tg("deleteWebhook")
        tunnel_proc.terminate()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
