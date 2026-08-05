import time
import httpx
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands
from supabase import create_client, Client
from datetime import datetime, timezone
import uuid

# Supabase setup
SUPABASE_URL = "https://zrvsuwdlhnnfvqxxohex.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InpydnN1d2RsaG5uZnZxeHhvaGV4Iiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc4NTUxMTQwMCwiZXhwIjoyMTAxMDg3NDAwfQ.19YNUSRWeJknVytkfQjvnzsjT0LmvqkWUX0eRRDSGJY"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def fetch_top_coins():
    print("Fetching top 50 coins...")
    url = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=market_cap_desc&per_page=50&page=1&sparkline=false&price_change_percentage=24h"
    response = httpx.get(url, timeout=15)
    response.raise_for_status()
    return response.json()

def fetch_ohlcv(coin_id):
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlcv?vs_currency=usd&days=30"
    response = httpx.get(url, timeout=15)
    if response.status_code == 429:
        print("Rate limited, sleeping for 10 seconds...")
        time.sleep(10)
        response = httpx.get(url, timeout=15)
    response.raise_for_status()
    return response.json()

def calculate_indicators(ohlcv_data):
    if not ohlcv_data or len(ohlcv_data) < 50:
        return None
    
    df = pd.DataFrame(ohlcv_data, columns=['timestamp', 'open', 'high', 'low', 'close'])
    
    # Calculate indicators
    rsi = RSIIndicator(close=df['close'], window=14).rsi().iloc[-1]
    macd = MACD(close=df['close'])
    macd_line = macd.macd().iloc[-1]
    macd_signal = macd.macd_signal().iloc[-1]
    macd_histogram = macd.macd_diff().iloc[-1]
    
    ema_20 = EMAIndicator(close=df['close'], window=20).ema_indicator().iloc[-1]
    ema_50 = EMAIndicator(close=df['close'], window=50).ema_indicator().iloc[-1]
    
    bb = BollingerBands(close=df['close'], window=20, window_dev=2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_middle = bb.bollinger_mavg().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    
    # Handle NaNs
    import math
    def clean(val):
        return None if pd.isna(val) else float(val)

    return {
        'rsi_14': clean(rsi),
        'macd_line': clean(macd_line),
        'macd_signal': clean(macd_signal),
        'macd_histogram': clean(macd_histogram),
        'ema_20': clean(ema_20),
        'ema_50': clean(ema_50),
        'bb_upper': clean(bb_upper),
        'bb_middle': clean(bb_middle),
        'bb_lower': clean(bb_lower),
    }

def collect_data():
    coins = fetch_top_coins()
    now = datetime.now(timezone.utc).isoformat()
    
    for coin in coins:
        cg_id = coin['id']
        symbol = coin['symbol'].upper()
        name = coin['name']
        price = coin['current_price']
        market_cap = coin.get('market_cap')
        volume_24h = coin.get('total_volume')
        price_change_24h = coin.get('price_change_percentage_24h')
        
        print(f"Processing {name} ({symbol})...")
        
        # Upsert coin to get db ID
        coin_record = {
            'symbol': symbol,
            'name': name,
            'coingecko_id': cg_id,
            'current_price': price,
            'market_cap': market_cap,
            'volume_24h': volume_24h,
            'price_change_24h': price_change_24h,
            'updated_at': now
        }
        
        # Check if coin exists
        existing_coin = supabase.table('coins').select('id').eq('coingecko_id', cg_id).execute()
        if existing_coin.data:
            db_coin_id = existing_coin.data[0]['id']
            supabase.table('coins').update(coin_record).eq('id', db_coin_id).execute()
        else:
            db_coin_id = str(uuid.uuid4())
            coin_record['id'] = db_coin_id
            supabase.table('coins').insert(coin_record).execute()
            
        # Insert market snapshot
        snapshot_record = {
            'id': str(uuid.uuid4()),
            'coin_id': db_coin_id,
            'price': price,
            'volume': volume_24h,
            'market_cap': market_cap,
            'snapshot_at': now
        }
        supabase.table('market_snapshots').insert(snapshot_record).execute()
        
        # Fetch OHLCV and calculate indicators
        try:
            ohlcv_data = fetch_ohlcv(cg_id)
            indicators = calculate_indicators(ohlcv_data)
            
            if indicators:
                indicator_record = {
                    'id': str(uuid.uuid4()),
                    'coin_id': db_coin_id,
                    **indicators,
                    'calculated_at': now
                }
                supabase.table('technical_indicators').insert(indicator_record).execute()
                print(f"✅ Data for {symbol} saved.")
            else:
                print(f"⚠️ Not enough data for {symbol} indicators.")
        except Exception as e:
            print(f"❌ Error fetching/calculating for {symbol}: {e}")
            
        time.sleep(2) # rate limit

if __name__ == '__main__':
    collect_data()
