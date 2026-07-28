import os
import sys
import csv
import asyncio
import time
import math
import json
import numpy as np
import signal as signal_mod
from typing import Dict, Optional, List
from collections import deque
from dataclasses import dataclass

from phase23_lib import load_cfg, ts_iso, BotConfig, ExchangeManager

class GracefulKiller:
    kill_now = False
    def __init__(self):
        signal_mod.signal(signal_mod.SIGINT, self.exit_gracefully)
        signal_mod.signal(signal_mod.SIGTERM, self.exit_gracefully)
    def exit_gracefully(self, signum, frame):
        self.kill_now = True

class PairsTrader:
    """
    Statistical Arbitrage (Pairs Trading) Bot.
    Trades the spread between two correlated assets (e.g., BTC and ETH).
    
    Logic:
    - Tracks the logarithmic ratio: ln(Price_A) - ln(Price_B)
    - Computes 24-hour rolling Mean and Standard Deviation.
    - Enters Long Spread (Long A, Short B) when Z-Score < -2.5.
    - Enters Short Spread (Short A, Long B) when Z-Score > +2.5.
    - Exits when Z-Score crosses 0.
    """

    # ── Per-Pair Profiles (Hi-Fi Sweep Optimized) ─────────────
    PAIR_PROFILES = {
        ("BTC/USDT:USDT", "ETH/USDT:USDT"): {
            "Z_ENTRY_THRESHOLD": 4.5,
            "OVERSHOOT_PCT": 0.008,     # Legacy fallback (used if DYNAMIC_Z_TARGET is None)
            "DYNAMIC_Z_TARGET": 0.0,    # Exit when anchored Z crosses back to 0.0 (mean reversion)
            "HEDGE_RATIO": 0.5,         # ETH-Biased (2:1 exposure on ETH over BTC)
            "MAX_HOLD_SEC": 24 * 3600,
            "STOP_LOSS_PCT": 0.125,     # 12.5% of NOTIONAL_PER_LEG (equivalent to -$20 on $160 leg)
            "CORR_MIN": 0.5,        # Block entry if cross-corr < 0.5
            "Z_DECAY_ENABLED": True,    # Relax TP target on aging trades
        },
        ("ETH/USDT:USDT", "SOL/USDT:USDT"): {
            "Z_ENTRY_THRESHOLD": 3.0,
            "OVERSHOOT_PCT": 0.008,
            "DYNAMIC_Z_TARGET": 0.0,    # Exit when anchored Z crosses back to 0.0
            "HEDGE_RATIO": 0.5,         # 0.5 or 2.0 dynamic hedge based on EMA regime
            "MAX_HOLD_SEC": 12 * 3600,
            "STOP_LOSS_PCT": 0.25,  # 25% of NOTIONAL_PER_LEG (equivalent to -$40 on $160 leg)
            "CORR_MIN": 0.5,        # Block entry if cross-corr < 0.5
        },
        ("BTC/USDT:USDT", "SOL/USDT:USDT"): {
            "Z_ENTRY_THRESHOLD": 3.0,
            "OVERSHOOT_PCT": 0.006,
            "DYNAMIC_Z_TARGET": 0.0,    # Exit when anchored Z crosses back to 0.0
            "HEDGE_RATIO": 0.5,
            "MAX_HOLD_SEC": 12 * 3600,
            "CORR_MIN": 0.5,        # Block entry if cross-corr < 0.5
        },
    }

    # ── Shared Defaults (overridable via YAML pairs_trading section) ──
    Z_EXIT_THRESHOLD = 0.0        # Exit threshold (Z-Score crosses 0)
    SAMPLE_INTERVAL_SEC = 300
    HISTORY_LENGTH = 288          # 288 * 5 mins = 24 hours
    NOTIONAL_PER_LEG = 160.0      # Default $160 Notional per leg (ensures BTC fills 0.002+)
    MIN_SPREAD_PCT = 0.0035       # 0.35% minimum required spread divergence
    STOP_LOSS_USD = -24.0         # Default hard stop loss (scales with notional)
    EMERGENCY_Z_STOP = 10.0       # Structural break stop loss
    EMERGENCY_COOLDOWN_SEC = 14400 # 4 hour timeout after emergency stop (Protects against knife catching)
    # ───────────────────────────────────────────────────────────

    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.exchange_manager = ExchangeManager(cfg)
        
        if len(cfg.symbols) < 2:
            raise ValueError("PairsTrader requires at least two symbols in the configuration.")
            
        self.sym_a = cfg.symbols[0]
        self.sym_b = cfg.symbols[1]
        
        # Load per-pair profile (try both orderings)
        pair_key = (self.sym_a, self.sym_b)
        pair_key_rev = (self.sym_b, self.sym_a)
        profile = self.PAIR_PROFILES.get(pair_key) or self.PAIR_PROFILES.get(pair_key_rev)
        
        # Read sizing from YAML pairs_trading section
        pairs_cfg = getattr(cfg, 'raw_config', {}).get('pairs_trading', {})
        
        if profile:
            # Load from profile, but allow explicit YAML overrides from user_settings.yaml
            self.Z_ENTRY_THRESHOLD = float(pairs_cfg.get("z_entry_threshold", profile["Z_ENTRY_THRESHOLD"]))
            self.OVERSHOOT_PCT = profile["OVERSHOOT_PCT"]
            self.DYNAMIC_Z_TARGET = profile.get("DYNAMIC_Z_TARGET", None)
            self.BASE_HEDGE_RATIO = float(pairs_cfg.get("hedge_ratio", profile["HEDGE_RATIO"]))
            self.HEDGE_RATIO = self.BASE_HEDGE_RATIO
            self.MAX_HOLD_SEC = float(pairs_cfg.get("max_hold_sec", profile["MAX_HOLD_SEC"]))
            self.CORR_MIN = profile.get("CORR_MIN", 0.0)
            self.Z_DECAY_ENABLED = profile.get("Z_DECAY_ENABLED", True)
        else:
            # Fallback defaults (BTC/ETH-like)
            self.Z_ENTRY_THRESHOLD = float(pairs_cfg.get("z_entry_threshold", 3.5))
            self.OVERSHOOT_PCT = 0.008
            self.DYNAMIC_Z_TARGET = None
            self.BASE_HEDGE_RATIO = float(pairs_cfg.get("hedge_ratio", 1.0))
            self.HEDGE_RATIO = self.BASE_HEDGE_RATIO
            self.MAX_HOLD_SEC = float(pairs_cfg.get("max_hold_sec", 24 * 3600))
            self.CORR_MIN = 0.0
            self.Z_DECAY_ENABLED = True
            print(f"[PAIRS] WARNING: No profile for {self.sym_a} vs {self.sym_b}. Using defaults.")
        
        self.NOTIONAL_PER_LEG = float(pairs_cfg.get('notional_per_leg', self.NOTIONAL_PER_LEG))
        
        # Determine Stop Loss
        if 'stop_loss_usd' in pairs_cfg:
            self.STOP_LOSS_USD = float(pairs_cfg['stop_loss_usd'])
        elif 'stop_loss_pct' in pairs_cfg:
            self.STOP_LOSS_USD = -(self.NOTIONAL_PER_LEG * float(pairs_cfg['stop_loss_pct']))
        elif profile and 'STOP_LOSS_PCT' in profile:
            self.STOP_LOSS_USD = -(self.NOTIONAL_PER_LEG * float(profile['STOP_LOSS_PCT']))
        else:
            self.STOP_LOSS_USD = -(self.NOTIONAL_PER_LEG * 0.15)
            
        print(f"[PAIRS] Loaded profile for {self.sym_a} vs {self.sym_b} successfully.")
        
        self.ratio_history = deque(maxlen=self.HISTORY_LENGTH)
        self.last_sample_ts = 0.0
        
        self.open_position = False
        self.closing_position = False
        self.exit_reason = ""
        self.position_direction = 0  # 1 for Long Spread, -1 for Short Spread
        self.entry_z = 0.0
        self.entry_ts = 0.0
        self.entry_mark_a = 0.0
        self.entry_mark_b = 0.0
        self.entry_mean_ratio = 0.0
        self.entry_std_ratio = 0.0
        self.close_start_ts = 0.0
        self._last_position_check_ts = 0.0   # For live reconciliation (manual-close detection)
        
        self.btc_ema = 0.0
        self.ema_state_file = os.path.join(os.getcwd(), "data", "ema_state.json")
        self._load_ema_state()
        
        # Correlation gate state
        self._ret_a_buf = deque(maxlen=3600)   # ~1 hour of 1Hz ticks
        self._ret_b_buf = deque(maxlen=3600)
        self._prev_mark_a = 0.0
        self._prev_mark_b = 0.0
        self._last_corr = 0.0
        self._corr_tick = 0
        
        self.state: dict = {}
        
        # Trade log
        self.trade_log_file = "data/trades_pairs.csv"
        self._init_trade_log()
        
        # SHM for reading market data from scribe
        try:
            from phase23_shm import SharedMemoryManager
            self.shm_mgr = SharedMemoryManager(is_writer=False)
        except Exception as e:
            print(f"[{ts_iso()}] Failed to init market SHM reader: {e}")
            self.shm_mgr = None

        # SHM for writing Dashboard Signals
        try:
            from phase23_shm import SharedMemoryManager, SIGNALS_SHM_NAME, SIGNALS_SHM_SIZE
            self.shm_writer = SharedMemoryManager(is_writer=True, name=SIGNALS_SHM_NAME, size=SIGNALS_SHM_SIZE)
            print(f"[{ts_iso()}] Signals SHM connected")
        except Exception as e:
            print(f"[{ts_iso()}] Failed to init Signals SHM: {e}")
            self.shm_writer = None

    def _init_trade_log(self):
        os.makedirs("data", exist_ok=True)
        if not os.path.exists(self.trade_log_file):
            with open(self.trade_log_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["timestamp", "pair", "action", "sym_a_price", "sym_b_price", "z_score", "pnl"])

    def log_trade(self, action: str, price_a: float, price_b: float, z_score: float, pnl: float = 0.0):
        pair_str = f"{self.sym_a} / {self.sym_b}"
        with open(self.trade_log_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([ts_iso(), pair_str, action, f"{price_a:.4f}", f"{price_b:.4f}", f"{z_score:.4f}", f"{pnl:.6f}"])

    # ── Entry State Persistence ──────────────────────────────────────────
    # Saves/loads the frozen entry stats (mean, std, prices, ts) to disk
    # so that anchored Z-score survives bot restarts.
    ENTRY_STATE_FILE = "data/entry_state.json"

    def _save_entry_state(self):
        state = {
            "entry_mean_ratio": self.entry_mean_ratio,
            "entry_std_ratio": self.entry_std_ratio,
            "entry_ts": self.entry_ts,
            "entry_mark_a": self.entry_mark_a,
            "entry_mark_b": self.entry_mark_b,
            "entry_z": self.entry_z,
            "position_direction": self.position_direction,
            "entry_hedge_ratio": getattr(self, "HEDGE_RATIO", 1.0),
        }
        try:
            with open(self.ENTRY_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception as e:
            print(f"[{ts_iso()}] [WARN] Failed to save entry state: {e}")

    def _load_entry_state(self) -> bool:
        """Load persisted entry state. Returns True if loaded successfully."""
        try:
            if os.path.exists(self.ENTRY_STATE_FILE):
                with open(self.ENTRY_STATE_FILE, "r", encoding="utf-8") as f:
                    state = json.load(f)
                self.entry_mean_ratio = float(state["entry_mean_ratio"])
                self.entry_std_ratio = float(state["entry_std_ratio"])
                self.entry_ts = float(state["entry_ts"])
                self.entry_mark_a = float(state["entry_mark_a"])
                self.entry_mark_b = float(state["entry_mark_b"])
                self.entry_z = float(state.get("entry_z", 0.0))
                self.position_direction = int(state.get("position_direction", self.position_direction))
                if "entry_hedge_ratio" in state:
                    self.HEDGE_RATIO = float(state["entry_hedge_ratio"])
                print(f"[{ts_iso()}] [RECONCILE] Restored entry state from disk: "
                      f"mean={self.entry_mean_ratio:.6f}, std={self.entry_std_ratio:.6f}, "
                      f"entryZ={self.entry_z:+.2f}")
                return True
        except Exception as e:
            print(f"[{ts_iso()}] [WARN] Failed to load entry state: {e}")
        return False

    def _clear_entry_state(self):
        if os.path.exists(self.ENTRY_STATE_FILE):
            try:
                os.remove(self.ENTRY_STATE_FILE)
            except Exception:
                pass

    def _load_ema_state(self):
        try:
            if os.path.exists(self.ema_state_file):
                with open(self.ema_state_file, "r") as f:
                    state = json.load(f)
                    self.btc_ema = float(state.get("btc_ema", 0.0))
        except Exception:
            pass

    def _save_ema_state(self):
        try:
            with open(self.ema_state_file, "w") as f:
                json.dump({"btc_ema": self.btc_ema}, f)
        except Exception:
            pass

    async def _bootstrap_history(self, lookback_hours: int = 24):
        """Rebuild ratio history from scribe JSONL files on startup, fallback to API."""
        import json, glob
        history_dir = os.path.join(os.getcwd(), "data", "history")
        
        last_sample = 0.0
        samples_added = 0
        
        if os.path.isdir(history_dir):
            files = sorted(glob.glob(os.path.join(history_dir, "scribe_*.jsonl")))
            if files:
                files = files[-lookback_hours:]
                print(f"[{ts_iso()}] [BOOTSTRAP] Parsing up to {lookback_hours} hours of local scribe history data...")
                for filepath in files:
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            for line in f:
                                if not line.strip(): continue
                                try:
                                    record = json.loads(line)
                                except json.JSONDecodeError:
                                    continue
                                
                                ts = record.get("ts", 0)
                                data = record.get("data", {})
                                
                                if self.sym_a in data and self.sym_b in data:
                                    mark_a = float(data[self.sym_a].get("mark", 0.0))
                                    if mark_a <= 0:
                                        bids_a = data[self.sym_a].get("bids", [])
                                        asks_a = data[self.sym_a].get("asks", [])
                                        if bids_a and asks_a: mark_a = (float(bids_a[0][0]) + float(asks_a[0][0])) / 2.0
                                        
                                    mark_b = float(data[self.sym_b].get("mark", 0.0))
                                    if mark_b <= 0:
                                        bids_b = data[self.sym_b].get("bids", [])
                                        asks_b = data[self.sym_b].get("asks", [])
                                        if bids_b and asks_b: mark_b = (float(bids_b[0][0]) + float(asks_b[0][0])) / 2.0
                                    
                                    if mark_a > 0 and mark_b > 0:
                                        if ts - last_sample >= self.SAMPLE_INTERVAL_SEC:
                                            # Gap detection during bootstrap
                                            if last_sample > 0 and ts - last_sample > 3600:
                                                self.ratio_history.clear()
                                                samples_added = 0
                                            ratio = math.log(mark_a) - math.log(mark_b)
                                            self.ratio_history.append(ratio)
                                            last_sample = ts
                                            samples_added += 1
                    except Exception as e:
                        print(f"[{ts_iso()}] [BOOTSTRAP] Skipped file {os.path.basename(filepath)}: {e}")
        
        # If the buffer is not full, we fetch via Binance API
        if len(self.ratio_history) < self.HISTORY_LENGTH:
            print(f"[{ts_iso()}] [BOOTSTRAP] Missing history ({len(self.ratio_history)}/{self.HISTORY_LENGTH}). Fetching via Binance API...")
            try:
                adapter_a = self.exchange_manager.get_adapter(self.sym_a)
                adapter_b = self.exchange_manager.get_adapter(self.sym_b)
                limit = self.HISTORY_LENGTH
                klines_a = await adapter_a.exchange.fetch_ohlcv(self.sym_a, timeframe='5m', limit=limit)
                klines_b = await adapter_b.exchange.fetch_ohlcv(self.sym_b, timeframe='5m', limit=limit)
                
                # Clear whatever partial history we have to ensure clean alignment
                self.ratio_history.clear()
                
                # We align the last `limit` candles
                # CCXT returns klines chronologically (oldest to newest)
                # kline index 4 is the Close price
                
                # Map timestamps for exact alignment (close price)
                dict_b = { int(k[0]/1000): float(k[4]) for k in klines_b }
                
                samples_added = 0
                for k in klines_a:
                    ts_sec = int(k[0]/1000)
                    close_a = float(k[4])
                    close_b = dict_b.get(ts_sec)
                    if close_b:
                        ratio = math.log(close_a) - math.log(close_b)
                        self.ratio_history.append(ratio)
                        last_sample = float(ts_sec)
                        samples_added += 1
                        
            except Exception as e:
                print(f"[{ts_iso()}] [BOOTSTRAP-API] Error fetching klines: {e}")
                
        if last_sample > 0:
            self.last_sample_ts = last_sample
            
        print(f"[{ts_iso()}] [BOOTSTRAP] Successfully loaded {samples_added} ratio samples from history")

    def _estimate_pnl(self, mark_a: float, mark_b: float, taker_exit: bool = False) -> float:
        """Estimate net PnL of current spread position, including round-trip fees."""
        if not self.open_position or self.entry_mark_a <= 0 or self.entry_mark_b <= 0:
            return 0.0
        if self.HEDGE_RATIO < 1.0:
            notional_a = self.NOTIONAL_PER_LEG
            notional_b = self.NOTIONAL_PER_LEG / self.HEDGE_RATIO
        else:
            notional_a = self.NOTIONAL_PER_LEG * self.HEDGE_RATIO
            notional_b = self.NOTIONAL_PER_LEG
            
        qty_a = notional_a / self.entry_mark_a
        qty_b = notional_b / self.entry_mark_b
        
        if self.position_direction > 0:
            pnl_a = (mark_a - self.entry_mark_a) * qty_a
            pnl_b = (self.entry_mark_b - mark_b) * qty_b
        else:
            pnl_a = (self.entry_mark_a - mark_a) * qty_a
            pnl_b = (mark_b - self.entry_mark_b) * qty_b
        gross_pnl = pnl_a + pnl_b
        
        # Fee estimation: entry assumed maker, exit may be taker
        maker_fee = 0.0002
        taker_fee = 0.0004
        entry_fees = (notional_a + notional_b) * maker_fee
        exit_fee_rate = taker_fee if taker_exit else maker_fee
        exit_fees = (notional_a + notional_b) * exit_fee_rate
        return gross_pnl - entry_fees - exit_fees


    @staticmethod
    def _parse_position_amt(pos) -> float:
        """Extract signed position amount from exchange response."""
        if not pos:
            return 0.0
        raw = float(pos.get("positionAmt", pos.get("contracts", 0.0)) or 0.0)
        if str(pos.get("side", "")).lower() == "short" and raw > 0:
            raw = -raw
        return raw


    def _get_best_prices(self, sym, fallback_mark):
        if not self.state:
            return fallback_mark, fallback_mark
        book = self.state.get("books", {}).get(sym, {})
        if not book:
            return fallback_mark, fallback_mark
        best_bid = float(book.get("bids", [[fallback_mark]])[0][0])
        best_ask = float(book.get("asks", [[fallback_mark]])[0][0])
        return best_bid, best_ask

    async def _execute_maker_chaser(self, sym_a: str, sym_b: str, side_a: str, side_b: str, qty_a: float, qty_b: float, is_exit: bool, force_market: bool = False):
        import time
        broker_a = self.exchange_manager.get_adapter(sym_a)
        broker_b = self.exchange_manager.get_adapter(sym_b)
        
        if force_market:
            tasks = []
            if qty_a > 0.0001: tasks.append(broker_a.place_order(side_a, getattr(self, 'current_mark_a', 0), qty_a, reduce_only=is_exit, order_type="market"))
            if qty_b > 0.0001: tasks.append(broker_b.place_order(side_b, getattr(self, 'current_mark_b', 0), qty_b, reduce_only=is_exit, order_type="market"))
            if tasks:
                try: await asyncio.gather(*tasks)
                except Exception as e: print(f"[{ts_iso()}] [EXEC] Fallback Market close failed: {e}")
            return True

        amt_a_filled = 0.0
        amt_b_filled = 0.0
        max_loops = 5
        
        for loop_idx in range(max_loops):
            shm_state = self.shm_mgr.read() if getattr(self, 'shm_mgr', None) else getattr(self, 'state', None)
            if shm_state: self.state = shm_state
            
            fallback_a = getattr(self, 'current_mark_a', getattr(self, 'entry_mark_a', 0))
            fallback_b = getattr(self, 'current_mark_b', getattr(self, 'entry_mark_b', 0))
            
            best_bid_a, best_ask_a = self._get_best_prices(sym_a, fallback_a)
            best_bid_b, best_ask_b = self._get_best_prices(sym_b, fallback_b)
            
            rem_a = qty_a - amt_a_filled
            rem_b = qty_b - amt_b_filled
            
            legged_in = False
            if (rem_a <= 0.0001) != (rem_b <= 0.0001):
                legged_in = True
                
                if not is_exit and hasattr(self, 'entry_mean_ratio') and self.entry_std_ratio > 0:
                    filled_price_a = getattr(self, 'entry_mark_a', 0) if rem_a <= 0.0001 else (best_bid_a if side_a == "buy" else best_ask_a)
                    filled_price_b = getattr(self, 'entry_mark_b', 0) if rem_b <= 0.0001 else (best_bid_b if side_b == "buy" else best_ask_b)
                    
                    if filled_price_a > 0 and filled_price_b > 0:
                        new_ratio = math.log(filled_price_a) - math.log(filled_price_b)
                        new_z = (new_ratio - self.entry_mean_ratio) / self.entry_std_ratio
                        
                        direction = 1 if side_a == "buy" else -1
                        if (direction > 0 and new_z < self.Z_ENTRY_THRESHOLD * 0.5) or (direction < 0 and new_z > -self.Z_ENTRY_THRESHOLD * 0.5):
                            print(f"[{ts_iso()}] [EXEC] Spread collapsed (Z={new_z:.2f}). Forcing Market Catch-Up.")
                            force_market = True
                            break
            
            price_a = best_bid_a if side_a == "buy" else best_ask_a
            price_b = best_bid_b if side_b == "buy" else best_ask_b
            
            tasks = []
            if rem_a > 0.0001: tasks.append(broker_a.place_order(side_a, price_a, rem_a, order_type="limit", post_only=True, reduce_only=is_exit))
            if rem_b > 0.0001: tasks.append(broker_b.place_order(side_b, price_b, rem_b, order_type="limit", post_only=True, reduce_only=is_exit))
            
            t_start = time.time()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            ping_ms = time.time() - t_start
            
            base_sleep = 0.1 if legged_in else 0.5
            sleep_time = max(0.1, base_sleep - ping_ms)
            await asyncio.sleep(sleep_time)
            
            await asyncio.gather(broker_a.cancel_all(), broker_b.cancel_all(), return_exceptions=True)
            
            try:
                pos_a = await broker_a.fetch_position()
                pos_b = await broker_b.fetch_position()
                if not is_exit:
                    amt_a_filled = abs(self._parse_position_amt(pos_a)) if pos_a else 0.0
                    amt_b_filled = abs(self._parse_position_amt(pos_b)) if pos_b else 0.0
                else:
                    current_amt_a = abs(self._parse_position_amt(pos_a)) if pos_a else 0.0
                    current_amt_b = abs(self._parse_position_amt(pos_b)) if pos_b else 0.0
                    amt_a_filled = max(0.0, qty_a - current_amt_a)
                    amt_b_filled = max(0.0, qty_b - current_amt_b)
            except Exception:
                pass
                
            if (qty_a - amt_a_filled) <= 0.0001 and (qty_b - amt_b_filled) <= 0.0001:
                break
                
        try:
            pos_a = await broker_a.fetch_position()
            pos_b = await broker_b.fetch_position()
            curr_a = abs(self._parse_position_amt(pos_a)) if pos_a else 0.0
            curr_b = abs(self._parse_position_amt(pos_b)) if pos_b else 0.0
            
            rem_a = qty_a - curr_a if not is_exit else curr_a
            rem_b = qty_b - curr_b if not is_exit else curr_b
            
            tasks = []
            if rem_a > getattr(broker_a, 'min_qty', 0.001):
                tasks.append(broker_a.place_order(side_a, getattr(self, 'current_mark_a', 0), rem_a, order_type="market", reduce_only=is_exit))
            if rem_b > getattr(broker_b, 'min_qty', 0.001):
                tasks.append(broker_b.place_order(side_b, getattr(self, 'current_mark_b', 0), rem_b, order_type="market", reduce_only=is_exit))
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        except Exception:
            pass
        return True

    async def _execute_spread(self, direction: int, mark_a: float, mark_b: float) -> bool:
        """
        Executes the paired trades using Maker Chaser (or Market for emergency).
        """
        broker_a = self.exchange_manager.get_adapter(self.sym_a)
        broker_b = self.exchange_manager.get_adapter(self.sym_b)
        
        try:
            pos_a = await broker_a.fetch_position()
            pos_b = await broker_b.fetch_position()
        except Exception as e:
            print(f"[{ts_iso()}] [EXEC] Failed to fetch position in close: {e}")
            return False
            
        amt_a = abs(self._parse_position_amt(pos_a)) if pos_a else 0.0
        amt_b = abs(self._parse_position_amt(pos_b)) if pos_b else 0.0
        
        side_a = "sell" if (pos_a and self._parse_position_amt(pos_a) > 0) else "buy"
        side_b = "sell" if (pos_b and self._parse_position_amt(pos_b) > 0) else "buy"
        
        if direction == 0:
            # Emergency close = Force Market
            await self._execute_maker_chaser(self.sym_a, self.sym_b, side_a, side_b, amt_a, amt_b, is_exit=True, force_market=True)
        else:
            # Normal close = Maker Chaser
            await self._execute_maker_chaser(self.sym_a, self.sym_b, side_a, side_b, amt_a, amt_b, is_exit=True, force_market=False)
            
        return True

    async def _execute_entry_resting(self, direction: int, mark_a: float, mark_b: float):
        """
        Predictive Resting Entry: chases the exact 100ms orderbook with Maker Limit orders.
        """
        broker_a = self.exchange_manager.get_adapter(self.sym_a)
        broker_b = self.exchange_manager.get_adapter(self.sym_b)
        
        if self.HEDGE_RATIO < 1.0:
            notional_a = self.NOTIONAL_PER_LEG
            notional_b = self.NOTIONAL_PER_LEG / self.HEDGE_RATIO
        else:
            notional_a = self.NOTIONAL_PER_LEG * self.HEDGE_RATIO
            notional_b = self.NOTIONAL_PER_LEG
            
        qty_a = notional_a / mark_a
        qty_b = notional_b / mark_b
        
        try:
            qty_a = float(broker_a.exchange.amount_to_precision(self.sym_a, qty_a))
            qty_b = float(broker_b.exchange.amount_to_precision(self.sym_b, qty_b))
        except Exception:
            pass  # Fallback: use raw qty, exchange will truncate
        
        side_a = "buy" if direction > 0 else "sell"
        side_b = "sell" if direction > 0 else "buy"
        
        print(f"[{ts_iso()}] [EXEC] Starting Maker Chaser for {self.sym_a} and {self.sym_b}")
        await self._execute_maker_chaser(self.sym_a, self.sym_b, side_a, side_b, qty_a, qty_b, is_exit=False, force_market=False)
        
        # Step 5: Fetch real fill prices from exchange (actual avg fill, not mark)
        await asyncio.sleep(1)  # Brief wait for Binance to settle
        try:
            real_pos_a = await broker_a.fetch_position()
            real_pos_b = await broker_b.fetch_position()
            if real_pos_a:
                real_entry_a = float(real_pos_a.get("entryPrice", 0.0))
                if real_entry_a > 0:
                    self.entry_mark_a = real_entry_a
            if real_pos_b:
                real_entry_b = float(real_pos_b.get("entryPrice", 0.0))
                if real_entry_b > 0:
                    self.entry_mark_b = real_entry_b
            print(f"[{ts_iso()}] [EXEC] Real fills: {self.sym_a}=${self.entry_mark_a:.2f} | {self.sym_b}=${self.entry_mark_b:.2f}")
            self._save_entry_state()
        except Exception as e:
            print(f"[{ts_iso()}] [WARN] Could not fetch real fill prices: {e}")

    async def _trading_tick(self, mark_a: float, mark_b: float, ts_now: float):
        if mark_a <= 0 or mark_b <= 0:
            return
            
        current_ratio = math.log(mark_a) - math.log(mark_b)
        
        # Update return buffers for correlation gate
        if self._prev_mark_a > 0 and self._prev_mark_b > 0:
            self._ret_a_buf.append((mark_a - self._prev_mark_a) / self._prev_mark_a)
            self._ret_b_buf.append((mark_b - self._prev_mark_b) / self._prev_mark_b)
        self._prev_mark_a = mark_a
        self._prev_mark_b = mark_b
        
        # Recompute correlation every 3 ticks (~3s in live)
        self._corr_tick += 1
        if self._corr_tick % 3 == 0 and len(self._ret_a_buf) >= 300:
            arr_a = np.array(list(self._ret_a_buf)[-300:])
            arr_b = np.array(list(self._ret_b_buf)[-300:])
            if np.std(arr_a) > 1e-12 and np.std(arr_b) > 1e-12:
                c = np.corrcoef(arr_a, arr_b)[0, 1]
                if not np.isnan(c):
                    self._last_corr = c
        
        # Periodically sample the ratio
        if ts_now - self.last_sample_ts >= self.SAMPLE_INTERVAL_SEC:
            # Gap detection (e.g. internet went down for > 1 hour)
            if self.last_sample_ts > 0 and ts_now - self.last_sample_ts > 3600:
                print(f"[{ts_iso()}] [WARNING] Data gap of {ts_now - self.last_sample_ts:.0f}s detected. Fetching missing klines to patch history...")
                try:
                    adapter_a = self.exchange_manager.get_adapter(self.sym_a)
                    adapter_b = self.exchange_manager.get_adapter(self.sym_b)
                    
                    missing_seconds = ts_now - self.last_sample_ts
                    candles_missed = int(missing_seconds / 300)
                    fetch_limit = min(candles_missed + 2, self.HISTORY_LENGTH)
                    since_ms = int(self.last_sample_ts * 1000)
                    
                    klines_a = await adapter_a.exchange.fetch_ohlcv(self.sym_a, timeframe='5m', since=since_ms, limit=fetch_limit)
                    klines_b = await adapter_b.exchange.fetch_ohlcv(self.sym_b, timeframe='5m', since=since_ms, limit=fetch_limit)
                    
                    dict_b = { int(k[0]/1000): float(k[4]) for k in klines_b }
                    patched_count = 0
                    
                    for k in klines_a:
                        k_ts = int(k[0]/1000)
                        if k_ts > self.last_sample_ts and k_ts <= ts_now:
                            close_a = float(k[4])
                            close_b = dict_b.get(k_ts)
                            if close_b:
                                ratio = math.log(close_a) - math.log(close_b)
                                self.ratio_history.append(ratio)
                                self.last_sample_ts = float(k_ts)
                                patched_count += 1
                                
                    print(f"[{ts_iso()}] [RECOVERY] Successfully patched {patched_count} missing samples into history.")
                except Exception as e:
                    print(f"[{ts_iso()}] [ERROR] Failed to patch gap: {e}. Flushing history.")
                    self.ratio_history.clear()
                    # If we are in a position, we are flying blind. Force close via Market.
                    if self.open_position:
                        print(f"[{ts_iso()}] [EMERGENCY] Closing spread via Market due to data gap.")
                        final_pnl = self._estimate_pnl(mark_a, mark_b)
                        await self._execute_spread(0, mark_a, mark_b)
                        self.log_trade("EMERGENCY_GAP_CLOSE", mark_a, mark_b, 0.0, final_pnl)
                        self.open_position = False
                        self.closing_position = False
                        self.position_direction = 0
                        self._clear_entry_state()
            
            # Prevent double appending if gap patch caught us up to current tick
            if ts_now - self.last_sample_ts >= self.SAMPLE_INTERVAL_SEC - 5: 
                self.ratio_history.append(current_ratio)
                self.last_sample_ts = ts_now
            
            # Update BTC EMA (4H 90-EMA approximation -> N=4320)
            if self.btc_ema == 0.0:
                self.btc_ema = mark_a
            else:
                alpha = 2.0 / 4321.0
                self.btc_ema = (mark_a * alpha) + (self.btc_ema * (1.0 - alpha))
            self._save_ema_state()
            
        if len(self.ratio_history) < 50:
            return  # Need enough history to compute meaningful Z-Score
            
        # Calculate Mean and StDev
        hist_arr = np.array(self.ratio_history)
        mean_ratio = np.mean(hist_arr)
        std_ratio = np.std(hist_arr)
        
        if std_ratio < 1e-8:
            return
            
        z_score = (current_ratio - mean_ratio) / std_ratio
        
        # ── Live Position Reconciliation (Manual-Close Detection) ──────────────
        # Runs every 60s while bot believes it holds an open spread.
        # If the exchange reports 0 qty on both legs, the position was manually
        # closed externally. We self-heal immediately without requiring a restart.
        if self.open_position and not self.closing_position:
            if ts_now - self._last_position_check_ts >= 60:
                self._last_position_check_ts = ts_now
                broker_a = self.exchange_manager.get_adapter(self.sym_a)
                broker_b = self.exchange_manager.get_adapter(self.sym_b)
                
                pos_a, pos_b = None, None
                fetch_success = False
                for attempt in range(2):
                    try:
                        # Sync time before authenticated call to prevent signature errors
                        if attempt > 0:
                            if hasattr(broker_a, 'sync_time'):
                                await broker_a.sync_time()
                            await asyncio.sleep(2.0)
                        pos_a = await broker_a.fetch_position()
                        pos_b = await broker_b.fetch_position()
                        fetch_success = True
                        break  # Success
                    except Exception as e:
                        if attempt == 0:
                            continue  # Silent retry
                        # Only log on final failure, and suppress repeated failures
                        if not hasattr(self, '_reconcile_fail_count'):
                            self._reconcile_fail_count = 0
                        self._reconcile_fail_count += 1
                        if self._reconcile_fail_count <= 3 or self._reconcile_fail_count % 10 == 0:
                            print(f"[{ts_iso()}] [RECONCILE] Position check failed (attempt {self._reconcile_fail_count}): {type(e).__name__}")
                
                if fetch_success:
                    if not hasattr(self, '_reconcile_fail_count'):
                        self._reconcile_fail_count = 0
                    if self._reconcile_fail_count > 0:
                        self._reconcile_fail_count = 0  # Reset on success
                        
                    amt_a = self._parse_position_amt(pos_a) if pos_a else 0.0
                    amt_b = self._parse_position_amt(pos_b) if pos_b else 0.0
                    
                    if abs(amt_a) < 1e-6 and abs(amt_b) < 1e-6:
                        print(f"[{ts_iso()}] [RECONCILE] Positions not found on exchange — detected MANUAL CLOSE. Resetting to SCANNING.")
                        final_pnl = self._estimate_pnl(mark_a, mark_b)
                        self.log_trade("MANUAL_CLOSE", mark_a, mark_b, z_score, final_pnl)
                        self.open_position = False
                        self.closing_position = False
                        self.position_direction = 0
                        self.entry_ts = 0.0
                        self.entry_mark_a = 0.0
                        self.entry_mark_b = 0.0
                        self.entry_mean_ratio = 0.0
                        self._clear_entry_state()
                        return  # Skip rest of tick; will enter SCANNING on next tick

        # ── Orphan Position Detector (Network Fault-Tolerance) ──────────────
        # Runs every 60s while bot is scanning, checking for unmanaged positions
        # caused by network timeouts during failed entry attempts.
        if not self.open_position:
            if ts_now - self._last_position_check_ts >= 60:
                self._last_position_check_ts = ts_now
                broker_a = self.exchange_manager.get_adapter(self.sym_a)
                broker_b = self.exchange_manager.get_adapter(self.sym_b)
                try:
                    pos_a = await broker_a.fetch_position()
                    pos_b = await broker_b.fetch_position()
                    amt_a = self._parse_position_amt(pos_a) if pos_a else 0.0
                    amt_b = self._parse_position_amt(pos_b) if pos_b else 0.0
                    
                    if abs(amt_a) > 0 or abs(amt_b) > 0:
                        print(f"[{ts_iso()}] [CRITICAL] Orphan Position Detected! {self.sym_a}: {amt_a}, {self.sym_b}: {amt_b}")
                        print(f"[{ts_iso()}] [ADOPT] Auto-adopting manual position to manage it, instead of blindly liquidating.")
                        
                        if amt_a > 0 and amt_b < 0:
                            self.position_direction = 1
                        elif amt_a < 0 and amt_b > 0:
                            self.position_direction = -1
                        else:
                            self.position_direction = 1 if amt_a > 0 else -1
                            
                        self.entry_mark_a = float(pos_a.get("entryPrice", mark_a)) if pos_a else mark_a
                        self.entry_mark_b = float(pos_b.get("entryPrice", mark_b)) if pos_b else mark_b
                        
                        extracted_ts = 0
                        if pos_a:
                            extracted_ts = pos_a.get("timestamp") or pos_a.get("lastUpdateTimestamp")
                            if not extracted_ts and "info" in pos_a:
                                extracted_ts = int(pos_a["info"].get("updateTime", 0))
                        
                        if extracted_ts and extracted_ts > 0:
                            self.entry_ts = float(extracted_ts) / 1000.0
                            print(f"[{ts_iso()}] [ADOPT] Found precise historical entry timestamp.")
                        else:
                            self.entry_ts = ts_now
                            extracted_ts = int(ts_now * 1000)
                            
                        limit = 288
                        since_ms = int(extracted_ts) - (limit * 5 * 60 * 1000)
                        
                        try:
                            klines_a = await broker_a.exchange.fetch_ohlcv(self.sym_a, timeframe='5m', since=since_ms, limit=limit)
                            klines_b = await broker_b.exchange.fetch_ohlcv(self.sym_b, timeframe='5m', since=since_ms, limit=limit)
                            
                            dict_b = { int(k[0]/1000): float(k[4]) for k in klines_b }
                            hist_ratios = []
                            for k in klines_a:
                                t_a = int(k[0]/1000)
                                if t_a in dict_b:
                                    close_a = float(k[4])
                                    close_b = dict_b[t_a]
                                    if close_a > 0 and close_b > 0:
                                        hist_ratios.append(math.log(close_a) - math.log(close_b))
                            
                            if len(hist_ratios) >= 50:
                                hist_arr = np.array(hist_ratios)
                                self.entry_mean_ratio = float(np.mean(hist_arr))
                                self.entry_std_ratio = float(np.std(hist_arr))
                                print(f"[{ts_iso()}] [ADOPT] Perfect historical Z-score calibration successful.")
                            else:
                                raise ValueError("Insufficient historical overlap.")
                        except Exception as e:
                            print(f"[{ts_iso()}] [ADOPT] Historical reconstruction failed: {e}. Falling back to current.")
                            if len(self.ratio_history) >= 50:
                                hist_arr = np.array(self.ratio_history)
                                self.entry_mean_ratio = float(np.mean(hist_arr))
                                self.entry_std_ratio = float(np.std(hist_arr))
                            else:
                                self.entry_mean_ratio = current_ratio
                                self.entry_std_ratio = 0.01
                        
                        self.open_position = True
                        self.closing_position = False
                        self._save_entry_state()
                        print(f"[{ts_iso()}] [ADOPT] Successfully adopted position! Dir={self.position_direction}, Entry A={self.entry_mark_a:.2f}, Entry B={self.entry_mark_b:.2f}")
                except Exception:
                    pass  # Suppress logs if network is down

        # ── Check Exits ──
        if self.open_position and not self.closing_position:
            should_exit = False
            self.exit_reason = ""
            
            pnl = self._estimate_pnl(mark_a, mark_b)
            
            # Calculate Anchored Z for exit evaluation
            anchored_z = 0.0
            if self.entry_std_ratio > 1e-8:
                anchored_z = (current_ratio - self.entry_mean_ratio) / self.entry_std_ratio
                
            # 1. Hard Stop Loss
            if pnl <= self.STOP_LOSS_USD:
                should_exit = True
                self.exit_reason = f"STOP_LOSS (PnL: ${pnl:.2f})"
                
            # 2. Time-Based Stop Loss (Rotting Trade)
            elif ts_now - self.entry_ts >= self.MAX_HOLD_SEC:
                should_exit = True
                self.exit_reason = f"TIME_LIMIT_EXCEEDED"
                
            # 3. Emergency Z-Score Stop Loss (Structural Break)
            elif abs(anchored_z) >= getattr(self, 'EMERGENCY_Z_STOP', 10.0):
                should_exit = True
                self.exit_reason = f"EMERGENCY_Z_STOP (anchored_z={anchored_z:.2f}, PnL: ${pnl:.2f})"
                
                # Activate Cooldown to prevent re-entry knife catching
                cooldown_sec = getattr(self, 'EMERGENCY_COOLDOWN_SEC', 14400) # 4 hours
                self.cooldown_until = ts_now + cooldown_sec
                print(f"[{ts_iso()}] [CRITICAL] Structural break detected. Emergency exit triggered. Trading paused for {cooldown_sec//3600} hours.")
                
            # 4. Dynamic Anchored Z-Score Take Profit
            # Uses the frozen entry_mean_ratio + entry_std_ratio to compute an
            # anchored Z-score. Exits when the spread has fully mean-reverted back
            # through DYNAMIC_Z_TARGET (default 0.0 = the mean). This outperforms
            # the static OVERSHOOT_PCT in both high-vol and low-vol regimes.
            else:
                if self.entry_std_ratio > 1e-8:
                    z_target = getattr(self, 'DYNAMIC_Z_TARGET', None)
                    if z_target is not None:
                        effective_target_long = z_target  # default: 0.0
                        effective_target_short = z_target # default: 0.0
                        # Time-decay: relax the target as trade ages
                        if getattr(self, 'Z_DECAY_ENABLED', False):
                            hold_elapsed = ts_now - self.entry_ts
                            hold_fraction = hold_elapsed / self.MAX_HOLD_SEC
                            decay_start = getattr(self.cfg, 'z_decay_start', 0.50)
                            decay_max = getattr(self.cfg, 'z_decay_max', 0.25)
                            
                            if hold_fraction > decay_start:
                                decay_progress = min(1.0, (hold_fraction - decay_start) / (1.0 - decay_start))
                                decay_amount = decay_progress * decay_max
                                entry_z_abs = abs(getattr(self, 'entry_z', self.Z_ENTRY_THRESHOLD))
                                effective_target_long = z_target - (decay_amount * entry_z_abs)
                                effective_target_short = z_target + (decay_amount * entry_z_abs)
                        # Long spread: exit when anchored Z >= effective_target_long
                        if self.position_direction > 0 and anchored_z >= effective_target_long:
                            should_exit = True
                            self.exit_reason = f"DYNAMIC_Z_REVERSION (anchored_z={anchored_z:.2f}, target={effective_target_long:.2f}, PnL: ${pnl:.2f})"
                        # Short spread: exit when anchored Z <= effective_target_short
                        elif self.position_direction < 0 and anchored_z <= effective_target_short:
                            should_exit = True
                            self.exit_reason = f"DYNAMIC_Z_REVERSION (anchored_z={anchored_z:.2f}, target={effective_target_short:.2f}, PnL: ${pnl:.2f})"
                    else:
                        # Legacy fallback: static overshoot
                        if self.position_direction > 0 and current_ratio >= self.entry_mean_ratio + self.OVERSHOOT_PCT:
                            should_exit = True
                            self.exit_reason = f"OVERSHOOT_REVERSION (PnL: ${pnl:.2f})"
                        elif self.position_direction < 0 and current_ratio <= self.entry_mean_ratio - self.OVERSHOOT_PCT:
                            should_exit = True
                            self.exit_reason = f"OVERSHOOT_REVERSION (PnL: ${pnl:.2f})"
            if should_exit:
                print(f"[{ts_iso()}] [PAIRS EXIT] {self.exit_reason}. Initiating Spread Chase.")
                self.closing_position = True
                self.close_start_ts = ts_now
            
        if self.closing_position:
            broker_a = self.exchange_manager.get_adapter(self.sym_a)
            broker_b = self.exchange_manager.get_adapter(self.sym_b)
            
            # Emergency taker fallback: if chase has run > 60 seconds, force Market close
            chase_elapsed = ts_now - getattr(self, 'close_start_ts', ts_now)
            if chase_elapsed > 60:
                print(f"[{ts_iso()}] [PAIRS EXIT] Chase timeout ({chase_elapsed:.0f}s). Forcing Market exit.")
                try:
                    await asyncio.gather(broker_a.cancel_all(), broker_b.cancel_all())
                except Exception as e:
                    print(f"[{ts_iso()}] [PAIRS EXIT] Failed to cancel orders during emergency exit: {e}")
                
                success = await self._execute_spread(0, mark_a, mark_b)
                if not success:
                    print(f"[{ts_iso()}] [PAIRS EXIT] Emergency close failed (network issue). Will retry.")
                    self.close_start_ts = ts_now - 30  # Give it 30s breathing room before next emergency attempt
                    return
                
                final_pnl = self._estimate_pnl(mark_a, mark_b, taker_exit=True)
                self.log_trade(self.exit_reason + " (TAKER_FALLBACK)", mark_a, mark_b, z_score, final_pnl)
                self.open_position = False
                self.closing_position = False
                self.position_direction = 0
                self._clear_entry_state()
                return
            
            # Normal maker chase flow
            await asyncio.gather(broker_a.cancel_all(), broker_b.cancel_all())
            
            try:
                pos_a = await broker_a.fetch_position()
                pos_b = await broker_b.fetch_position()
            except Exception as e:
                print(f"[{ts_iso()}] [PAIRS EXIT] fetch_position failed during chase: {e}")
                return  # skip tick, retry next tick
            
            amt_a = self._parse_position_amt(pos_a) if pos_a else 0.0
            amt_b = self._parse_position_amt(pos_b) if pos_b else 0.0
            
            if abs(amt_a) < 1e-6 and abs(amt_b) < 1e-6:
                print(f"[{ts_iso()}] [PAIRS EXIT] Successfully closed all positions via Maker Limits.")
                final_pnl = self._estimate_pnl(mark_a, mark_b)
                self.log_trade(self.exit_reason, mark_a, mark_b, z_score, final_pnl)
                self.open_position = False
                self.closing_position = False
                self.position_direction = 0
                self._clear_entry_state()
                return
                
            tasks = []
            if abs(amt_a) >= 1e-6:
                side_a = "sell" if amt_a > 0 else "buy"
                tasks.append(broker_a.place_order(side_a, mark_a, abs(amt_a), reduce_only=True, order_type="limit", post_only=True))
                
            if abs(amt_b) >= 1e-6:
                side_b = "sell" if amt_b > 0 else "buy"
                tasks.append(broker_b.place_order(side_b, mark_b, abs(amt_b), reduce_only=True, order_type="limit", post_only=True))
                
            if tasks:
                await asyncio.gather(*tasks)
            return
            
        # ── Check Entries ──
        if not self.open_position:
            # Enforce Emergency Cooldown
            if hasattr(self, 'cooldown_until') and ts_now < self.cooldown_until:
                return  # Skip entry evaluation entirely until cooldown expires
                
            # EMA Regime Filter is always enabled.
            # We invert the base hedge ratio during bear markets to bias the apex asset.
            use_dynamic = getattr(self, "DYNAMIC_HEDGE", True)
            if use_dynamic:
                if self.btc_ema > 0:
                    if mark_a > self.btc_ema:
                        self.HEDGE_RATIO = getattr(self, "BASE_HEDGE_RATIO", 0.5)
                    else:
                        base = getattr(self, "BASE_HEDGE_RATIO", 0.5)
                        self.HEDGE_RATIO = 1.0 / base if base > 0 else 1.0
                        
                        # Extreme Z-Score Override: If ETH or BTC has completely flash-crashed, 
                        # override the defensive posture to aggressively catch the bounce.
                        if abs(z_score) >= 5.0:
                            self.HEDGE_RATIO = 0.5

            divergence_pct = abs(current_ratio - mean_ratio)

            # Correlation gate: block entries when pair is decoupled
            if self.CORR_MIN > 0 and self._last_corr < self.CORR_MIN and len(self._ret_a_buf) >= 300:
                return  # Pair decoupled — skip entry
                
            is_long_entry = z_score < -self.Z_ENTRY_THRESHOLD and divergence_pct >= self.MIN_SPREAD_PCT
            is_short_entry = z_score > self.Z_ENTRY_THRESHOLD and divergence_pct >= self.MIN_SPREAD_PCT
            
            if is_long_entry or is_short_entry:
                if is_long_entry:
                    # Long Spread: Buy A, Sell B
                    print(f"[{ts_iso()}] [PAIRS ENTRY] Long Spread | Z={z_score:.2f} | Div={divergence_pct*100:.2f}% | Long {self.sym_a}, Short {self.sym_b}")
                    self.open_position = True
                    self.position_direction = 1
                    self.entry_z = z_score
                    self.entry_ts = ts_now
                    self.entry_mark_a = mark_a
                    self.entry_mark_b = mark_b
                    self.entry_mean_ratio = mean_ratio
                    self.entry_std_ratio = std_ratio
                    self._save_entry_state()
                    try:
                        await self._execute_entry_resting(1, mark_a, mark_b)
                        self.log_trade("ENTRY_LONG_SPREAD", mark_a, mark_b, z_score)
                    except Exception as e:
                        print(f"[{ts_iso()}] [ERROR] Entry execution aborted: {e}. Reverting state.")
                        self.open_position = False
                        self.position_direction = 0
                        self._clear_entry_state()
                    
                elif is_short_entry:
                    # Short Spread: Sell A, Buy B
                    print(f"[{ts_iso()}] [PAIRS ENTRY] Short Spread | Z={z_score:.2f} | Div={divergence_pct*100:.2f}% | Short {self.sym_a}, Long {self.sym_b}")
                    self.open_position = True
                    self.position_direction = -1
                    self.entry_z = z_score
                    self.entry_ts = ts_now
                    self.entry_mark_a = mark_a
                    self.entry_mark_b = mark_b
                    self.entry_mean_ratio = mean_ratio
                    self.entry_std_ratio = std_ratio
                    self._save_entry_state()
                    try:
                        await self._execute_entry_resting(-1, mark_a, mark_b)
                        self.log_trade("ENTRY_SHORT_SPREAD", mark_a, mark_b, z_score)
                    except Exception as e:
                        print(f"[{ts_iso()}] [ERROR] Entry execution aborted: {e}. Reverting state.")
                        self.open_position = False
                        self.position_direction = 0
                        self._clear_entry_state()
    
    async def run(self):
        print(f"\n{'='*60}")
        print(f"  PAIRS TRADER - Statistical Arbitrage")
        print(f"  Pair: {self.sym_a} vs {self.sym_b}")
        print(f"  Z-Score Entry: ±{self.Z_ENTRY_THRESHOLD}")
        print(f"  Notional Per Leg: ${self.NOTIONAL_PER_LEG}")
        print(f"")
        print(f"  [!] WARNING: HORIZON REQUIREMENT")
        print(f"  Mean-reversion requires a long-term investment horizon.")
        print(f"  Minimum recommended runtime: 12-18 months to smooth out")
        print(f"  'crypto winter' drawdowns and capture full regime cycles.")
        print(f"{'='*60}\n")

        # Fetch Wallet Size and Recommend Sizing
        try:
            adapter_a = self.exchange_manager.get_adapter(self.sym_a)
            adapter_b = self.exchange_manager.get_adapter(self.sym_b)
            if hasattr(adapter_a, "exchange"):
                try:
                    await adapter_a.exchange.set_leverage(5, self.sym_a)
                    print(f"[{ts_iso()}] [MARGIN] Set 5x leverage for {self.sym_a}")
                except Exception as e:
                    print(f"[{ts_iso()}] [MARGIN] Could not set leverage for {self.sym_a} (may already be 5x): {e}")

            if hasattr(adapter_b, "exchange"):
                try:
                    await adapter_b.exchange.set_leverage(5, self.sym_b)
                    print(f"[{ts_iso()}] [MARGIN] Set 5x leverage for {self.sym_b}")
                except Exception as e:
                    print(f"[{ts_iso()}] [MARGIN] Could not set leverage for {self.sym_b} (may already be 5x): {e}")

            if hasattr(adapter_a, "exchange"):
                bal_data = await adapter_a.exchange.fetch_balance()
                usdt_bal = bal_data.get("USDT", {}).get("total", 0.0)
                
                safe_util = 0.8
                buying_power = usdt_bal * 5.0 * safe_util
                if self.HEDGE_RATIO < 1.0:
                    rec_leg = buying_power / (1.0 + (1.0 / self.HEDGE_RATIO))
                else:
                    rec_leg = buying_power / (1.0 + self.HEDGE_RATIO)
                
                print(f"[{ts_iso()}] [WALLET] Detected USDT Balance: ${usdt_bal:.2f}")
                print(f"[{ts_iso()}] [ADVICE] Recommended Max Static Size: ${rec_leg:.2f} per leg (80% utilization @ 5x)")
                
                if self.NOTIONAL_PER_LEG > rec_leg:
                    self.wallet_advice = f"?? Wallet: ${usdt_bal:.2f} (UNSAFE)"
                    print(f"[{ts_iso()}] [WARNING] Wallet: ${usdt_bal:.2f} | Configured size (${self.NOTIONAL_PER_LEG:.2f}) EXCEEDS safe recommendation!")
                else:
                    self.wallet_advice = f"Wallet: ${usdt_bal:.2f} (SAFE)"
                    print(f"[{ts_iso()}] [ADVICE] Wallet: ${usdt_bal:.2f} | Configured size (${self.NOTIONAL_PER_LEG:.2f}) is safe.")
        except Exception as e:
            print(f"[{ts_iso()}] [RECONCILE] Failed to fetch positions: {e}")

        # Bootstrap history
        await self._bootstrap_history(lookback_hours=24)
        
        # Check if there is an active entry state on disk to auto-adopt an open position
        if self._load_entry_state():
            self.open_position = True
            print(f"[{ts_iso()}] [BOOTSTRAP] Auto-adopted open position from disk.")
        elif self.open_position:
            # No persisted state — fall back to bootstrapped history (best effort)
            if len(self.ratio_history) > 0:
                hist_arr = np.array(self.ratio_history)
                self.entry_mean_ratio = float(np.mean(hist_arr))
                self.entry_std_ratio = float(np.std(hist_arr))
                print(f"[{ts_iso()}] [RECONCILE] No saved entry state — using bootstrapped history as fallback.")

        state_path = os.path.join(os.getcwd(), "data", "live_state.pt")
        last_state_ts = 0.0
        last_sync_ts = time.time()
        sync_interval = 300.0
        killer = GracefulKiller()

        _last_shm_warn_ts = 0.0
        _last_gc_ts = time.time()

        while not killer.kill_now:
            loop_start = time.time()
            if loop_start - _last_gc_ts > 60:
                import gc
                gc.collect()
                _last_gc_ts = loop_start
            try:
                shm_state = self.shm_mgr.read() if self.shm_mgr else None
                if shm_state and shm_state.get("marks") is not None:
                    self.state = shm_state
                else:
                    # SHM unavailable — warn once every 30s and skip tick
                    now_warn = time.time()
                    if now_warn - _last_shm_warn_ts >= 30.0:
                        print(f"[{ts_iso()}] [WARNING] SHM unavailable — is Scribe running? Waiting...")
                        _last_shm_warn_ts = now_warn
                    await asyncio.sleep(0.5)
                    continue

                mark_a = float(self.state.get("marks", {}).get(self.sym_a, {}).get("mark_price", 0.0))
                mark_b = float(self.state.get("marks", {}).get(self.sym_b, {}).get("mark_price", 0.0))
                
                # Fallback: use L2 book mid if mark_price stream is unavailable
                if mark_a <= 0:
                    book_a = self.state.get("books", {}).get(self.sym_a, {})
                    bids_a = book_a.get("bids", [])
                    asks_a = book_a.get("asks", [])
                    if bids_a and asks_a:
                        mark_a = (float(bids_a[0][0]) + float(asks_a[0][0])) / 2.0
                if mark_b <= 0:
                    book_b = self.state.get("books", {}).get(self.sym_b, {})
                    bids_b = book_b.get("bids", [])
                    asks_b = book_b.get("asks", [])
                    if bids_b and asks_b:
                        mark_b = (float(bids_b[0][0]) + float(asks_b[0][0])) / 2.0
                
                # One-time confirmation that marks are flowing
                if not hasattr(self, '_marks_confirmed') and mark_a > 0 and mark_b > 0:
                    self._marks_confirmed = True
                    print(f"[{ts_iso()}] [LIVE] Marks flowing: {self.sym_a}=${mark_a:.2f} | {self.sym_b}=${mark_b:.2f}")

                await self._trading_tick(mark_a, mark_b, time.time())

            except Exception as e:
                import traceback
                print(f"[{ts_iso()}] Loop error: {e}")
                traceback.print_exc()

            elapsed = time.time() - loop_start
            sleep_time = max(0.01, self.cfg.loop_interval_sec - elapsed)
            await asyncio.sleep(sleep_time)

            now_ts = time.time()
            if now_ts - last_sync_ts > sync_interval:
                for sym in [self.sym_a, self.sym_b]:
                    adapter = self.exchange_manager.get_adapter(sym)
                    if hasattr(adapter, "sync_time"):
                        await adapter.sync_time()
                last_sync_ts = now_ts
                
            if not hasattr(self, '_last_signal_export'):
                self._last_signal_export = 0.0
            if not hasattr(self, '_last_heartbeat'):
                self._last_heartbeat = 0.0
                
            # FAST LOOP (5 seconds): Update the UI/Dashboard JSON
            if now_ts - self._last_signal_export >= 5.0:
                status = "IN SPREAD" if self.open_position else "SCANNING"
                
                z_str = "N/A"
                z_score = 0.0
                mean_ratio = 0.0
                std_ratio = 0.0
                current_ratio = 0.0
                if mark_a > 0 and mark_b > 0:
                    current_ratio = float(math.log(mark_a) - math.log(mark_b))
                
                if len(self.ratio_history) > 50 and current_ratio != 0:
                    history_arr = np.array(self.ratio_history)
                    mean_ratio = float(np.mean(history_arr))
                    std_ratio = float(np.std(history_arr))
                    if std_ratio > 0:
                        z_score = float((current_ratio - mean_ratio) / std_ratio)
                        z_str = f"{z_score:+.2f}"
                        
                # Compute anchored Z-score (frozen at entry) for accurate in-trade monitoring
                anchored_z = 0.0
                est_pnl = 0.0
                if self.open_position and self.entry_std_ratio > 1e-8 and current_ratio != 0:
                    anchored_z = float((current_ratio - self.entry_mean_ratio) / self.entry_std_ratio)
                    est_pnl = float(self._estimate_pnl(mark_a, mark_b))
                
                sig_data = {
                    "ts": float(now_ts),
                    "status": status,
                    "sym_a": self.sym_a,
                    "sym_b": self.sym_b,
                    "mark_a": float(mark_a),
                    "mark_b": float(mark_b),
                    "z_score": float(z_score),
                    "anchored_z": anchored_z,
                    "estimated_pnl": est_pnl,
                    "mean_ratio": float(mean_ratio),
                    "std_ratio": float(std_ratio),
                    "current_ratio": float(current_ratio),
                    "ratio_history": [float(r) for r in self.ratio_history][-100:] if self.ratio_history else [],
                    "open_position": bool(self.open_position),
                    "position_direction": int(self.position_direction) if hasattr(self, 'position_direction') else 0,
                    "entry_ts": float(getattr(self, 'entry_ts', 0)) if self.open_position else 0.0,
                    "entry_mean_ratio": float(getattr(self, 'entry_mean_ratio', 0)) if self.open_position else 0.0,
                    "entry_std_ratio": float(getattr(self, 'entry_std_ratio', 0)) if self.open_position else 0.0,
                    # Strategy config — read by dashboard
                                        "z_threshold": float(self.Z_ENTRY_THRESHOLD),
                    "wallet_advice": getattr(self, "wallet_advice", "No wallet data yet..."),
                    "notional_per_leg": float(self.NOTIONAL_PER_LEG),
                    "stop_loss_usd": float(self.STOP_LOSS_USD),
                }
                if self.shm_writer:
                    self.shm_writer.write(sig_data)
                self._last_signal_export = now_ts
                
                # SLOW LOOP (300 seconds): Print heartbeat to terminal
                if now_ts - self._last_heartbeat >= 300.0:
                    if self.open_position and mark_a > 0 and mark_b > 0:
                        # Show anchored Z + PnL for honest in-trade monitoring
                        az_str = f"{anchored_z:+.2f}" if self.entry_std_ratio > 1e-8 else "N/A"
                        hold_h = (now_ts - self.entry_ts) / 3600 if self.entry_ts > 0 else 0
                        print(f"[{ts_iso()}] [~ {status}] {self.sym_a}=${mark_a:.2f} | {self.sym_b}=${mark_b:.2f} | Z={z_str} | AnchoredZ={az_str} | PnL=${est_pnl:+.2f} | Hold={hold_h:.1f}h")
                    else:
                        print(f"[{ts_iso()}] [~ {status}] {self.sym_a}=${mark_a:.2f} | {self.sym_b}=${mark_b:.2f} | Z-Score={z_str}")
                    self._last_heartbeat = now_ts

        print(f"\n[{ts_iso()}] Pairs Trader shutting down...")
        if self.open_position:
            # ── Get current prices at shutdown (3-tier fallback) ──────────────
            # Tier 1: SHM mark price
            mark_a = float(self.state.get("marks", {}).get(self.sym_a, {}).get("mark_price", 0.0))
            mark_b = float(self.state.get("marks", {}).get(self.sym_b, {}).get("mark_price", 0.0))

            # Tier 2: L2 book mid (same fallback as the main loop)
            if mark_a <= 0:
                book_a = self.state.get("books", {}).get(self.sym_a, {})
                bids_a = book_a.get("bids", [])
                asks_a = book_a.get("asks", [])
                if bids_a and asks_a:
                    mark_a = (float(bids_a[0][0]) + float(asks_a[0][0])) / 2.0
            if mark_b <= 0:
                book_b = self.state.get("books", {}).get(self.sym_b, {})
                bids_b = book_b.get("bids", [])
                asks_b = book_b.get("asks", [])
                if bids_b and asks_b:
                    mark_b = (float(bids_b[0][0]) + float(asks_b[0][0])) / 2.0

            # Tier 3: REST API fetch — last resort when SHM/book unavailable
            if mark_a <= 0 or mark_b <= 0:
                try:
                    adapter_a = self.exchange_manager.get_adapter(self.sym_a)
                    adapter_b = self.exchange_manager.get_adapter(self.sym_b)
                    ticker_a = await adapter_a.exchange.fetch_ticker(self.sym_a)
                    ticker_b = await adapter_b.exchange.fetch_ticker(self.sym_b)
                    if mark_a <= 0:
                        mark_a = float(ticker_a.get("last", 0.0))
                    if mark_b <= 0:
                        mark_b = float(ticker_b.get("last", 0.0))
                    print(f"[{ts_iso()}] [SHUTDOWN] REST prices: {self.sym_a}=${mark_a:.2f} | {self.sym_b}=${mark_b:.2f}")
                except Exception as e:
                    print(f"[{ts_iso()}] [SHUTDOWN] REST price fetch failed: {e}")

            # ── Calculate PnL and stats ───────────────────────────────────────
            pnl = self._estimate_pnl(mark_a, mark_b) if mark_a > 0 and mark_b > 0 else 0.0
            hold_secs = time.time() - self.entry_ts if self.entry_ts > 0 else 0
            hold_h = hold_secs / 3600
            direction_str = "LONG SPREAD (Long BTC, Short ETH)" if self.position_direction > 0 else "SHORT SPREAD (Short BTC, Long ETH)"

            # Rolling + anchored Z-score
            z_score_str = "N/A"
            anchored_z_str = "N/A"
            if mark_a > 0 and mark_b > 0:
                current_ratio = math.log(mark_a) - math.log(mark_b)
                if self.entry_std_ratio > 1e-8:
                    az_val = (current_ratio - self.entry_mean_ratio) / self.entry_std_ratio
                    anchored_z_str = f"{az_val:+.2f}"
                
                if len(self.ratio_history) > 50:
                    hist_arr = np.array(self.ratio_history)
                    std = np.std(hist_arr)
                    if std > 1e-8:
                        z_val = (current_ratio - np.mean(hist_arr)) / std
                        z_score_str = f"{z_val:+.2f}"

            print(f"\n{'='*60}")
            print(f"  OPEN POSITION DETECTED")
            print(f"{'='*60}")
            print(f"  Direction:     {direction_str}")
            print(f"  Entry BTC:     ${self.entry_mark_a:.2f}  |  Current: ${mark_a:.2f}")
            print(f"  Entry ETH:     ${self.entry_mark_b:.2f}  |  Current: ${mark_b:.2f}")
            print(f"  Estimated PnL: ${pnl:+.2f}")
            print(f"  Hold Time:     {hold_h:.1f} hours")
            print(f"  Z-Score:       {z_score_str}  |  AnchoredZ: {anchored_z_str}")
            dyn_z = getattr(self, 'DYNAMIC_Z_TARGET', None)
            print(f"  Exit Target:   AnchoredZ={'N/A' if dyn_z is None else dyn_z} (dynamic Z) | {'waiting...' if anchored_z_str == 'N/A' else ('WOULD EXIT' if (self.position_direction > 0 and dyn_z is not None and float(anchored_z_str) >= dyn_z) or (self.position_direction < 0 and dyn_z is not None and float(anchored_z_str) <= -dyn_z) else 'not yet')}")
            print(f"{'='*60}")

            # Interactive prompt with 30s auto-close timeout
            import threading
            user_choice = [None]

            def ask_input():
                try:
                    answer = input("\n  Close position now? [Y/n] (auto-close in 30s): ").strip().lower()
                    user_choice[0] = answer
                except EOFError:
                    user_choice[0] = "y"

            input_thread = threading.Thread(target=ask_input, daemon=True)
            input_thread.start()
            input_thread.join(timeout=30.0)

            if user_choice[0] is None:
                print("\n  [TIMEOUT] No response — auto-closing positions for safety.")
                should_close = True
            elif user_choice[0] in ("n", "no"):
                should_close = False
            else:
                should_close = True

            if should_close:
                if mark_a > 0 and mark_b > 0:
                    # Log the trade with accurate prices before closing
                    z_score = 0.0
                    if len(self.ratio_history) > 50:
                        current_ratio = math.log(mark_a) - math.log(mark_b)
                        hist_arr = np.array(self.ratio_history)
                        std = np.std(hist_arr)
                        if std > 1e-8:
                            z_score = (current_ratio - np.mean(hist_arr)) / std
                    self.log_trade("SHUTDOWN_CLOSE", mark_a, mark_b, z_score, pnl)
                    await self._execute_spread(0, mark_a, mark_b)
                    print(f"[{ts_iso()}] [SHUTDOWN] Closed spread positions. Estimated PnL: ${pnl:+.2f}")
                else:
                    # No price data — emergency reduce-only market close
                    print(f"[{ts_iso()}] [SHUTDOWN] No price data — emergency market close.")
                    for sym in [self.sym_a, self.sym_b]:
                        try:
                            adapter = self.exchange_manager.get_adapter(sym)
                            await adapter.close_all()
                        except Exception as e:
                            print(f"[{ts_iso()}] [SHUTDOWN] Emergency close failed for {sym}: {e}")
                    print(f"[{ts_iso()}] [SHUTDOWN] Emergency close complete. Check Binance for actual PnL.")
            else:
                print(f"[{ts_iso()}] [SHUTDOWN] Positions LEFT OPEN on exchange. Bot state cleared.")
                print(f"  NOTE: On next startup, the bot will auto-adopt the open positions.")

        await self.exchange_manager.close_all()
        print(f"[{ts_iso()}] Shutdown complete.")


async def main():
    import os
    cfg_file = "user_settings.yaml"
    cfg = load_cfg(cfg_file)
    # For pairs trading, ensure the config has the pairs we want to trade.
    # The first two symbols in cfg.symbols will be used.
    trader = PairsTrader(cfg)
    await trader.run()

if __name__ == "__main__":
    from phase23_lib import TeeLogger
    sys.stdout = TeeLogger("logs/trader_pairs.log")
    asyncio.run(main())
