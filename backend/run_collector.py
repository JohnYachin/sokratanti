"""
CAIOS Data Collector — заполняет Supabase реальными данными с CoinGecko.
Запускается один раз для первоначальной загрузки данных.
"""
import os, time, json, uuid
from datetime import datetime, timezone
from supabase import create_client
import httpx

SUPABASE_URL = "https://zrvsuwdlhnnfvqxxohex.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY"

TOP_10 = [
    {"symbol": "BTC",   "name": "Bitcoin",   "coingecko_id": "bitcoin",     "rank": 1},
    {"symbol": "ETH",   "name": "Ethereum",  "coingecko_id": "ethereum",    "rank": 2},
    {"symbol": "BNB",   "name": "BNB",       "coingecko_id": "binancecoin", "rank": 3},
    {"symbol": "SOL",   "name": "Solana",    "coingecko_id": "solana",      "rank": 4},
    {"symbol": "XRP",   "name": "XRP",       "coingecko_id": "ripple",      "rank": 5},
    {"symbol": "ADA",   "name": "Cardano",   "coingecko_id": "cardano",     "rank": 6},
    {"symbol": "AVAX",  "name": "Avalanche", "coingecko_id": "avalanche-2", "rank": 7},
    {"symbol": "DOT",   "name": "Polkadot",  "coingecko_id": "polkadot",    "rank": 8},
    {"symbol": "MATIC", "name": "Polygon",   "coingecko_id": "matic-network","rank": 9},
    {"symbol": "LINK",  "name": "Chainlink", "coingecko_id": "chainlink",   "rank": 10},
]

def main():
    sb = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("✅ Supabase connected")

    # 1. Upsert coins
    print("\n📌 Step 1: Upserting coins into DB...")
    for coin in TOP_10:
        try:
            sb.table("coins").upsert({
                "symbol": coin["symbol"],
                "name": coin["name"],
                "coingecko_id": coin["coingecko_id"],
                "rank": coin["rank"],
                "is_active": True,
            }, on_conflict="coingecko_id").execute()
            print(f"  ✓ {coin['symbol']}")
        except Exception as e:
            print(f"  ✗ {coin['symbol']}: {e}")

    # 2. Fetch current prices
    print("\n📊 Step 2: Fetching prices from CoinGecko...")
    ids = ",".join(c["coingecko_id"] for c in TOP_10)
    try:
        r = httpx.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": ids, "order": "market_cap_desc", "sparkline": "false"},
            timeout=20
        )
        markets = {d["id"]: d for d in r.json()}
        print(f"  ✓ Got data for {len(markets)} coins")
    except Exception as e:
        print(f"  ✗ CoinGecko error: {e}")
        markets = {}

    # 3. Fetch coin UUIDs from DB and update prices + snapshots
    print("\n💾 Step 3: Saving prices + snapshots...")
    db_coins = sb.table("coins").select("id,coingecko_id,symbol").execute().data or []
    coin_uuid_map = {c["coingecko_id"]: c["id"] for c in db_coins}

    now = datetime.now(timezone.utc).isoformat()
    snapshots_saved = 0

    for coin in TOP_10:
        cg_id = coin["coingecko_id"]
        coin_uuid = coin_uuid_map.get(cg_id)
        market = markets.get(cg_id, {})

        if not coin_uuid:
            print(f"  ✗ {coin['symbol']}: UUID not found in DB")
            continue
        if not market:
            print(f"  ✗ {coin['symbol']}: No market data")
            continue

        price = market.get("current_price", 0)
        vol   = market.get("total_volume", 0)
        mcap  = market.get("market_cap", 0)
        ch24  = market.get("price_change_percentage_24h", 0)
        hi24  = market.get("high_24h", 0)
        lo24  = market.get("low_24h", 0)

        # Update coins table with latest price
        try:
            sb.table("coins").update({
                "rank": market.get("market_cap_rank", coin["rank"]),
            }).eq("id", coin_uuid).execute()
        except Exception as e:
            print(f"  ⚠ Update {coin['symbol']}: {e}")

        try:
            snapshot = {
                "id": str(uuid.uuid4()),
                "coin_id": coin_uuid,
                "price": price,
                "price_change_24h": ch24,
                "volume_24h": vol,
                "market_cap": mcap,
                "high_24h": hi24,
                "low_24h": lo24,
                "snapshot_at": now,
            }
            sb.table("market_snapshots").insert(snapshot).execute()
            snapshots_saved += 1
            print(f"  ✓ {coin['symbol']}: ${price:,.2f} | 24h: {ch24:+.1f}%")
        except Exception as e:
            print(f"  ✗ Snapshot {coin['symbol']}: {e}")

    print(f"\n✅ Done! {snapshots_saved}/{len(TOP_10)} snapshots saved.")

    # 4. Show DB summary
    print("\n📋 DB Summary:")
    try:
        coins_count = len(sb.table("coins").select("id").execute().data or [])
        snaps_count = len(sb.table("market_snapshots").select("id").execute().data or [])
        print(f"  coins: {coins_count} rows")
        print(f"  market_snapshots: {snaps_count} rows")
    except Exception as e:
        print(f"  Error: {e}")


if __name__ == "__main__":
    main()
