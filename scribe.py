"""
Scribe — Data Collection Service
Records market data (L2 books, trades, marks) to JSONL files
and pushes live state to Shared Memory for the Pairs trader.

Features:
  - Hot-reload phase23.yaml without restart (detects symbol changes, reconnects WS)
  - Atomic SHM writes + threaded disk I/O (non-blocking)
  - Hourly JSONL file rotation
  - TeeLogger: all stdout mirrored to logs/scribe.log
"""
import asyncio
import os
import sys
import time
import traceback
import json
from phase23_lib import load_cfg, ts_iso, TeeLogger
from concurrent.futures import ThreadPoolExecutor
from phase23_data_collector import Phase23DataCollector
from phase23_shm import SharedMemoryManager

CONFIG_PATH = "user_settings.yaml" if os.path.exists("user_settings.yaml") else "phase23.yaml"
CONFIG_CHECK_INTERVAL = 60.0   # How often to poll YAML mtime (seconds)
HTF_ENABLED = False             # HTF REST candle polling — unused by PairsTrader, keep off


class Recorder:
    def __init__(self, data_dir, cli_mode=False):
        self.history_dir = os.path.join(data_dir, "history")
        os.makedirs(self.history_dir, exist_ok=True)
        self.current_file = None
        self.current_hour = -1
        self.cli_mode = cli_mode

    def _get_filepath(self, ts):
        t = time.gmtime(ts)
        hour = t.tm_hour
        if hour != self.current_hour:
            self.current_hour = hour
            date_str = time.strftime("%Y-%m-%d_%H", t)
            filename = f"scribe_{date_str}.jsonl"
            if self.current_file:
                self.current_file.close()
            self.current_file = open(
                os.path.join(self.history_dir, filename), "a", encoding="utf-8"
            )
            if not self.cli_mode:
                self._prune_history()
        return self.current_file

    def _prune_history(self):
        try:
            cutoff = time.time() - (14 * 24 * 3600)
            for f in os.listdir(self.history_dir):
                if f.startswith("scribe_") and f.endswith(".jsonl"):
                    filepath = os.path.join(self.history_dir, f)
                    if os.path.getmtime(filepath) < cutoff:
                        os.remove(filepath)
                        print(f"[{ts_iso()}] Pruned old history file: {f}")
        except Exception as e:
            print(f"[{ts_iso()}] Error pruning history: {e}")

    def write(self, frame):
        try:
            f = self._get_filepath(frame.ts)
            record = {"ts": frame.ts, "data": {}}
            for sym, d in frame.data.items():
                record["data"][sym] = {
                    "bids": d["book"]["bids"],
                    "asks": d["book"]["asks"],
                    "trades": d["trades"],
                    "mark": d["mark"].get("mark_price", 0.0),
                    "funding": d["mark"].get("funding_rate", 0.0),
                    "htf": d.get("htf", {}),
                }
            f.write(json.dumps(record) + "\n")
            f.flush()
        except Exception as e:
            print(f"[{ts_iso()}] Recorder Error: {e}")


def _load_config():
    """Load YAML config and return (cfg, mtime)."""
    cfg = load_cfg(CONFIG_PATH)
    mtime = os.path.getmtime(CONFIG_PATH)
    return cfg, mtime


async def _build_collector(cfg) -> Phase23DataCollector:
    """Instantiate and connect a fresh Phase23DataCollector from current config."""
    collector = Phase23DataCollector(
        api_key=cfg.api_key,
        api_secret=cfg.api_secret,
        symbols=cfg.symbols,
        context_symbols=cfg.context_symbols,
        testnet=cfg.testnet,
        leverage=cfg.leverage,
        frame_interval_ms=100,
        depth_levels=20,
        ws_seed_snapshot=True,
    )
    # Disable HTF loop at source level if not needed
    if not HTF_ENABLED:
        collector._run_htf_loop = lambda: asyncio.sleep(1e9)

    await collector.connect_ws()
    all_syms = sorted(set(cfg.symbols + cfg.context_symbols))
    print(f"[{ts_iso()}] Collector connected for symbols: {all_syms}")
    return collector


async def main(cli_mode=False):
    print(f"[{ts_iso()}] Starting Scribe Service...")

    # ── Load initial config ──────────────────────────────────────────
    cfg, last_config_mtime = _load_config()
    last_config_check = time.time()

    # ── Initialize components ────────────────────────────────────────
    data_dir = os.path.join(os.getcwd(), "data")
    os.makedirs(data_dir, exist_ok=True)

    collector = await _build_collector(cfg)
    recorder = Recorder(data_dir, cli_mode=cli_mode)

    shm_mgr = SharedMemoryManager(is_writer=True)
    print(f"[{ts_iso()}] Shared Memory initialized (Writer).")
    print(f"[{ts_iso()}] Scribe initialized. Collecting {len(cfg.symbols + cfg.context_symbols)} symbols.")

    # Wait for initial frames
    while len(collector.frames) < 10:
        await asyncio.sleep(1.0)
        print(f"[{ts_iso()}] Frames collected: {len(collector.frames)}")

    last_ts = 0.0
    io_pool = ThreadPoolExecutor(max_workers=2)
    frame_count = 0
    gap_count = 0
    dropped_count = 0
    last_heartbeat = time.time()
    heartbeat_interval = 60.0

    print(f"[{ts_iso()}] Scribe ready. Recording + SHM active.")

    try:
        while True:
            loop_start = time.time()
            try:
                now = time.time()

                # ── Hot-Reload Config Check ──────────────────────────
                if now - last_config_check >= CONFIG_CHECK_INTERVAL:
                    last_config_check = now
                    try:
                        new_mtime = os.path.getmtime(CONFIG_PATH)
                        if new_mtime != last_config_mtime:
                            print(f"[{ts_iso()}] [HOT-RELOAD] Config change detected. Reloading...")
                            new_cfg, new_mtime = _load_config()
                            old_syms = set(cfg.symbols + cfg.context_symbols)
                            new_syms = set(new_cfg.symbols + new_cfg.context_symbols)

                            if old_syms != new_syms:
                                print(f"[{ts_iso()}] [HOT-RELOAD] Symbol set changed: {old_syms} → {new_syms}. Reconnecting WS...")
                                await collector.stop()
                                collector = await _build_collector(new_cfg)
                                # Wait for fresh frames
                                while len(collector.frames) < 5:
                                    await asyncio.sleep(0.5)
                            else:
                                print(f"[{ts_iso()}] [HOT-RELOAD] Config reloaded (no symbol change — no WS reconnect needed).")

                            cfg = new_cfg
                            last_config_mtime = new_mtime
                    except Exception as e:
                        print(f"[{ts_iso()}] [HOT-RELOAD] Error reading config: {e}")

                # ── Frame Processing ─────────────────────────────────
                frames = collector.get_recent_frames(120)

                if frames:
                    newest = frames[-1]
                    if newest.ts > last_ts:
                        if last_ts > 0 and newest.ts - last_ts > 5.0:
                            gap_count += 1
                        last_ts = newest.ts
                        frame_count += 1

                        # 1. SHM — must never block on disk I/O
                        state = {
                            "ts": newest.ts,
                            "marks": collector.marks,
                            "books": {
                                s: newest.data[s]["book"]
                                for s in cfg.symbols if s in newest.data
                            },
                            "funding": {
                                s: collector.marks.get(s, {}).get("funding_rate", 0.0)
                                for s in cfg.symbols
                            },
                        }
                        shm_mgr.write(state)

                        # 2. JSONL disk write (threaded)
                        if not io_pool._shutdown:
                            io_pool.submit(recorder.write, newest)
                        else:
                            dropped_count += 1

                # ── Heartbeat ────────────────────────────────────────
                if now - last_heartbeat > heartbeat_interval:
                    elapsed_hb = now - last_heartbeat
                    fps = frame_count / elapsed_hb if elapsed_hb > 0 else 0
                    drop_str = f" | Dropped: {dropped_count}" if dropped_count > 0 else ""
                    print(f"[{ts_iso()}] [HEARTBEAT] {frame_count} frames ({fps:.1f}/s) | Gaps>5s: {gap_count}{drop_str}")
                    frame_count = 0
                    gap_count = 0
                    dropped_count = 0
                    last_heartbeat = now

            except Exception as e:
                print(f"[{ts_iso()}] Scribe Error: {e}")
                traceback.print_exc()

            elapsed = time.time() - loop_start
            sleep_time = max(0.0, cfg.loop_interval_sec - elapsed)
            await asyncio.sleep(sleep_time)

    finally:
        print(f"[{ts_iso()}] Scribe shutting down...")
        await collector.stop()
        io_pool.shutdown(wait=True)
        shm_mgr.cleanup()
        print(f"[{ts_iso()}] Shutdown complete.")


if __name__ == "__main__":
    sys.stdout = TeeLogger("logs/scribe.log")
    try:
        asyncio.run(main(cli_mode=True))
    except KeyboardInterrupt:
        print(f"[{ts_iso()}] Scribe stopped by user.")
