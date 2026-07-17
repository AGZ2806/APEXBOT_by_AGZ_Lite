import os
import time
import asyncio
from dataclasses import dataclass, field
from collections import deque
from typing import Dict, Optional, Deque, Tuple, Any, List
import numpy as np
import ccxt.async_support as ccxt

def ts_iso(t=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

class TeeLogger:
    def __init__(self, filepath):
        import sys
        self.terminal = sys.stdout
        self.filepath = filepath
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        self.log = open(filepath, "a", encoding="utf-8")
        self.last_was_cr = False

    def write(self, message):
        if not message:
            return
        # Prevent \r progress lines from mixing with normal trade log prints
        if self.last_was_cr and message != '\n' and not message.startswith('\r'):
            if self.terminal:
                self.terminal.write('\n')
            self.log.write('\n')
            
        if '\r' in message and not message.endswith('\n'):
            self.last_was_cr = True
        elif '\n' in message:
            self.last_was_cr = False
            
        if self.terminal:
            self.terminal.write(message)
        self.log.write(message)
        self.log.flush()

    def flush(self):
        if self.terminal:
            self.terminal.flush()
        self.log.flush()

    def __enter__(self):
        import sys
        sys.stdout = self
        sys.stderr = self
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        import sys
        sys.stdout = self.terminal
        sys.stderr = self.terminal
        self.log.close()

@dataclass
class BotConfig:
    api_key: str
    api_secret: str
    symbols: list
    context_symbols: list
    testnet: bool
    start_balance: float

    usdt_per_level: float
    auto_scale_qty: bool
    max_usdt_per_level: float
    min_notional_usdt: float
    min_profit_pct: float

    dd_soft: float
    dd_hard: float
    lambda_dd: float
    lambda_trade: float

    leverage: int
    prometheus_port: int
    loop_interval_sec: float
    save_interval_sec: int

    rebuild_interval_sec: float
    price_dev_limit: float


    # Execution extras
    max_pos_notional: float = 500.0
    # Phase 46: Position-Aware Grid
    inventory_gate_pct: float = 0.6
    max_loss_per_stop_usdt: float = 0.15


    # Phase 23.3: Low Invasiveness Fixes
    slippage_gate_pct: float = 0.002
    symbol_configs: Dict[str, Dict[str, float]] = field(default_factory=dict)

    # Phase 23.4
    shock_drawdown_limit: float = 0.05
    trend_ema_alpha: float = 0.01

    # Phase 23.5
    max_global_leverage: float = 8.0
    max_corr_exposure: float = 0.5
    corr_threshold: float = 0.75


    # Phase 37: Volatility Scaled Exits (Z-Score)
    # These replace the fixed percentages above if used in trader.py
    trailing_stop_activation_sigma: float = 1.0 # 1.0x Volatility to activate
    trailing_stop_callback_sigma: float = 0.3   # 0.3x Volatility to close
    
    trailing_stop_callback_ratio: float = 0.10
    trailing_stop_min_callback: float = 0.0005
    
    # Raw Config for extensions
    raw_config: Dict[str, Any] = field(default_factory=dict)
    
    # Phase 35: Stop Loss
    stop_loss_pct: float = 0.005 # Base 0.5%
    stop_loss_sigma: float = 5.0 # 5 Sigma (Vol 0.1% -> Stop 0.5%)
    
    # Runtime Config
    ws_seed_snapshot: bool = True


def load_cfg(path: str = "user_settings.yaml") -> BotConfig:
    import os
    import yaml
    try:
        from dotenv import load_dotenv
        load_dotenv("key.env")
    except ImportError:
        pass
    
    d = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = yaml.safe_load(f) or {}
        except Exception as e:
            print(f"Error loading {path}: {e}")

    api_key = os.getenv("BINANCE_API_KEY", "")
    api_secret = os.getenv("BINANCE_API_SECRET", "")

    user_pairs_trading = d.get("pairs_trading", {})
    
    return BotConfig(
        api_key=api_key,
        api_secret=api_secret,
        symbols=d.get("symbols", ["BTC/USDT:USDT", "ETH/USDT:USDT"]),
        context_symbols=["BTC/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "ETH/USDT:USDT"],
        testnet=False,
        start_balance=150.0,

        usdt_per_level=5.0,
        auto_scale_qty=False,
        max_usdt_per_level=1000.0,
        max_pos_notional=55.0,
        min_notional_usdt=5.0,
        min_profit_pct=0.001,

        dd_soft=0.03,
        dd_hard=0.05,
        lambda_dd=2.0,
        lambda_trade=1.0,

        leverage=5,
        prometheus_port=8000,
        loop_interval_sec=1.0,
        save_interval_sec=1800,

        rebuild_interval_sec=25.0,
        price_dev_limit=0.035,
        
        slippage_gate_pct=0.002,
        symbol_configs={
            "SOL/USDT:USDT": {"min_notional": 6.0},
            "ETH/USDT:USDT": {"min_notional": 21.0}
        },

        shock_drawdown_limit=0.05,
        trend_ema_alpha=0.01,

        max_global_leverage=5.0,
        max_corr_exposure=0.5,
        corr_threshold=0.75,

        trailing_stop_callback_ratio=0.10,
        trailing_stop_min_callback=0.0005,
        
        raw_config={"pairs_trading": {
            "notional_per_leg": float(user_pairs_trading.get("notional_per_leg", 160.0)),
            "z_entry_threshold": float(user_pairs_trading.get("z_entry_threshold", 4.0))
        }},
        stop_loss_pct=0.005,
        ws_seed_snapshot=True,
        inventory_gate_pct=0.6,
        max_loss_per_stop_usdt=0.15,
    )


class PerformanceTracker:
    def __init__(self, maxlen_steps: int, loop_interval_sec: float):
        self.equity_hist: Deque[float] = deque(maxlen=maxlen_steps)
        self.mark_hist: Deque[float] = deque(maxlen=maxlen_steps)
        self.ret_hist: Deque[float] = deque(maxlen=maxlen_steps)
        self.loop_interval_sec = loop_interval_sec
        self.peak_equity: float = -1e18

    def update(self, equity: float, mark: float) -> Dict[str, float]:
        if self.equity_hist:
            prev_eq = self.equity_hist[-1]
            r = (equity - prev_eq) / (abs(prev_eq) + 1e-8)
        else:
            r = 0.0

        self.equity_hist.append(equity)
        self.mark_hist.append(mark)
        self.ret_hist.append(r)

        if equity > self.peak_equity:
            self.peak_equity = equity
        dd = (self.peak_equity - equity) / (abs(self.peak_equity) + 1e-8) if self.peak_equity > 0 else 0.0

        rets = np.array(self.ret_hist, dtype=np.float64)
        vol = float(np.std(rets)) if len(rets) > 5 else 0.0
        mean = float(np.mean(rets)) if len(rets) > 5 else 0.0
        steps_per_day = 86400.0 / max(0.5, self.loop_interval_sec)
        sharpe = (mean / (vol + 1e-8)) * np.sqrt(steps_per_day) if vol > 0 else 0.0

        pm = np.array(self.mark_hist, dtype=np.float64)
        if len(pm) > 25:
            pret = np.diff(np.log(pm + 1e-8))
            sig_s = float(np.std(pret[-20:])) if len(pret) >= 20 else float(np.std(pret))
            sig_l = float(np.std(pret[-120:])) if len(pret) >= 120 else float(np.std(pret))
            vol_ratio = sig_s / (sig_l + 1e-8) if sig_l > 0 else 0.0
        else:
            sig_s = sig_l = vol_ratio = 0.0

        quiet = 1.0 if vol_ratio < 0.8 else 0.0
        highvol = 1.0 if vol_ratio > 1.25 else 0.0

        return {
            "ret": r, "vol": vol, "sharpe": float(sharpe), "dd": float(dd),
            "sig_s": sig_s, "sig_l": sig_l, "vol_ratio": float(vol_ratio),
            "quiet": quiet, "highvol": highvol
        }


from abc import ABC, abstractmethod

class ExchangeAdapter(ABC):
    def __init__(self, cfg: BotConfig, symbol: str):
        self.cfg = cfg
        self.symbol = symbol
        self.maker_fee = 0.0002
        self.taker_fee = 0.0004
        self.min_notional = float(cfg.min_notional_usdt)

    @abstractmethod
    async def init(self):
        pass

    @abstractmethod
    def should_backoff(self) -> bool:
        pass

    @abstractmethod
    async def cancel_all(self):
        pass

    @abstractmethod
    async def place_order(self, side: str, price: float, qty: float, reduce_only: bool = False) -> int:
        pass

    @abstractmethod
    async def fetch_open_orders(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str):
        pass

    @abstractmethod
    async def fetch_position(self) -> Optional[Dict[str, Any]]:
        pass

    @abstractmethod
    async def sync_time(self):
        pass

class BinanceAdapter(ExchangeAdapter):
    def __init__(self, cfg: BotConfig, symbol: str):
        super().__init__(cfg, symbol)
        self.exchange = ccxt.binanceusdm({
            "apiKey": os.getenv("BINANCE_API_KEY", cfg.api_key),
            "secret": os.getenv("BINANCE_API_SECRET", cfg.api_secret),
            "enableRateLimit": True,
            "timeout": 20000,
            "options": {"defaultType": "future", "adjustForTimeDifference": True, "recvWindow": 20000, "broker": {"future": "x-XSY2ZGS8", "spot": "x-XSY2ZGS8", "swap": "x-XSY2ZGS8", "linear": "x-XSY2ZGS8", "delivery": "x-XSY2ZGS8"}},
        })
        if cfg.testnet:
            self.exchange.set_sandbox_mode(True)

        self._backoff_until = 0.0
        self._inited = False
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        
        # Override min_notional from symbol config if present
        sym_cfg = cfg.symbol_configs.get(symbol, {})
        if "min_notional" in sym_cfg:
            self.min_notional = float(sym_cfg["min_notional"])

    async def init(self):
        if self._inited:
            return
            
        async with self._init_lock:
            if self._inited:
                return
                
            await self.exchange.load_markets()
        try:
            await self.exchange.set_leverage(self.cfg.leverage, self.symbol)
        except Exception as e:
            print(f"[{ts_iso()}] [INIT] Set Leverage failed for {self.symbol}: {e}")

        try:
            m = self.exchange.market(self.symbol)
            min_cost = m.get("limits", {}).get("cost", {}).get("min")
            if min_cost:
                # User Request: Prioritize API value over config, but keep safety net
                self.min_notional = max(self.min_notional, float(min_cost))
                print(f"[{ts_iso()}] [INIT] {self.symbol} Min Notional set to {self.min_notional} (API: {min_cost})")
            # Also fetch min_qty (minimum amount precision) to guard taker fallback orders
            min_qty = m.get("limits", {}).get("amount", {}).get("min")
            if min_qty:
                self.min_qty = float(min_qty)
                print(f"[{ts_iso()}] [INIT] {self.symbol} Min Qty set to {self.min_qty} (from market info)")
        except Exception:
            pass
            
        # Fetch Fees
        try:
            fees = await self.exchange.fetch_trading_fees()
            if isinstance(fees, dict):
                sym_fee = fees.get(self.symbol) or fees.get("ETH/USDT:USDT") # Fallback
                if sym_fee:
                    self.maker_fee = float(sym_fee.get("maker", self.maker_fee))
                    self.taker_fee = float(sym_fee.get("taker", self.taker_fee))
                    print(f"[{ts_iso()}] Fees for {self.symbol}: Maker={self.maker_fee:.5f} Taker={self.taker_fee:.5f}")
        except Exception as e:
            print(f"[{ts_iso()}] Fee fetch failed for {self.symbol}: {e}")
            
        self._inited = True

    async def sync_time(self):
        """
        Sync local clock with Binance server time.
        Injects timeDifference into the ccxt exchange so all future requests
        use corrected timestamps — no OS-level time sync required.
        Uses a short 5s timeout and retries once silently before logging.
        """
        original_timeout = self.exchange.timeout
        self.exchange.timeout = 5000  # Shorter timeout for time sync (public endpoint)
        try:
            for attempt in range(2):
                try:
                    drift_ms = await self.exchange.load_time_difference()
                    if abs(drift_ms) > 2000:
                        print(f"[{ts_iso()}] [TIME SYNC] LARGE drift: {drift_ms:+d}ms. Auto-corrected.")
                    elif abs(drift_ms) > 500:
                        print(f"[{ts_iso()}] [TIME SYNC] Clock drift: {drift_ms:+d}ms. Auto-corrected.")
                    return  # Success — exit
                except Exception:
                    if attempt == 0:
                        await asyncio.sleep(1.0)  # Brief pause before retry
                        continue
                    # Both attempts failed — log once (type only, no URL dump)
                    pass  # Silently skip — last sync values still in effect
        finally:
            self.exchange.timeout = original_timeout  # Always restore

    def should_backoff(self) -> bool:
        return time.time() < self._backoff_until

    def _set_backoff(self, sec: float = 5.0):
        self._backoff_until = time.time() + sec

    async def cancel_all(self):
        async with self._lock:
            try:
                await self.exchange.cancel_all_orders(self.symbol)
            except Exception as e:
                print(f"[{ts_iso()}] cancel_all failed for {self.symbol}: {e}")
                self._set_backoff(5.0)

    async def place_order(self, side: str, price: float, qty: float, reduce_only: bool = False, order_type: str = "limit", post_only: bool = True) -> int:
        async with self._lock:
            try:
                await self.init()
                
                # For Limit orders, ensure price is valid
                if order_type == "limit":
                    price = float(max(price, 1e-6))
                    if not reduce_only and qty * price < self.min_notional:
                        # Fix (Audit Phase 32): Don't force entry on tiny signals. Skip instead.
                        # This prevents "Forced Entry" where $1 signal -> $6 trade.
                        return 0
                
                # Debug Notional
                if price and price > 0:
                    notional = qty * price
                    if notional < 21.0:
                        print(f"[{ts_iso()}] [DEBUG] Placing Order {self.symbol} {side} {qty} @ {price} = {notional:.2f} (Min: {self.min_notional})")
                
                params = {"reduceOnly": reduce_only}
                if order_type == "limit":
                    params["timeInForce"] = "GTC"
                    if post_only:
                        params["postOnly"] = True
                
                # For Market orders, price should be None
                final_price = price if order_type == "limit" else None
                
                await self.exchange.create_order(self.symbol, order_type, side, qty, final_price, params)
                return 1
            except Exception as e:
                print(f"[{ts_iso()}] Order failed: {repr(e)}")
                self._set_backoff(8.0)
                return 0

    async def fetch_open_orders(self) -> List[Dict[str, Any]]:
        # Let exception propagate so Trader knows API failed
        return await self.exchange.fetch_open_orders(self.symbol)

    async def cancel_order(self, order_id: str):
        async with self._lock:
            try:
                await self.exchange.cancel_order(order_id, self.symbol)
            except Exception as e:
                print(f"[{ts_iso()}] [WARN] Cancel failed {order_id}: {e}")

    async def fetch_position(self) -> Optional[Dict[str, Any]]:
        # Let exception propagate
        positions = await self.exchange.fetch_positions([self.symbol])
        return positions[0] if positions else None


class MemoryManager:
    def __init__(self, interval_sec: float = 3600.0):
        self.interval = interval_sec
        self.last_clean_ts = time.time()
        import gc
        self.gc = gc

    def check(self):
        now = time.time()
        if now - self.last_clean_ts > self.interval:
            self.clean()
            self.last_clean_ts = now

    def clean(self):
        # Force garbage collection
        n = self.gc.collect()

        print(f"[{ts_iso()}] [MEM] GC collected {n} objects. VRAM cache cleared.")


class ExchangeManager:
    def __init__(self, cfg: BotConfig):
        self.cfg = cfg
        self.adapters: Dict[str, ExchangeAdapter] = {}

    def get_adapter(self, symbol: str) -> ExchangeAdapter:
        if symbol not in self.adapters:
            # For now, default to BinanceAdapter. 
            # In future, this logic can check config to decide which adapter to use (Binance, Bybit, etc.)
            self.adapters[symbol] = BinanceAdapter(self.cfg, symbol)
        return self.adapters[symbol]

    async def close_all(self):
        for adapter in self.adapters.values():
            if hasattr(adapter, "exchange"):
                await adapter.exchange.close()

