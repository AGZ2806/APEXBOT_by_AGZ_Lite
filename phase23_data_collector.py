# phase23_data_collector.py
# Phase-23 Generic Multi-Symbol Data Collector
# Supports dynamic symbol lists and batched frame generation.

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np
import websockets

try:
    import ccxt.async_support as ccxt
except Exception as e:
    ccxt = None
    _ccxt_import_error = e


@dataclass
class Frame:
    ts: float
    # data[symbol] = { "bids": [], "asks": [], "mid": float, "trades": [], "mark": {}, "htf": {} }
    data: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    
    # Legacy/Convenience accessors can be added if needed, but we aim for generic.
    # We will keep a "context" field for global market state if needed.
    context: Dict[str, Any] = field(default_factory=dict)


class OrderBook:
    def __init__(self):
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}

    def apply_snapshot(self, bids, asks):
        self.bids.clear(); self.asks.clear()
        for p, q in bids:
            self.bids[float(p)] = float(q)
        for p, q in asks:
            self.asks[float(p)] = float(q)

    def apply_delta(self, bids, asks):
        for p, q in bids:
            pf = float(p); qf = float(q)
            if qf <= 0: self.bids.pop(pf, None)
            else: self.bids[pf] = qf
        for p, q in asks:
            pf = float(p); qf = float(q)
            if qf <= 0: self.asks.pop(pf, None)
            else: self.asks[pf] = qf

    def top_levels(self, n):
        bids = sorted(self.bids.items(), key=lambda x: -x[0])[:n]
        asks = sorted(self.asks.items(), key=lambda x: x[0])[:n]
        return bids, asks

    def mid(self):
        if not self.bids or not self.asks:
            return 0.0
        return 0.5 * (max(self.bids.keys()) + min(self.asks.keys()))


class Phase23DataCollector:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbols: List[str],
        context_symbols: List[str] = [],
        testnet: bool = False,
        leverage: int = 10,
        frame_interval_ms: int = 100,
        depth_levels: int = 20,
        ws_seed_snapshot: bool = True,
    ):
        if ccxt is None:
            raise ImportError(f"ccxt.async_support not available: {_ccxt_import_error}")

        self.api_key = api_key
        self.api_secret = api_secret
        # Combine all symbols we need to track
        self.trading_symbols = symbols
        self.context_symbols = context_symbols
        self.all_symbols = list(set(symbols + context_symbols))
        
        self.testnet = testnet
        self.leverage = leverage
        self.frame_interval = frame_interval_ms / 1000.0
        self.depth_levels = depth_levels
        self.ws_seed_snapshot = ws_seed_snapshot

        # Generic State Storage
        self.books: Dict[str, OrderBook] = {s: OrderBook() for s in self.all_symbols}
        self.marks: Dict[str, Dict[str, Any]] = {s: {} for s in self.all_symbols}
        self.trades: Dict[str, List[Dict[str, Any]]] = {s: [] for s in self.all_symbols}
        self.htf_candles: Dict[str, Dict[str, Any]] = {s: {} for s in self.all_symbols}
        
        # Sequence Tracking (Critical Fix)
        # last_u[symbol] = last_final_update_id
        self.last_u: Dict[str, int] = {s: 0 for s in self.all_symbols}


        # Map stream names to symbols for fast lookup
        # e.g. "ethusdt" -> "ETH/USDT:USDT"
        self.stream_map = {}
        for s in self.all_symbols:
            # "ETH/USDT:USDT" -> "ethusdt"
            base = s.split('/')[0].lower()
            quote = s.split('/')[1].split(':')[0].lower()
            stream_name = f"{base}{quote}"
            self.stream_map[stream_name] = s
            self.stream_map[stream_name.upper()] = s # Handle uppercase too just in case

        self.queue = asyncio.Queue(maxsize=5000)
        self.frames: List[Frame] = []
        self._task = None
        self._running = False
        self._last_frame_ts = 0.0
        self._last_msg_ts = 0.0
        self.last_depth_log = 0.0 # Debug for L2 verification

        self.first_frame_event = asyncio.Event()
        self.exchange = self._make_exchange()
        
        self._htf_task = None

    def _make_exchange(self):
        ex = ccxt.binanceusdm({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future", "broker": {"future": "x-XSY2ZGS8", "spot": "x-XSY2ZGS8", "swap": "x-XSY2ZGS8", "linear": "x-XSY2ZGS8", "delivery": "x-XSY2ZGS8"}},
        })
        if self.testnet:
            ex.set_sandbox_mode(True)
        return ex

    async def _configure_exchange(self):
        for sym in self.trading_symbols: # Only set leverage for trading symbols
            try:
                await self.exchange.set_leverage(self.leverage, sym)
            except Exception as e:
                print(f"Leverage set failed {sym}: {e}")

    async def initialize_books(self):
        for sym in self.all_symbols:
            try:
                ob = await self.exchange.fetch_order_book(sym, limit=self.depth_levels)
                bids = [[str(p), str(q)] for p, q in ob.get("bids", [])]
                asks = [[str(p), str(q)] for p, q in ob.get("asks", [])]
                self.books[sym].apply_snapshot(bids, asks)
                
                if "lastUpdateId" in ob:
                    self.last_u[sym] = ob["lastUpdateId"]
                    
                print(f"[{ts()}] Initial snapshot loaded for {sym} (last_u={self.last_u[sym]})")
            except Exception as e:
                print(f"Initial snapshot failed {sym}: {e}")

    def _ws_base(self):
        return "wss://stream.binancefuture.com/stream?streams=" if self.testnet \
               else "wss://fstream.binance.com/stream?streams="

    def _stream_url(self):
        streams = []
        for s in self.all_symbols:
            base = s.split('/')[0].lower()
            quote = s.split('/')[1].split(':')[0].lower()
            ss = f"{base}{quote}"
            streams += [f"{ss}@depth20@100ms", f"{ss}@aggTrade", f"{ss}@markPrice@1s"]
        return self._ws_base() + "/".join(streams)

    async def connect_ws(self):
        await self.start()

    async def start(self):
        if self._running:
            return
        self._running = True
        await self._configure_exchange()
        if self.ws_seed_snapshot:
            await self.initialize_books()

        print(f"[{ts()}] Phase23DataCollector connecting WS...")
        self._task = asyncio.create_task(self._run_ws())
        self._htf_task = asyncio.create_task(self._run_htf_loop())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try: await self._task
            except: pass
            self._task = None
            
        if self._htf_task:
            self._htf_task.cancel()
            try: await self._htf_task
            except: pass
            self._htf_task = None

        if self.exchange:
            try: await self.exchange.close()
            except: pass

    async def next_frame(self, timeout=1.0):
        try:
            return await asyncio.wait_for(self.queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    def get_recent_frames(self, n: int):
        return self.frames[-n:] if len(self.frames) >= n else None

    def get_mark_price(self, symbol: str):
        if symbol in self.marks and self.marks[symbol].get("mark_price", 0.0) > 0:
            return float(self.marks[symbol]["mark_price"])
        if symbol in self.books:
            return float(self.books[symbol].mid())
        return 0.0

    # --- account helpers for reward ---
    async def fetch_balance(self):
        try:
            return await self.exchange.fetch_balance()
        except Exception:
            return None

    async def fetch_position(self, symbol):
        try:
            positions = await self.exchange.fetch_positions([symbol])
            return positions[0] if positions else None
        except Exception:
            return None

    async def fetch_htf_candles(self):
        # Fetch 1m and 5m klines for ALL symbols
        # To avoid rate limits, we might need to stagger or use gather with semaphore
        # For now, serial fetch is safer for rate limits, or just fetch primary symbols?
        # Let's fetch for all trading symbols + context.
        
        for sym in self.all_symbols:
            try:
                # 1m
                k1 = await self.exchange.fetch_ohlcv(sym, timeframe="1m", limit=2)
                if k1 and len(k1) >= 1:
                    c = k1[-2] if len(k1) > 1 else k1[-1]
                    self.htf_candles[sym]["1m"] = {
                        "close": float(c[4]), "vol": float(c[5]), "high": float(c[2]), "low": float(c[3])
                    }
                
                # 5m
                k5 = await self.exchange.fetch_ohlcv(sym, timeframe="5m", limit=2)
                if k5 and len(k5) >= 1:
                    c = k5[-2] if len(k5) > 1 else k5[-1]
                    self.htf_candles[sym]["5m"] = {
                        "close": float(c[4]), "vol": float(c[5]), "high": float(c[2]), "low": float(c[3])
                    }

                # 1h (New Phase 2)
                k1h = await self.exchange.fetch_ohlcv(sym, timeframe="1h", limit=2)
                if k1h and len(k1h) >= 1:
                    c = k1h[-2] if len(k1h) > 1 else k1h[-1]
                    self.htf_candles[sym]["1h"] = {
                        "close": float(c[4]), "open": float(c[1]), "vol": float(c[5]), "high": float(c[2]), "low": float(c[3])
                    }

                # 4h (New Phase 2)
                k4h = await self.exchange.fetch_ohlcv(sym, timeframe="4h", limit=2)
                if k4h and len(k4h) >= 1:
                    c = k4h[-2] if len(k4h) > 1 else k4h[-1]
                    self.htf_candles[sym]["4h"] = {
                        "close": float(c[4]), "open": float(c[1]), "vol": float(c[5]), "high": float(c[2]), "low": float(c[3])
                    }
            except Exception as e:
                print(f"[{ts()}] HTF fetch failed for {sym}: {e}")
                # Don't spam logs too much
                await asyncio.sleep(0.1)

    async def _run_htf_loop(self):
        while self._running:
            await self.fetch_htf_candles()
            await asyncio.sleep(30) # 30s poll

    async def _run_ws(self):
        url = self._stream_url()
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=60) as ws:
                    print(f"[{ts()}] WS connected.")
                    self._last_msg_ts = time.time()
                    conn_start_time = time.time()

                    while self._running:
                        msg = await ws.recv()
                        self._last_msg_ts = time.time()
                        data = json.loads(msg)
                        payload = data.get("data", {})
                        et = payload.get("e", "")

                        if et == "depthUpdate":
                            self._handle_depth(payload)
                        elif et == "aggTrade":
                            self._handle_trade(payload)
                        elif et == "markPriceUpdate":
                            if not hasattr(self, '_logged_mark'):
                                print(f"[{ts()}] First mark event: {payload}")
                                self._logged_mark = True
                            self._handle_mark(payload)

                        now_ts = time.time()
                        if now_ts - self._last_frame_ts >= self.frame_interval:
                            self._last_frame_ts = now_ts
                            frame = self._build_frame(now_ts)
                            if frame is not None:
                                if not self.first_frame_event.is_set():
                                    self.first_frame_event.set()

                                if not self.queue.full():
                                    await self.queue.put(frame)

                                self.frames.append(frame)
                                if len(self.frames) > 5000:
                                    self.frames = self.frames[-4000:]

                        if time.time() - self._last_msg_ts > 10.0:
                            raise RuntimeError("WS watchdog timeout")
                            
                        # Proactive Reconnect (23h)
                        if time.time() - conn_start_time > 82800: # 23 hours
                             print(f"[{ts()}] Proactive 23h Reconnect...")
                             raise RuntimeError("Scheduled 23h Reconnect")

            except Exception as e:
                print(f"[{ts()}] WS error/reconnect: {e}")
                await asyncio.sleep(2.0)

    def _get_symbol_from_stream(self, stream_symbol: str) -> Optional[str]:
        # stream_symbol is like "ETHUSDT"
        return self.stream_map.get(stream_symbol)

    async def _reset_symbol(self, sym: str):
        # Smart Log: Only log once every 5 minutes per symbol to avoid spam
        now = time.time()
        last_log = getattr(self, "_last_reset_log", {}).get(sym, 0)
        
        if now - last_log > 300:
            print(f"[{ts()}] [CRITICAL] Resetting book for {sym} due to sequence gap/error. (Suppressing further logs for 5m)")
            if not hasattr(self, "_last_reset_log"): self._last_reset_log = {}
            self._last_reset_log[sym] = now
            
        try:
            ob = await self.exchange.fetch_order_book(sym, limit=self.depth_levels)
            bids = [[str(p), str(q)] for p, q in ob.get("bids", [])]
            asks = [[str(p), str(q)] for p, q in ob.get("asks", [])]
            self.books[sym].apply_snapshot(bids, asks)
            
            # Reset sequence ID from snapshot if available (Binance REST usually returns 'lastUpdateId')
            # But WS stream continuity requires us to know the 'u' of the snapshot.
            # REST API 'lastUpdateId' corresponds to the final update ID of the snapshot.
            if "lastUpdateId" in ob:
                self.last_u[sym] = ob["lastUpdateId"]
                print(f"[{ts()}] Reset complete. New last_u={self.last_u[sym]}")
            else:
                # Fallback: Reset to 0 and wait for next stream event to re-sync?
                # If we don't know last_u, we can't validate the next event.
                # We might need to drop events until U <= last_u + 1 <= u is satisfied again?
                # Actually, if we reset, we should treat the next event as a "first event" logic?
                # Let's set last_u to the snapshot's ID.
                self.last_u[sym] = 0 
                
        except Exception as e:
            print(f"[{ts()}] Reset failed for {sym}: {e}")

    def _handle_depth(self, p):
        s = p.get("s", "")
        sym = self._get_symbol_from_stream(s)
        if not sym: return
        
        # Phase 23 Fix: @depth20 stream sends SNAPSHOTS, not Deltas.
        # It may be wrapped in "depthUpdate" event on Futures, but it is a snapshot.
        # We must use apply_snapshot, and we cannot enforce U > last_u + 1 because it's subsampled (100ms).
        
        u = p.get("u") # Final Update ID
        if u is None:
            # Fallback for some headers
            u = p.get("lastUpdateId")
            
        if u is None: return

        # Sequence Check: Just ensure we are moving forward
        last_u = self.last_u.get(sym, 0)
        
        if u <= last_u:
            # Stale packet
            return
            
        self.last_u[sym] = u
        
        bids = p.get("b", [])
        asks = p.get("a", [])
        
        # CRITICAL FIX: Use apply_snapshot for @depth20
        # apply_delta is ONLY for @depth (Diff) stream.
        self.books[sym].apply_snapshot(bids, asks)

    def _handle_trade(self, p):
        s = p.get("s", "")
        sym = self._get_symbol_from_stream(s)
        if not sym: return

        trade = {
            "price": float(p.get("p", 0.0)),
            "qty": float(p.get("q", 0.0)),
            "is_buyer_maker": bool(p.get("m", False)),
            "ts": float(p.get("T", 0)) / 1000.0,
        }
        self.trades[sym].append(trade)
        # Keep last 300 trades
        if len(self.trades[sym]) > 400:
            self.trades[sym] = self.trades[sym][-300:]

    def _handle_mark(self, p):
        s = p.get("s", "")
        sym = self._get_symbol_from_stream(s)
        if not sym: return

        mark = {
            "mark_price": float(p.get("p", 0.0)),
            "index_price": float(p.get("i", 0.0)),
            "funding_rate": float(p.get("r", 0.0)),
            "event_ts": float(p.get("E", 0)) / 1000.0,
            "next_funding_ts": float(p.get("T", 0)) / 1000.0,
        }
        self.marks[sym] = mark

    def _book_dict(self, ob: OrderBook):
        bids, asks = ob.top_levels(self.depth_levels)
        return {"bids": bids, "asks": asks, "mid": ob.mid()}

    def _build_frame(self, ts_now):
        # Check if we have valid mids for all trading symbols
        for sym in self.trading_symbols:
            if self.books[sym].mid() <= 0:
                return None
        
        frame_data = {}
        
        for sym in self.all_symbols:
            book_data = self._book_dict(self.books[sym])
            
            # Inject mark data
            if self.marks[sym]:
                book_data.update(self.marks[sym])
                
            # Trades
            tr = self.trades[sym]
            self.trades[sym] = [] # Clear trades after consuming
            
            frame_data[sym] = {
                "book": book_data,
                "trades": tr,
                "mark": self.marks[sym].copy(),
                "htf": self.htf_candles[sym].copy()
            }

            # Periodic L2 Verification Log (Every 60s)
            if time.time() - self.last_depth_log > 60:
                 # Log just one symbol to prove data flow
                 if sym == self.trading_symbols[0]:
                     b_len = len(self.books[sym].bids)
                     a_len = len(self.books[sym].asks)
                     print(f"[{ts()}] [L2 CHECK] {sym} Depth: Bids={b_len} Asks={a_len} (Should be ~20)")
                     self.last_depth_log = time.time()
            
        return Frame(
            ts=ts_now,
            data=frame_data,
            context={} # Can populate with global stats if needed
        )


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
