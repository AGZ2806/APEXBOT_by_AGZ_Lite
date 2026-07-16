
import os
import requests
import datetime
import time
from concurrent.futures import ThreadPoolExecutor

# Configuration
import argparse
import sys

# Configuration Defaults
DEFAULT_SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
BASE_URL = "https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily"
DOWNLOAD_DIR = "data/downloads"

def ensure_dir(path):
    if not os.path.exists(path):
        os.makedirs(path)

def generate_dates(start, end):
    start_dt = datetime.datetime.strptime(start, "%Y-%m-%d")
    end_dt = datetime.datetime.strptime(end, "%Y-%m-%d")
    delta = end_dt - start_dt
    for i in range(delta.days + 1):
        yield start_dt + datetime.timedelta(days=i)

def download_file(symbol, target_date):
    # Format: YYYYMMDD and YYYY-MM-DD
    date_compact = target_date.strftime("%Y%m%d")
    date_hyphen = target_date.strftime("%Y-%m-%d")
    
    # URL Construction
    # https://static.okx.com/cdn/okx/match/orderbook/L2/400lv/daily/20251109/ETH-USDT-SWAP-L2orderbook-400lv-2025-11-09.tar.gz
    filename = f"{symbol}-L2orderbook-400lv-{date_hyphen}.tar.gz"
    url = f"{BASE_URL}/{date_compact}/{filename}"
    
    target_dir = os.path.join(DOWNLOAD_DIR, symbol)
    ensure_dir(target_dir)
    target_path = os.path.join(target_dir, filename)
    
    if os.path.exists(target_path):
        # Check size > 0
        if os.path.getsize(target_path) > 1024:
            print(f"[SKIP] {filename} exists.")
            return

    print(f"[DOWNLOADING] {filename} ...")
    try:
        start_t = time.time()
        with requests.get(url, stream=True) as r:
            if r.status_code == 404:
                print(f"[MISSING] {filename} not found on server.")
                return
            r.raise_for_status()
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192): 
                    f.write(chunk)
        duration = time.time() - start_t
        print(f"[SUCCESS] {filename} saved in {duration:.1f}s")
    except Exception as e:
        print(f"[ERROR] Failed to download {filename}: {e}")
        # Cleanup partial file
        if os.path.exists(target_path):
            os.remove(target_path)

def main():
    parser = argparse.ArgumentParser(description="Download OKX L2 Data")
    parser.add_argument("--start", type=str, required=True, help="Start Date YYYY-MM-DD")
    parser.add_argument("--end", type=str, required=True, help="End Date YYYY-MM-DD")
    parser.add_argument("--symbols", type=str, default="BTC,ETH,SOL", help="Comma-separated symbols")
    
    args = parser.parse_args()
    
    start_date = args.start
    end_date = args.end
    
    # Map shorthand symbols to full OKX tickers
    sym_map = {
        "BTC": "BTC-USDT-SWAP",
        "ETH": "ETH-USDT-SWAP",
        "SOL": "SOL-USDT-SWAP"
    }
    
    symbols_to_download = []
    for s in args.symbols.split(","):
        s = s.strip().upper()
        if s in sym_map:
            symbols_to_download.append(sym_map[s])
        elif "-SWAP" in s:
             symbols_to_download.append(s)
        else:
            print(f"Unknown symbol shorthand: {s}")

    print(f"Starting Download Job: {start_date} to {end_date}")
    print(f"Symbols: {symbols_to_download}")
    ensure_dir(DOWNLOAD_DIR)
    
    tasks = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        for d in generate_dates(start_date, end_date):
            for sym in symbols_to_download:
                tasks.append(executor.submit(download_file, sym, d))
    
    print("Download Job Complete.")

if __name__ == "__main__":
    main()
