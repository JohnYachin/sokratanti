"""
CAIOS Phase 3+4 — AI Council Runner with Technical Indicators
Запускает GPT-4o агентов для каждой из 10 монет с RSI/MACD/BB/EMA.
Схема: voting_cycles → agent_executions → signals + market_indicators
"""
import asyncio, json, time, uuid, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
from supabase import create_client

# Import indicators module
sys.path.insert(0, str(Path(__file__).parent))
from app.data.indicators import compute_all, format_for_prompt

SB_URL  = "https://zrvsuwdlhnnfvqxxohex.supabase.co"
SB_KEY  = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY"
OAI_KEY = "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"

OAI_HEADERS = {"Authorization": f"Bearer {OAI_KEY}", "Content-Type": "application/json"}

SIGNAL_WEIGHTS = {
    "STRONG_BUY": 2.0,
    "BUY": 1.0,
    "HOLD": 0.0,
    "SELL": -1.0,
    "STRONG_SELL": -2.0,
}

TOP_10 = [
    {"symbol": "BTC",  "coingecko_id": "bitcoin"},
    {"symbol": "ETH",  "coingecko_id": "ethereum"},
    {"symbol": "BNB",  "coingecko_id": "binancecoin"},
    {"symbol": "SOL",  "coingecko_id": "solana"},
    {"symbol": "XRP",  "coingecko_id": "ripple"},
    {"symbol": "ADA",  "coingecko_id": "cardano"},
    {"symbol": "AVAX", "coingecko_id": "avalanche-2"},
    {"symbol": "DOT",  "coingecko_id": "polkadot"},
    {"symbol": "MATIC","coingecko_id": "matic-network"},
    {"symbol": "LINK", "coingecko_id": "chainlink"},
]


async def fetch_prices() -> dict:
    ids = ",".join(c["coingecko_id"] for c in TOP_10)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": ids, "order": "market_cap_desc", "sparkline": "false"}
        )
    return {d["id"]: d for d in r.json()}


async def fetch_fear_greed() -> dict:
    """Fetch Fear & Greed Index from alternative.me. Returns {value, label, text}."""
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://api.alternative.me/fng/?limit=2")
        data = r.json()["data"]
        today     = data[0]
        yesterday = data[1] if len(data) > 1 else data[0]
        val   = int(today["value"])
        label = today["value_classification"]
        prev  = int(yesterday["value"])
        trend = "improving" if val > prev else "worsening" if val < prev else "stable"
        return {
            "value":  val,
            "label":  label,
            "trend":  trend,
            "text":   f"{val}/100 ({label}) — {trend} vs yesterday ({prev})",
        }
    except Exception as e:
        print(f"   ⚠️  Fear & Greed fetch error: {e}")
        return {"value": 50, "label": "Neutral", "trend": "stable", "text": "N/A"}


async def fetch_ohlcv(coingecko_id: str, days: int = 90) -> dict:
    """
    Fetch daily close prices + volumes from CoinGecko market_chart.
    Returns {closes: [...], volumes: [...]} or empty dict on error.
    """
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"https://api.coingecko.com/api/v3/coins/{coingecko_id}/market_chart",
                params={"vs_currency": "usd", "days": days, "interval": "daily"}
            )
        data = r.json()
        closes  = [p[1] for p in data.get("prices", [])]
        volumes = [v[1] for v in data.get("total_volumes", [])]
        return {"closes": closes, "volumes": volumes}
    except Exception as e:
        print(f"   ⚠️  OHLCV fetch error for {coingecko_id}: {e}")
        return {}


async def compute_and_save_indicators(sb, coin_id: str, coingecko_id: str, current_price: float) -> dict:
    """
    Fetch historical data, compute indicators, save to market_indicators.
    Returns indicator dict for use in agent prompts.
    """
    ohlcv = await fetch_ohlcv(coingecko_id)
    if not ohlcv:
        return {}

    closes  = ohlcv["closes"]
    volumes = ohlcv["volumes"]
    ind = compute_all(closes, volumes, current_price)
    if not ind:
        return {}

    try:
        sb.table("market_indicators").insert({
            "coin_id": coin_id,
            **{k: float(v) if v is not None else None for k, v in ind.items() if k != "trend_signal"},
            "trend_signal": ind.get("trend_signal"),
        }).execute()
    except Exception as e:
        # Ignore duplicate date errors
        if "unique" not in str(e).lower():
            print(f"   ⚠️  Indicators save error: {e}")

    return ind


async def call_agent(agent: dict, coin_symbol: str, coin_name: str, market: dict,
                     indicators: dict = None, fng: dict = None) -> dict:
    """Call one GPT-4o agent with market data + technical indicators + Fear & Greed."""
    start = time.time()
    price  = market.get("current_price", 0)
    ch24   = market.get("price_change_percentage_24h", 0)
    vol    = market.get("total_volume", 0)
    mcap   = market.get("market_cap", 0)
    hi24   = market.get("high_24h", 0)
    lo24   = market.get("low_24h", 0)

    ind_text = format_for_prompt(indicators, price) if indicators else "Technical indicators: not available"
    fng_val  = fng.get("value", 50) if fng else 50
    fng_txt  = fng.get("text", "N/A") if fng else "N/A"
    fng_zone = (
        "Extreme Fear (contrarian BUY opportunity)" if fng_val <= 25 else
        "Fear (cautiously bullish)"                if fng_val <= 45 else
        "Neutral"                                  if fng_val <= 55 else
        "Greed (caution — market may be overheated)" if fng_val <= 75 else
        "Extreme Greed (contrarian SELL signal)"
    )

    user_msg = (
        f"Analyze {coin_name} ({coin_symbol}) for a trading signal.\n\n"
        f"📈 Market Data:\n"
        f"  Current Price: ${price:,.4f}\n"
        f"  24h Change: {ch24:+.2f}%\n"
        f"  24h High: ${hi24:,.4f} | Low: ${lo24:,.4f}\n"
        f"  24h Volume: ${vol:,.0f}\n"
        f"  Market Cap: ${mcap:,.0f}\n\n"
        f"😨 Market Sentiment (Fear & Greed): {fng_txt}\n"
        f"  Interpretation: {fng_zone}\n\n"
        f"{ind_text}\n\n"
        f"Your specialization: {agent['specialization']}\n"
        f"Use ALL available data (price action + technical indicators + market sentiment) to form your view.\n\n"
        f'Respond ONLY with valid JSON (no markdown):\n'
        f'{{"signal": "STRONG_BUY"|"BUY"|"HOLD"|"SELL"|"STRONG_SELL", '
        f'"confidence": 0.0-1.0, "reasoning": "2-3 sentences referencing specific indicators and sentiment"}}'
    )

    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(
                "https://api.openai.com/v1/chat/completions",
                headers=OAI_HEADERS,
                json={
                    "model": agent.get("model_version", "gpt-4o"),
                    "temperature": 0.2,
                    "max_tokens": 200,
                    "messages": [
                        {"role": "system", "content": agent["system_prompt"]},
                        {"role": "user", "content": user_msg},
                    ],
                }
            )
        raw = r.json()["choices"][0]["message"]["content"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        vote = json.loads(raw)
        signal = vote.get("signal", "HOLD").upper()
        if signal not in SIGNAL_WEIGHTS:
            signal = "HOLD"
        exec_ms = int((time.time() - start) * 1000)
        return {
            "agent_id": agent["id"],
            "signal": signal,
            "confidence": min(max(float(vote.get("confidence", 0.5)), 0.0), 1.0),
            "reasoning": vote.get("reasoning", ""),
            "raw_output": raw,
            "execution_time_ms": exec_ms,
            "status": "success",
        }
    except Exception as e:
        exec_ms = int((time.time() - start) * 1000)
        return {
            "agent_id": agent["id"],
            "signal": "HOLD",
            "confidence": 0.0,
            "reasoning": f"Error: {e}",
            "raw_output": None,
            "execution_time_ms": exec_ms,
            "status": "error",
            "error_message": str(e),
        }


def calculate_consensus(votes: list[dict], agents_by_id: dict) -> tuple[str, float, float]:
    """Weighted majority vote. Returns (signal, confidence, consensus_score)."""
    total_weight = 0
    weighted_score = 0
    for vote in votes:
        agent = agents_by_id.get(vote["agent_id"], {})
        weight = float(agent.get("weight", 1.0))
        signal_score = SIGNAL_WEIGHTS.get(vote["signal"], 0.0)
        conf = vote.get("confidence", 0.5)
        weighted_score += signal_score * conf * weight
        total_weight += weight

    normalized = weighted_score / total_weight if total_weight > 0 else 0.0
    avg_conf = sum(v.get("confidence", 0) for v in votes) / max(len(votes), 1)

    if normalized >= 1.2:
        final_signal = "STRONG_BUY"
    elif normalized >= 0.4:
        final_signal = "BUY"
    elif normalized <= -1.2:
        final_signal = "STRONG_SELL"
    elif normalized <= -0.4:
        final_signal = "SELL"
    else:
        final_signal = "HOLD"

    return final_signal, avg_conf, normalized


async def run_council_for_coin(sb, agents: list, agents_by_id: dict,
                                coin_data: dict, market: dict,
                                indicators: dict = None, fng: dict = None) -> dict:
    """Run full AI Council cycle for one coin with technical indicators."""
    coin_id   = coin_data["id"]
    coin_sym  = coin_data["symbol"]
    coin_name = coin_data.get("name", coin_sym)
    price     = market.get("current_price", 0)
    now       = datetime.now(timezone.utc)

    trend = indicators.get("trend_signal", "N/A") if indicators else "N/A"
    rsi   = indicators.get("rsi_14") if indicators else None
    print(f"\n{'='*50}")
    print(f"⚙️  Running AI Council for {coin_sym} | ${price:,.2f} | RSI={rsi:.1f if rsi else 'N/A'} | {trend}")
    print(f"{'='*50}")

    # 1. Create voting_cycle (status=in_progress)
    cycle_id = str(uuid.uuid4())
    try:
        sb.table("voting_cycles").insert({
            "id": cycle_id,
            "coin_id": coin_id,
            "started_at": now.isoformat(),
            "status": "in_progress",
            "agents_responded": 0,
        }).execute()
        print(f"   ✓ Created voting cycle: {cycle_id[:8]}...")
    except Exception as e:
        print(f"   ✗ Failed to create cycle: {e}")
        return {"coin": coin_sym, "error": str(e)}

    # 2. Call all agents in parallel (with indicators + Fear & Greed)
    print(f"   🤖 Calling {len(agents)} agents in parallel...")
    tasks = [call_agent(agent, coin_sym, coin_name, market, indicators, fng) for agent in agents]
    votes = await asyncio.gather(*tasks)
    print(f"   ✓ Got {len(votes)} votes")

    # 3. Save agent_executions
    for vote in votes:
        try:
            agent_exec = {
                "agent_id": vote["agent_id"],
                "cycle_id": cycle_id,
                "coin_id": coin_id,
                "signal": vote["signal"],
                "confidence": round(vote["confidence"], 3),
                "reasoning": vote["reasoning"],
                "execution_time_ms": vote["execution_time_ms"],
                "status": vote["status"],
            }
            if vote.get("error_message"):
                agent_exec["error_message"] = vote["error_message"]
            sb.table("agent_executions").insert(agent_exec).execute()
            agent_name = agents_by_id.get(vote["agent_id"], {}).get("name", "?")
            em = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}.get(vote["signal"],"❓")
            print(f"   {em} {agent_name}: {vote['signal']} ({int(vote['confidence']*100)}%) — {vote['reasoning'][:60]}...")
        except Exception as e:
            print(f"   ✗ agent_execution save error: {e}")

    # 4. Calculate consensus
    final_signal, avg_conf, score = calculate_consensus(votes, agents_by_id)
    completed_at = datetime.now(timezone.utc)
    valid_until  = completed_at + timedelta(hours=1)

    signal_emoji = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}.get(final_signal,"❓")
    print(f"\n   {signal_emoji} CONSENSUS: {final_signal} | Conf: {int(avg_conf*100)}% | Score: {score:.3f}")

    # 5. Save signal
    result_json = {
        "votes": [{"agent": agents_by_id.get(v["agent_id"],{}).get("name","?"),
                   "signal": v["signal"], "confidence": v["confidence"],
                   "reasoning": v["reasoning"]} for v in votes],
        "final_signal": final_signal,
        "confidence": round(avg_conf, 3),
        "consensus_score": round(score, 4),
        "price_at_analysis": price,
    }
    try:
        sb.table("signals").insert({
            "coin_id": coin_id,
            "cycle_id": cycle_id,
            "signal": final_signal,
            "confidence": round(avg_conf, 3),
            "score": round(score, 4),
            "price_at_signal": price,
            "valid_until": valid_until.isoformat(),
            "is_active": True,
        }).execute()
        print(f"   ✓ Signal saved to DB")
    except Exception as e:
        print(f"   ✗ Signal save error: {e}")

    # 6. Update voting_cycle to completed
    try:
        sb.table("voting_cycles").update({
            "completed_at": completed_at.isoformat(),
            "agents_responded": len([v for v in votes if v["status"] == "success"]),
            "final_signal": final_signal,
            "final_confidence": round(avg_conf, 3),
            "consensus_score": round(score, 4),
            "result_json": result_json,
            "status": "completed",
        }).eq("id", cycle_id).execute()
        print(f"   ✓ Voting cycle updated to completed")
    except Exception as e:
        print(f"   ✗ Cycle update error: {e}")

    return {
        "coin": coin_sym,
        "signal": final_signal,
        "confidence": avg_conf,
        "score": score,
        "price": price,
    }


async def main():
    sb = create_client(SB_URL, SB_KEY)
    print("✅ Supabase connected")

    # Load agents
    agents = sb.table("agents").select("*").eq("is_active", True).execute().data
    if not agents:
        print("❌ No active agents in DB")
        return
    agents_by_id = {a["id"]: a for a in agents}
    print(f"✅ Loaded {len(agents)} agents: {[a['name'] for a in agents]}")

    # Load coins with their DB IDs
    db_coins = sb.table("coins").select("id,symbol,name,coingecko_id").execute().data
    target_syms = {c["symbol"] for c in TOP_10}
    coins = [c for c in db_coins if c["symbol"] in target_syms]
    coins_by_cgid = {c["coingecko_id"]: c for c in coins}
    print(f"✅ Loaded {len(coins)} target coins")

    # Fetch current prices + Fear & Greed Index
    print("\n📊 Fetching market data...")
    markets, fng = await asyncio.gather(fetch_prices(), fetch_fear_greed())
    fng_icon = "😨" if fng["value"] <= 30 else "🤦" if fng["value"] >= 70 else "😐"
    print(f"✅ Prices: {len(markets)} coins | {fng_icon} Fear & Greed: {fng['text']}")

    # Deactivate old signals
    try:
        sb.table("signals").update({"is_active": False}).eq("is_active", True).execute()
        print("✅ Old signals deactivated")
    except Exception as e:
        print(f"⚠️  Deactivate old signals: {e}")

    # Run AI Council for each coin (with indicators)
    results = []
    now = datetime.now(timezone.utc)
    print(f"\n🚀 Starting AI Council run — {now.strftime('%d.%m.%Y %H:%M UTC')}")

    for coin_info in TOP_10:
        cg_id  = coin_info["coingecko_id"]
        market = markets.get(cg_id)
        coin   = coins_by_cgid.get(cg_id)
        if not market or not coin:
            print(f"\n⚠️  Skipping {coin_info['symbol']}: missing data")
            continue

        # Phase 3: Compute technical indicators
        price = market.get("current_price", 0)
        print(f"\n📐 Computing indicators for {coin_info['symbol']}...")
        indicators = await compute_and_save_indicators(sb, coin["id"], cg_id, price)
        if indicators:
            trend = indicators.get("trend_signal", "?")
            rsi   = indicators.get("rsi_14")
            macd_h = indicators.get("macd_histogram")
            print(f"   ✓ RSI={rsi:.1f if rsi else 'N/A'} | MACD_hist={macd_h:+.4f if macd_h else 'N/A'} | Trend={trend}")
        else:
            print(f"   ⚠️  No indicators (need more history)")

        result = await run_council_for_coin(sb, agents, agents_by_id, coin, market, indicators, fng)
        results.append(result)

    # Print summary
    print(f"\n{'='*55}")
    print(f"📊 CAIOS AI COUNCIL — SUMMARY")
    print(f"{'='*55}")
    for r in sorted(results, key=lambda x: abs(x.get("score", 0)), reverse=True):
        if "error" in r:
            continue
        em = {"BUY":"🟢","STRONG_BUY":"🟢🟢","SELL":"🔴","STRONG_SELL":"🔴🔴","HOLD":"🟡"}.get(r["signal"],"❓")
        print(f"{em} {r['coin']:5s} {r['signal']:12s} ${r['price']:>12,.2f}  conf:{int(r['confidence']*100)}%  score:{r['score']:+.2f}")

    # Count signals
    db_sigs = sb.table("signals").select("id,signal").eq("is_active", True).execute().data
    buy_count  = sum(1 for s in db_sigs if s["signal"] in ("BUY","STRONG_BUY"))
    sell_count = sum(1 for s in db_sigs if s["signal"] in ("SELL","STRONG_SELL"))
    hold_count = sum(1 for s in db_sigs if s["signal"] == "HOLD")
    print(f"\n🏆 Total: {buy_count} BUY | {hold_count} HOLD | {sell_count} SELL")
    print(f"✅ Signals saved to DB: {len(db_sigs)}")
    print(f"\nDone in {(datetime.now(timezone.utc) - now).seconds}s")


if __name__ == "__main__":
    asyncio.run(main())
