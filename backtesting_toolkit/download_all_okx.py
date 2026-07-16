import os
import requests
import time
import argparse
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# --- Config Defaults ---
DEFAULT_START = "2024-10-20"
DEFAULT_END = "2024-12-31" 
SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP", "BNB-USDT-SWAP", "XRP-USDT-SWAP"]
BASE_URL = "https://www.okx.com/cdn/okex/traderecords" 

# Data Types to Download
TYPES = ["l2", "trades", "candles", "funding"]

# Directory Config
RAW_DIR = "data/okx_raw"
BASE_DIRS = {
    "l2": os.path.join(RAW_DIR, "l2"),
    "trades": os.path.join(RAW_DIR, "trades"),
    "candles": os.path.join(RAW_DIR, "candles"),
    "funding": os.path.join(RAW_DIR, "funding")
}

for d in BASE_DIRS.values():
    os.makedirs(d, exist_ok=True)

def download_file(url, filepath):
    if os.path.exists(filepath):
        # partial check?
        if os.path.getsize(filepath) > 1024:
            print(f"[SKIP] Exists: {filepath}")
            return

    print(f"[DOWNLOADING] {url}")
    try:
        # Use cloudscraper or headers if 403 (OKX often blocks python-requests user-agent)
        headers = {'User-Agent': 'Mozilla/5.0'}
        r = requests.get(url, stream=True, headers=headers, timeout=10)
        
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"[SUCCESS] Saved: {filepath}")
        elif r.status_code == 404:
            print(f"[MISSING] 404: {url}")
        else:
            print(f"[ERROR] {r.status_code} for {url}")
            
    except Exception as e:
        print(f"[EXCEPTION] {e} for {url}")

def get_urls_for_date(date_str, symbols):
    urls = []
    
    # OKX Public Data S3 Base
    # L2: https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/{YYYYMMDD}/{SYM}-L2orderbook-400lv-{YYYY-MM-DD}.tar.gz
    
    date_nodash = date_str.replace("-", "")
    
    for sym in symbols:
        # 1. Trades
        # https://www.okx.com/cdn/okex/traderecords/trades/daily/20240101/BTC-USDT-SWAP-trades-2024-01-01.zip
        url_trades = f"{BASE_URL}/trades/daily/{date_nodash}/{sym}-trades-{date_str}.zip"
        path_trades = os.path.join(BASE_DIRS["trades"], sym, f"{sym}-trades-{date_str}.zip")
        os.makedirs(os.path.dirname(path_trades), exist_ok=True)
        urls.append((url_trades, path_trades))
        
        # 2. Candles (OHLCV) - 1m
        url_candle = f"{BASE_URL}/candlesticks/daily/{date_nodash}/{sym}-candlesticks-{date_str}.zip"
        path_candle = os.path.join(BASE_DIRS["candles"], sym, f"{sym}-candlesticks-{date_str}.zip")
        os.makedirs(os.path.dirname(path_candle), exist_ok=True)
        urls.append((url_candle, path_candle))
        
        # 3. L2 Orderbook (400 level)
        # https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/20240101/BTC-USDT-SWAP-L2orderbook-400lv-2024-01-01.tar.gz
        L2_BASE = "https://static.okx.com/cdn/okx/match/orderbook/L2/400lv"
        url_l2 = f"{L2_BASE}/daily/{date_nodash}/{sym}-L2orderbook-400lv-{date_str}.tar.gz"
        path_l2 = os.path.join(BASE_DIRS["l2"], sym, f"{sym}-L2orderbook-400lv-{date_str}.tar.gz")
        os.makedirs(os.path.dirname(path_l2), exist_ok=True)
        urls.append((url_l2, path_l2))

    # FUNDING RATE (One file per day for ALL symbols)
    FUND_BASE = "https://static.okx.com/cdn/okex/traderecords/swaprates"
    url_fund = f"{FUND_BASE}/daily/{date_nodash}/allswap-fundingrates-{date_str}.zip"
    path_fund = os.path.join(BASE_DIRS["funding"], f"allswap-fundingrates-{date_str}.zip")
    urls.append((url_fund, path_fund))
    
    return urls

def main():
    parser = argparse.ArgumentParser(description="Download OKX Data (L2, Trades, Candles, Funding)")
    parser.add_argument("--start", type=str, default=DEFAULT_START, help="Start Date YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=DEFAULT_END, help="End Date YYYY-MM-DD")
    parser.add_argument("--workers", type=int, default=8, help="Parallel downloads")
    args = parser.parse_args()

    print(f"--- Starting Download: {args.start} to {args.end} ---")
    
    start = datetime.strptime(args.start, "%Y-%m-%d")
    end = datetime.strptime(args.end, "%Y-%m-%d")
    
    all_tasks = []
    current = start
    while current <= end:
        ds = current.strftime("%Y-%m-%d")
        tasks = get_urls_for_date(ds, SYMBOLS)
        all_tasks.extend(tasks)
        current += timedelta(days=1)
        
    print(f"Total Files to Download: {len(all_tasks)}")
    if not all_tasks:
        print("No tasks generated. Check date range.")
        return

    print(f"Sample URL: {all_tasks[0][0]}")
    
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(download_file, u, p) for u, p in all_tasks]
        for i, f in enumerate(futures):
            if i % 100 == 0:
                print(f"Progress: {i}/{len(futures)} tasks submitted...")
            f.result() # Wait for completion/exceptions
            
    print("--- Download Complete ---")

if __name__ == "__main__":
    main()
