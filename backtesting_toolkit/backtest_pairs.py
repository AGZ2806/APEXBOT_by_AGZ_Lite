"""
Pairs Trader Backtester
=======================
Replays scribe data through trader_pairs.py's PairsTrader logic.
Feeds mark prices and executes trades.

Usage:
    python tests/backtest_pairs.py --hours 24
    python tests/backtest_pairs.py --start 2025-02-03 --end 2025-02-03
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import argparse
import json
import glob
import csv
import time as time_mod
import numpy as np
from datetime import datetime, timezone
from typing import Dict, List

from phase23_lib import load_cfg, BotConfig, TeeLogger

try:
    import orjson
    def load_json(line): return orjson.loads(line)
except ImportError:
    def load_json(line): return json.loads(line)


# ─── Mock Exchange ────────────────────────────────────────────────────
# Copied from backtest_ta.py to simulate Binance Futures.

class MockPosition:
    def __init__(self):
        self.contracts = 0.0
        self.entry_price = 0.0
        self.unrealized_pnl = 0.0
        self.mark_price = 0.0

class MockExchangeAdapter:
    def __init__(self, symbol: str, initial_balance: float = 1000.0):
        self.symbol = symbol
        self.balance = initial_balance
        self.position = MockPosition()
        self.current_mark = 0.0
        self.orders: List[Dict] = []
        self.trade_log: List[Dict] = []
        
        # Pairs trading with Market orders will hit Taker fees.
        # We assume maker=0.0002, taker=0.0005. 
        self.slippage_pct = 0.0003
        self.maker_fee_pct = 0.0002
        self.taker_fee_pct = 0.0005

        self.pnl_realized = 0.0
        self.wins = 0
        self.losses = 0
        self.funding_total = 0.0

    def apply_funding(self, funding_rate: float):
        if abs(self.position.contracts) < 0.0001:
            return 0.0
        notional = abs(self.position.contracts) * self.current_mark
        payment = notional * funding_rate
        if self.position.contracts > 0:  # Long pays positive funding
            self.balance -= payment
            self.funding_total -= payment
            return -payment
        else:  # Short receives positive funding
            self.balance += payment
            self.funding_total += payment
            return payment

    def set_mark(self, price: float):
        self.current_mark = price
        self.position.mark_price = price
        self._update_unrealized()

    def _update_unrealized(self):
        if abs(self.position.contracts) < 0.0001:
            self.position.unrealized_pnl = 0.0
            return
        if self.position.contracts > 0:
            self.position.unrealized_pnl = (self.current_mark - self.position.entry_price) * self.position.contracts
        else:
            self.position.unrealized_pnl = (self.position.entry_price - self.current_mark) * abs(self.position.contracts)

    def _execute_fill(self, side, price, qty, fee_pct, reduce_only=False):
        fee = price * qty * fee_pct

        if side == "buy":
            if self.position.contracts < 0:
                close_qty = min(qty, abs(self.position.contracts))
                pnl = (self.position.entry_price - price) * close_qty
                self.pnl_realized += pnl - fee
                self.balance += pnl - fee
                if pnl - fee > 0: self.wins += 1
                else: self.losses += 1
                self.position.contracts += close_qty
                qty -= close_qty
            if qty > 0.0001 and not reduce_only:
                if self.position.contracts > 0:
                    total = self.position.contracts + qty
                    self.position.entry_price = (self.position.entry_price * self.position.contracts + price * qty) / total
                    self.position.contracts = total
                else:
                    self.position.entry_price = price
                    self.position.contracts = qty
                self.balance -= fee
        else:  # sell
            if self.position.contracts > 0:
                close_qty = min(qty, self.position.contracts)
                pnl = (price - self.position.entry_price) * close_qty
                self.pnl_realized += pnl - fee
                self.balance += pnl - fee
                if pnl - fee > 0: self.wins += 1
                else: self.losses += 1
                self.position.contracts -= close_qty
                qty -= close_qty
            if qty > 0.0001 and not reduce_only:
                if self.position.contracts < 0:
                    total = abs(self.position.contracts) + qty
                    self.position.entry_price = (self.position.entry_price * abs(self.position.contracts) + price * qty) / total
                    self.position.contracts = -total
                else:
                    self.position.entry_price = price
                    self.position.contracts = -qty
                self.balance -= fee

        if abs(self.position.contracts) < 0.0001:
            self.position.contracts = 0.0
            self.position.entry_price = 0.0
        self._update_unrealized()

    async def fetch_position(self):
        return {
            "contracts": self.position.contracts,
            "positionAmt": self.position.contracts,
            "entryPrice": str(self.position.entry_price),
            "unrealizedPnl": str(self.position.unrealized_pnl)
        }

    async def fetch_open_orders(self):
        return self.orders

    async def cancel_all(self):
        self.orders.clear()

    async def cancel_order(self, order_id: str):
        self.orders = [o for o in self.orders if o.get("id") != order_id]

    async def place_order(self, side, price, qty, reduce_only=False, order_type="limit", post_only=False):
        if qty < 0.0001:
            return 0
        
        if order_type == "market" or price == 0:
            slip = self.slippage_pct if side == "buy" else -self.slippage_pct
            exec_price = self.current_mark * (1.0 + slip)
            self._execute_fill(side, exec_price, qty, self.taker_fee_pct, reduce_only)
            return 1
        else:
            # Simulate Maker fill (0.02% fee, no slippage)
            self._execute_fill(side, price, qty, self.maker_fee_pct, reduce_only)
            return 1

    def get_equity(self):
        return self.balance + self.position.unrealized_pnl

    async def close_all(self):
        if abs(self.position.contracts) > 0.0001:
            side = "sell" if self.position.contracts > 0 else "buy"
            await self.place_order(side, self.current_mark, abs(self.position.contracts), reduce_only=True, order_type="market")


class MockExchangeManager:
    def __init__(self, symbols, balance_per_sym=100.0):
        self.adapters = {sym: MockExchangeAdapter(sym, balance_per_sym) for sym in symbols}

    def get_adapter(self, sym):
        return self.adapters[sym]

    def set_mark_prices(self, marks: dict):
        for sym, price in marks.items():
            if sym in self.adapters:
                self.adapters[sym].set_mark(price)

    def get_total_equity(self):
        return sum(a.get_equity() for a in self.adapters.values())


# ─── Backtest Engine ──────────────────────────────────────────────────

async def run_backtest(cfg, hours=24, start_date=None, end_date=None, dynamic_exit_z=None, 
                       hedge_ratio_override=None, stop_loss_override=None, max_hold_override=None, z_decay=False,
                       emergency_z_stop_override=None, emergency_cooldown_override=None, notional_override=None):
    import phase23_lib
    from trader_pairs import PairsTrader

    data_dir = os.path.join(os.getcwd(), "data", "history")

    all_files = sorted(glob.glob(os.path.join(data_dir, "scribe_*.jsonl")))
    if not all_files:
        print("No scribe data files found!")
        return

    if start_date:
        start_prefix = f"scribe_{start_date}"
        end_prefix = f"scribe_{end_date}" if end_date else "scribe_9999"
        selected = [f for f in all_files
                    if os.path.basename(f) >= start_prefix and os.path.basename(f) <= end_prefix + "_99"]
        if selected and all_files.index(selected[0]) > 0:
            warmup_idx = all_files.index(selected[0]) - 1
            selected.insert(0, all_files[warmup_idx])
    else:
        cutoff_ts = time_mod.time() - hours * 3600
        selected = []
        for f in all_files:
            try:
                bn = os.path.basename(f).replace("scribe_", "").replace(".jsonl", "")
                parts = bn.split("_")
                dt = datetime.strptime(parts[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                if len(parts) > 1:
                    dt = dt.replace(hour=int(parts[1]))
                if dt.timestamp() >= cutoff_ts - 7200:
                    selected.append(f)
            except Exception:
                continue

    if not selected:
        print("No files match the specified range!")
        return

    print(f"[Backtest] Pairs Trading Mode | {len(selected)} files")
    
    sym_a = cfg.symbols[0]
    sym_b = cfg.symbols[1]
    
    # 2) Setup mock exchange and bot
    mock_exchange = MockExchangeManager([sym_a, sym_b], balance_per_sym=100.0)
    start_equity = mock_exchange.get_total_equity()

    # Create Bot
    bot = PairsTrader.__new__(PairsTrader)
    bot.cfg = cfg
    bot.sym_a = sym_a
    bot.sym_b = sym_b
    from collections import deque
    bot.ratio_history = deque(maxlen=PairsTrader.HISTORY_LENGTH)
    bot.last_sample_ts = 0.0
    bot.open_position = False
    bot.closing_position = False
    bot.exit_reason = ""
    bot.position_direction = 0
    bot.entry_z = 0.0
    bot.entry_ts = 0.0
    bot.entry_mark_a = 0.0
    bot.entry_mark_b = 0.0
    bot.entry_mean_ratio = 0.0
    bot.entry_std_ratio = 0.0
    bot.entry_notional = 0.0
    bot.close_start_ts = 0.0
    bot._last_position_check_ts = 0.0   # Manual-close detection (no-op in backtest)
    
    # Macro EMA Regime Filter
    bot.btc_ema = 0.0
    bot.EMA_STATE_FILE = "data/ema_state_backtest.json"
    
    # Correlation gate state
    bot._ret_a_buf = deque(maxlen=3600)
    bot._ret_b_buf = deque(maxlen=3600)
    bot._prev_mark_a = 0.0
    bot._prev_mark_b = 0.0
    bot._last_corr = 0.0
    bot._corr_tick = 0

    bot.exchange_manager = mock_exchange
    bot.state = {}
    pair_tag = f"{sym_a.split('/')[0]}_{sym_b.split('/')[0]}".lower()
    from datetime import datetime as _dt
    run_ts = _dt.now().strftime("%Y%m%d_%H%M")
    bot.trade_log_file = f"data/backtest_{pair_tag}_{run_ts}.csv"
    bot._init_trade_log()
    
    # Constants from class (shared)
    bot.Z_EXIT_THRESHOLD = PairsTrader.Z_EXIT_THRESHOLD
    bot.SAMPLE_INTERVAL_SEC = PairsTrader.SAMPLE_INTERVAL_SEC
    bot.NOTIONAL_PER_LEG = notional_override if notional_override is not None else PairsTrader.NOTIONAL_PER_LEG
    bot.MIN_SPREAD_PCT = PairsTrader.MIN_SPREAD_PCT
    bot.STOP_LOSS_USD = PairsTrader.STOP_LOSS_USD
    if emergency_z_stop_override is not None:
        bot.EMERGENCY_Z_STOP = emergency_z_stop_override
    else:
        bot.EMERGENCY_Z_STOP = getattr(PairsTrader, 'EMERGENCY_Z_STOP', 10.0)
    if emergency_cooldown_override is not None:
        bot.EMERGENCY_COOLDOWN_SEC = emergency_cooldown_override * 3600
    else:
        bot.EMERGENCY_COOLDOWN_SEC = getattr(PairsTrader, 'EMERGENCY_COOLDOWN_SEC', 14400)
    
    # Per-pair profile (try both orderings)
    pair_key = (sym_a, sym_b)
    pair_key_rev = (sym_b, sym_a)
    profile = PairsTrader.PAIR_PROFILES.get(pair_key) or PairsTrader.PAIR_PROFILES.get(pair_key_rev)
    if profile:
        bot.Z_ENTRY_THRESHOLD = profile["Z_ENTRY_THRESHOLD"]
        bot.OVERSHOOT_PCT = profile["OVERSHOOT_PCT"]
        bot.DYNAMIC_Z_TARGET = profile.get("DYNAMIC_Z_TARGET", None)
        bot.HEDGE_RATIO = profile["HEDGE_RATIO"]
        bot.MAX_HOLD_SEC = profile["MAX_HOLD_SEC"]
        bot.CORR_MIN = profile.get("CORR_MIN", 0.0)
        exit_str = f"DynZ={bot.DYNAMIC_Z_TARGET}" if bot.DYNAMIC_Z_TARGET is not None else f"OvSht={bot.OVERSHOOT_PCT}"
        print(f"[Backtest] Profile: Z={bot.Z_ENTRY_THRESHOLD}, Exit={exit_str}, "
              f"Hedge={bot.HEDGE_RATIO}, Hold={bot.MAX_HOLD_SEC//3600}h, CorrMin={bot.CORR_MIN}")
    else:
        bot.Z_ENTRY_THRESHOLD = 3.5
        bot.OVERSHOOT_PCT = 0.008
        bot.DYNAMIC_Z_TARGET = None
        bot.HEDGE_RATIO = 1.0
        bot.MAX_HOLD_SEC = 24 * 3600
        bot.CORR_MIN = 0.0
        print(f"[Backtest] WARNING: No profile for {sym_a}/{sym_b}. Using defaults.")

    # Apply CLI overrides
    if hedge_ratio_override is not None:
        bot.BASE_HEDGE_RATIO = hedge_ratio_override
        bot.HEDGE_RATIO = hedge_ratio_override
        print(f"[Backtest] BASE HEDGE RATIO OVERRIDE: {hedge_ratio_override} (EMA Filter Enabled)")
    if stop_loss_override is not None:
        bot.STOP_LOSS_USD = stop_loss_override
        print(f"[Backtest] STOP LOSS OVERRIDE: ${stop_loss_override:.2f}")
    if max_hold_override is not None:
        bot.MAX_HOLD_SEC = max_hold_override * 3600
        print(f"[Backtest] MAX HOLD OVERRIDE: {max_hold_override} hours")
    if z_decay:
        bot.Z_DECAY_ENABLED = True
        print(f"[Backtest] TIME-DECAYED Z-TARGET: ENABLED (relaxes from 0.0 toward entry Z over hold period)")
    else:
        bot.Z_DECAY_ENABLED = False

    # Time mocking
    fake_time = [0.0]
    _real_time = time_mod.time
    _real_ts_iso = phase23_lib.ts_iso
    _real_sleep = asyncio.sleep

    def mock_time(): return fake_time[0]
    def mock_ts_iso(): return datetime.fromtimestamp(fake_time[0], tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    async def mock_sleep(s): pass

    time_mod.time = mock_time
    phase23_lib.ts_iso = mock_ts_iso
    asyncio.sleep = mock_sleep
    
    import trader_pairs
    trader_pairs.ts_iso = mock_ts_iso

    equity_curve = [start_equity]
    timestamps = []
    tick_count = 0
    last_print_idx = -100
    last_tick_ts = 0.0
    total_files = len(selected)
    
    last_funding_hour = -1
    _funding_rates = {}
    total_funding = 0.0

    # ── Dynamic Exit Mode ──
    _dynamic_exit_z = dynamic_exit_z
    _original_overshoot = bot.OVERSHOOT_PCT
    if _dynamic_exit_z is not None:
        print(f"[Backtest] DYNAMIC EXIT MODE: Anchored Z target = {_dynamic_exit_z:+.1f}")

    print(f"\n[{mock_ts_iso()}] Starting Backtest for Pair: {sym_a} / {sym_b}")

    for file_idx, fpath in enumerate(selected):
        try:
            if file_idx - last_print_idx >= 100 or file_idx == total_files - 1:
                fname = os.path.basename(fpath)
                pct = (file_idx + 1) / total_files * 100
                eq = mock_exchange.get_total_equity()
                print(f"\r[Backtest] [{file_idx+1}/{total_files}] {pct:.0f}% | Eq=${eq:.2f} | {fname}          ", end="", flush=True)
                last_print_idx = file_idx

            with open(fpath, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        record = load_json(line)
                    except Exception:
                        continue

                    ts = record.get("ts", 0)
                    if ts <= 0: continue

                    # Evaluate at 10Hz max for faster backtests
                    if ts - last_tick_ts < 9.0: continue
                    last_tick_ts = ts
                    fake_time[0] = ts
                    tick_count += 1

                    raw_data = record.get("data", {})
                    if not raw_data: continue

                    # Extract Marks
                    mark_a, mark_b = 0.0, 0.0
                    if sym_a in raw_data:
                        d = raw_data[sym_a]
                        bids = d.get("bids", [])
                        asks = d.get("asks", [])
                        mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0 if bids and asks else 0.0
                        mark_a = d.get("mark", mid)
                        if mark_a == 0.0:
                            mark_a = mid
                        if "funding" in d: _funding_rates[sym_a] = d["funding"]
                        
                    if sym_b in raw_data:
                        d = raw_data[sym_b]
                        bids = d.get("bids", [])
                        asks = d.get("asks", [])
                        mid = (float(bids[0][0]) + float(asks[0][0])) / 2.0 if bids and asks else 0.0
                        mark_b = d.get("mark", mid)
                        if mark_b == 0.0:
                            mark_b = mid
                        if "funding" in d: _funding_rates[sym_b] = d["funding"]

                    if mark_a <= 0 or mark_b <= 0: continue
                    
                    mock_exchange.adapters[sym_a].set_mark(mark_a)
                    mock_exchange.adapters[sym_b].set_mark(mark_b)

                    # Funding settlement every 8h (use absolute epoch to avoid month-boundary resets)
                    funding_hour = int(ts) // (8 * 3600)
                    if funding_hour != last_funding_hour and last_funding_hour >= 0:
                        for sym in [sym_a, sym_b]:
                            fr = _funding_rates.get(sym, 0.0)
                            adapter = mock_exchange.adapters.get(sym)
                            if adapter and fr != 0:
                                fp = adapter.apply_funding(fr)
                                total_funding += fp
                    last_funding_hour = funding_hour

                    # Dynamic exit: update OVERSHOOT_PCT before each tick
                    if _dynamic_exit_z is not None and bot.open_position and not bot.closing_position:
                        if hasattr(bot, 'entry_std_ratio') and bot.entry_std_ratio > 1e-8:
                            bot.OVERSHOOT_PCT = _dynamic_exit_z * bot.entry_std_ratio
                    elif _dynamic_exit_z is not None and not bot.open_position:
                        bot.OVERSHOOT_PCT = _original_overshoot  # Reset for entry logic

                    # Run Trading Logic
                    await bot._trading_tick(mark_a, mark_b, ts)
                    
                    # Log equity occasionally (e.g. every minute)
                    if tick_count % 60 == 0:
                        equity_curve.append(mock_exchange.get_total_equity())
                        timestamps.append(ts)

        except Exception as e:
            import traceback
            print(f"\n[Backtest ERROR] File: {os.path.basename(fpath)}: {e}")
            traceback.print_exc()
            continue

    print()

    # Restore
    time_mod.time = _real_time
    phase23_lib.ts_iso = _real_ts_iso
    asyncio.sleep = _real_sleep

    # Close positions
    for sym in [sym_a, sym_b]:
        adapter = mock_exchange.get_adapter(sym)
        if abs(adapter.position.contracts) > 0.0001:
            await adapter.close_all()

    final_equity = mock_exchange.get_total_equity()
    pnl = final_equity - start_equity
    pnl_pct = (pnl / start_equity) * 100
    duration_h = (timestamps[-1] - timestamps[0]) / 3600.0 if len(timestamps) > 1 else 0

    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    max_dd = ((peak - equity_arr) / (peak + 1e-8)).max() if len(equity_arr) > 0 else 0

    print(f"\n{'='*60}")
    print(f"  PAIRS TRADER BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Pair:           {sym_a} vs {sym_b}")
    print(f"  Duration:       {duration_h:.1f} hours")
    print(f"  ---")
    print(f"  Start Equity:   ${start_equity:.2f}")
    print(f"  Final Equity:   ${final_equity:.2f}")
    print(f"  Total PnL:      ${pnl:+.4f} ({pnl_pct:+.2f}%)")
    print(f"  Funding P&L:    ${total_funding:+.4f}")
    print(f"  Max Drawdown:   {max_dd*100:.2f}%")
    print(f"{'='*60}\n")


async def main():
    parser = argparse.ArgumentParser(description="Pairs Trader Backtest")
    parser.add_argument("--hours", type=int, default=24)
    parser.add_argument("--start", type=str, default=None, help="Start date YYYY-MM-DD or YYYY-MM-DD_HH")
    parser.add_argument("--end", type=str, default=None, help="End date YYYY-MM-DD or YYYY-MM-DD_HH")
    parser.add_argument("--config", type=str, default="phase23.yaml")
    parser.add_argument("--pair", type=str, default="BTC/USDT:USDT,ETH/USDT:USDT",
                        help="Comma-separated pair (e.g. BTC/USDT:USDT,ETH/USDT:USDT)")
    parser.add_argument("--dynamic-exit", type=float, default=None,
                        help="Use dynamic anchored Z-score exit. Value is the Z target (e.g. -1.0, 0.0, 0.5)")
    parser.add_argument("--hedge-ratio", type=float, default=None,
                        help="Override HEDGE_RATIO (e.g. 0.5 for 2:1 ETH-biased, 1.0 for balanced)")
    parser.add_argument("--stop-loss", type=float, default=None,
                        help="Override Stop Loss USD (e.g. -8.0 for a tighter stop loss)")
    parser.add_argument("--max-hold", type=float, default=None,
                        help="Override Max Hold Time in hours (e.g. 12 or 48)")
    parser.add_argument("--z-decay", action="store_true", default=False,
                        help="Enable time-decayed Z-target (relaxes TP target as hold time increases)")
    parser.add_argument("--decay-start", type=float, default=0.5,
                        help="Fraction of hold time before decay begins (0.0 to 1.0)")
    parser.add_argument("--decay-max", type=float, default=0.25,
                        help="Maximum fraction of entry Z to relax (e.g. 0.5 = 50%%)")
    parser.add_argument("--emergency-z-stop", type=float, default=None,
                        help="Override EMERGENCY_Z_STOP for backtest")
    parser.add_argument("--emergency-cooldown", type=float, default=None,
                        help="Override EMERGENCY_COOLDOWN_SEC (in hours)")
    parser.add_argument("--notional", type=float, default=None,
                        help="Override NOTIONAL_PER_LEG for backtest")
    args = parser.parse_args()

    cfg = load_cfg(args.config)
    cfg.symbols = [s.strip() for s in args.pair.split(",")]
    cfg.z_decay_start = args.decay_start
    cfg.z_decay_max = args.decay_max
    
    if args.start:
        await run_backtest(cfg, start_date=args.start, end_date=args.end or args.start,
                          dynamic_exit_z=args.dynamic_exit, hedge_ratio_override=args.hedge_ratio,
                          stop_loss_override=args.stop_loss, max_hold_override=args.max_hold,
                          z_decay=args.z_decay, emergency_z_stop_override=args.emergency_z_stop,
                          emergency_cooldown_override=args.emergency_cooldown,
                          notional_override=args.notional)
    else:
        await run_backtest(cfg, hours=args.hours, dynamic_exit_z=args.dynamic_exit,
                          hedge_ratio_override=args.hedge_ratio, stop_loss_override=args.stop_loss,
                          max_hold_override=args.max_hold, z_decay=args.z_decay,
                          emergency_z_stop_override=args.emergency_z_stop,
                          emergency_cooldown_override=args.emergency_cooldown)

if __name__ == "__main__":
    import sys
    from datetime import datetime as _dt
    os.makedirs("logs", exist_ok=True)
    # Pair-specific + timestamped log file
    pair_arg = "btc_eth"
    for i, a in enumerate(sys.argv):
        if a == "--pair" and i + 1 < len(sys.argv):
            pair_arg = sys.argv[i+1].replace("/USDT:USDT","").replace("/","_").replace(",","_vs_").lower()
    run_ts = _dt.now().strftime("%Y%m%d_%H%M")
    sys.stdout = TeeLogger(f"logs/backtest_{pair_arg}_{run_ts}.log")
    asyncio.run(main())
