import os
import sys
import time
import json
import threading
import subprocess
import requests
import yaml
import customtkinter as ctk
from datetime import datetime
import multiprocessing
import webbrowser

# Matplotlib integration
import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np

CURRENT_VERSION = "3.0.6"

# Force Current Working Directory to the executable's folder
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)

from scribe import main as scribe_main
from trader_pairs import main as trader_main

def run_scribe():
    import asyncio
    import sys
    import traceback
    from phase23_lib import TeeLogger
    import os
    os.makedirs("logs", exist_ok=True)
    sys.stdout = TeeLogger("logs/scribe.log")
    sys.stderr = sys.stdout
    try:
        from scribe import main as scribe_main
        asyncio.run(scribe_main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"CRITICAL ERROR IN SCRIBE: {e}")
        traceback.print_exc()

class ObfuscatedLogger:
    def __init__(self, filename, queue=None):
        import sys
        self.filename = filename
        self.queue = queue
        self.terminal = sys.stdout

    def write(self, message):
        import zlib, base64, time
        self.terminal.write(message)
        if not message.strip():
            return
        if self.queue:
            try:
                self.queue.put_nowait(message)
            except:
                pass
        try:
            with open(self.filename, 'a') as f:
                msg_with_time = f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}"
                compressed = zlib.compress(msg_with_time.encode('utf-8'))
                b64 = base64.b64encode(compressed).decode('utf-8')
                f.write(b64 + "\n")
        except:
            pass
            
    def flush(self):
        self.terminal.flush()

def run_trader(log_queue=None):
    import asyncio
    import sys
    import traceback
    import os
    
    log_path = os.path.join(APP_DIR, "system.apexlog")
    sys.stdout = ObfuscatedLogger(log_path, log_queue)
    sys.stderr = sys.stdout
    try:
        from trader_pairs import main as trader_main
        asyncio.run(trader_main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"CRITICAL ERROR IN TRADER: {e}")
        traceback.print_exc()

def bootstrap_configs():
    import shutil
    
    # Generate key.env regardless of frozen status (Fix #2)
    if not os.path.exists("key.env"):
        with open("key.env", "w") as f:
            f.write("BINANCE_API_KEY=\nBINANCE_API_SECRET=\n")

    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        default_yaml = os.path.join(meipass, "user_settings.yaml")
        if not os.path.exists("user_settings.yaml") and os.path.exists(default_yaml):
            try:
                shutil.copy2(default_yaml, "user_settings.yaml")
            except Exception:
                pass

bootstrap_configs()

# Path definitions
CONFIG_PATH = "user_settings.yaml"
ENV_PATH = "key.env"
TRADER_LOG = "logs/trader_pairs.log"

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class PairsTraderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Apex Crypto Bot by AGZ")
        self.geometry("1200x850")
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

        # State Variables
        self.latency_ms = 0
        self.is_unstable = False
        self.bot_processes = []
        self.bot_running = False
        self.live_z_score = "N/A"
        self.live_div = "N/A"
        self.live_btc = "N/A"
        self.live_eth = "N/A"
        self.wallet_advice = "No wallet data yet..."
        self.live_pnl = "N/A"
        self.live_mean_ratio = "N/A"
        self.live_std_ratio = "N/A"
        self.live_current_ratio = "N/A"
        self.live_anchored_z = "N/A"
        self.live_status_shm = "STOPPED"

        # Theme Colors
        self.color_bg = "#121212"
        self.color_card = "#1E1E1E"
        self.color_cyan = "#00E5FF"
        self.color_green = "#00FF88"
        self.color_red = "#FF3366"
        self.color_text_muted = "#888888"

        # Setup Grid Layout
        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=1)

        self._build_sidebar()
        self._build_dashboard()
        self._build_settings()
        self._build_apikeys()
        self._build_history()
        self._build_app_settings()
        self._build_help()

        self.select_frame("dashboard")

        self.load_env_config()
        self.load_yaml_config()
        
        self.fetch_tickers_news_loop()
        self.load_env_config()

        # Start Background Threads
        threading.Thread(target=self.ping_loop, daemon=True).start()
        threading.Thread(target=self.shm_tail_loop, daemon=True).start()
        
        # Periodic UI update
        self.update_ui_loop()
        self.update_chart_loop()

    def on_closing(self):
        self.stop_bot()
        self.destroy()
        import sys
        sys.exit(0)

    def _build_sidebar(self):
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color="#181818")
        self.sidebar.grid(row=0, column=0, sticky="nsew")
        self.sidebar.grid_rowconfigure(9, weight=1)

        # Branding
        brand_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=20, pady=(20, 30), sticky="w")
        ctk.CTkLabel(brand_frame, text="▲", font=ctk.CTkFont(size=24, weight="bold"), text_color=self.color_cyan).pack(side="left", padx=(0,10))
        ctk.CTkLabel(brand_frame, text="APEX", font=ctk.CTkFont(size=20, weight="bold"), text_color="white").pack(side="left")

        # Menu Buttons (Stylized)
        btn_kwargs = {"fg_color": "transparent", "text_color": "white", "anchor": "w", "font": ctk.CTkFont(size=14)}
        
        self.btn_dash = ctk.CTkButton(self.sidebar, text="Dashboard", fg_color="#2A2A2A", text_color=self.color_cyan, anchor="w", font=ctk.CTkFont(size=14, weight="bold"), command=lambda: self.select_frame("dashboard"))
        self.btn_dash.grid(row=1, column=0, padx=20, pady=5, sticky="ew")

        self.btn_settings = ctk.CTkButton(self.sidebar, text="Strategy & Config", command=lambda: self.select_frame("settings"), **btn_kwargs)
        self.btn_settings.grid(row=2, column=0, padx=20, pady=5, sticky="ew")

        self.btn_keys = ctk.CTkButton(self.sidebar, text="API Keys", command=lambda: self.select_frame("keys"), **btn_kwargs)
        self.btn_keys.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        self.btn_history = ctk.CTkButton(self.sidebar, text="History", command=lambda: self.select_frame("history"), **btn_kwargs)
        self.btn_history.grid(row=4, column=0, padx=20, pady=5, sticky="ew")

        self.btn_settings_app = ctk.CTkButton(self.sidebar, text="Settings", command=lambda: self.select_frame("app_settings"), **btn_kwargs)
        self.btn_settings_app.grid(row=5, column=0, padx=20, pady=5, sticky="ew")

        self.btn_help = ctk.CTkButton(self.sidebar, text="Help", command=lambda: self.select_frame("help"), **btn_kwargs)
        self.btn_help.grid(row=6, column=0, padx=20, pady=5, sticky="ew")

    def _build_dashboard(self):
        import collections
        self.frm_dash = ctk.CTkFrame(self, corner_radius=0, fg_color=self.color_bg)
        self.frm_dash.grid_columnconfigure((0, 1), weight=1)
        self.frm_dash.grid_rowconfigure(4, weight=1)

        card_kwargs = {"fg_color": self.color_card, "corner_radius": 10}

        # --- Row 0: Header & Clock ---
        header_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        header_frame.grid(row=0, column=0, columnspan=2, pady=(20, 10), sticky="ew", padx=30)
        ctk.CTkLabel(header_frame, text="Dashboard - Real-Time Stats", font=ctk.CTkFont(family="Inter", size=24, weight="bold"), text_color="#FFFFFF").pack(side="left")
        
        # News Marquee
        self.lbl_news = ctk.CTkLabel(header_frame, text="Loading Live Crypto News...", font=ctk.CTkFont(family="Inter", size=16, slant="italic"), text_color=self.color_cyan)
        self.lbl_news.pack(side="left", padx=30)
        
        self.btn_panic = ctk.CTkButton(header_frame, text="🚨 PANIC CLOSE ALL", fg_color="#AA0000", hover_color="#FF0000", font=ctk.CTkFont(weight="bold"), command=self.panic_action)
        self.btn_panic.pack(side="left", padx=20)
        
        self.lbl_clock = ctk.CTkLabel(header_frame, text="--:-- --\n--- --, ----", font=ctk.CTkFont(family="Inter", size=12), text_color=self.color_text_muted, justify="right")
        self.lbl_clock.pack(side="right")
        
        self.btn_update = ctk.CTkButton(header_frame, text="⚠️ Update Available!", fg_color="#FFA500", hover_color="#FF8C00", text_color="black", font=ctk.CTkFont(weight="bold"), command=lambda: webbrowser.open("https://apexbotagz.com/"))
        # Don't pack yet, wait for check

        # --- Row 1: Market Overview & Account Balance ---
        row1_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        row1_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=30, pady=5)
        row1_frame.grid_columnconfigure((0, 1), weight=1)

        panel_market = ctk.CTkFrame(row1_frame, **card_kwargs)
        panel_market.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        ctk.CTkLabel(panel_market, text="Market Overview", font=ctk.CTkFont(family="Inter", size=16), text_color="white").pack(anchor="w", padx=15, pady=(10, 5))
        
        market_inner = ctk.CTkFrame(panel_market, fg_color="transparent")
        market_inner.pack(fill="x", padx=15, pady=(0, 10))
        market_inner.grid_columnconfigure((0,1), weight=1)
        
        self.val_mark_a = ctk.CTkLabel(market_inner, text="Sym A\n$---", font=ctk.CTkFont(family="Inter", size=14), justify="left")
        self.val_mark_a.grid(row=0, column=0, sticky="w")
        self.val_mark_b = ctk.CTkLabel(market_inner, text="Sym B\n$---", font=ctk.CTkFont(family="Inter", size=14), justify="left")
        self.val_mark_b.grid(row=0, column=1, sticky="w")
        
        # Tickers for SOL and BNB
        self.val_mark_sol = ctk.CTkLabel(market_inner, text="SOL\n$---", font=ctk.CTkFont(family="Inter", size=14), justify="left")
        self.val_mark_sol.grid(row=0, column=2, sticky="w", padx=10)
        self.val_mark_bnb = ctk.CTkLabel(market_inner, text="BNB\n$---", font=ctk.CTkFont(family="Inter", size=14), justify="left")
        self.val_mark_bnb.grid(row=0, column=3, sticky="w")

        panel_account = ctk.CTkFrame(row1_frame, **card_kwargs)
        panel_account.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        self.lbl_balance = ctk.CTkLabel(panel_account, text="Account Balance", font=ctk.CTkFont(family="Inter", size=14, weight="bold"), text_color="#A0A0A0")
        self.lbl_balance.pack(anchor="w", padx=15, pady=(10, 5))
        self.val_balance = ctk.CTkLabel(panel_account, text="Scanning...", font=ctk.CTkFont(family="Inter", size=16, weight="bold"), text_color="white")
        self.val_balance.pack(anchor="w", padx=15, pady=(0, 10))

        # --- Row 2: Core Stats ---
        row2_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        row2_frame.grid(row=2, column=0, columnspan=2, sticky="ew", padx=30, pady=10)
        row2_frame.grid_columnconfigure((0, 1, 2, 3), weight=1)

        # Z-Score
        panel_z = ctk.CTkFrame(row2_frame, fg_color=self.color_card, corner_radius=10, border_width=2, border_color=self.color_cyan)
        panel_z.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        ctk.CTkLabel(panel_z, text="Live Z-Score:", font=ctk.CTkFont(family="Inter", size=14), text_color="white").pack(anchor="w", padx=15, pady=(10, 0))
        self.val_z = ctk.CTkLabel(panel_z, text="N/A", font=ctk.CTkFont(family="Inter", size=36, weight="bold"), text_color=self.color_cyan)
        self.val_z.pack(pady=(5, 5))

        # Anchored Z
        panel_az = ctk.CTkFrame(row2_frame, fg_color=self.color_card, corner_radius=10, border_width=2, border_color="#AA00FF")
        panel_az.grid(row=0, column=1, sticky="nsew", padx=5)
        ctk.CTkLabel(panel_az, text="Anchored Z:", font=ctk.CTkFont(family="Inter", size=14), text_color="white").pack(anchor="w", padx=15, pady=(10, 0))
        self.val_az = ctk.CTkLabel(panel_az, text="N/A", font=ctk.CTkFont(family="Inter", size=36, weight="bold"), text_color="#AA00FF")
        self.val_az.pack(pady=(5, 5))

        # PnL
        panel_pnl = ctk.CTkFrame(row2_frame, **card_kwargs)
        panel_pnl.grid(row=0, column=2, sticky="nsew", padx=5)
        ctk.CTkLabel(panel_pnl, text="Estimated PnL:", font=ctk.CTkFont(family="Inter", size=14), text_color="white").pack(anchor="w", padx=15, pady=(10, 0))
        self.val_pnl = ctk.CTkLabel(panel_pnl, text="N/A", font=ctk.CTkFont(family="Inter", size=36, weight="bold"), text_color=self.color_green)
        self.val_pnl.pack(pady=(10, 5))

        # Status
        panel_pos = ctk.CTkFrame(row2_frame, **card_kwargs)
        panel_pos.grid(row=0, column=3, sticky="nsew", padx=(5, 0))
        ctk.CTkLabel(panel_pos, text="Status:", font=ctk.CTkFont(family="Inter", size=14), text_color="white").pack(anchor="w", padx=15, pady=(10, 0))
        self.lbl_z_sub = ctk.CTkLabel(panel_pos, text="Bot Offline", font=ctk.CTkFont(family="Inter", size=16, weight="bold"), text_color=self.color_text_muted, justify="left")
        self.lbl_z_sub.pack(anchor="w", padx=15, pady=(10, 5))

        # --- Row 3: Controls ---
        row3_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        row3_frame.grid(row=3, column=0, columnspan=2, sticky="ew", padx=30, pady=5)
        row3_frame.grid_columnconfigure(0, weight=1)

        panel_controls = ctk.CTkFrame(row3_frame, fg_color="transparent")
        panel_controls.grid(row=0, column=0, sticky="nsew")
        panel_controls.grid_columnconfigure(0, weight=1)
        
        self.btn_toggle = ctk.CTkButton(panel_controls, text="SHUTDOWN (Click to Start)", font=ctk.CTkFont(size=18, weight="bold"), 
                                       fg_color="transparent", border_width=2, border_color=self.color_red, text_color=self.color_red, hover_color="#330011",
                                       height=60, command=self.toggle_bot)
        self.btn_toggle.grid(row=0, column=0, sticky="nsew")

        # --- Row 4: Chart Area ---
        row4_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        row4_frame.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=30, pady=10)
        row4_frame.grid_columnconfigure(0, weight=1)

        self.panel_chart = ctk.CTkFrame(row4_frame, **card_kwargs)
        self.panel_chart.grid(row=0, column=0, sticky="nsew")
        
        self.figure = Figure(figsize=(5, 3), dpi=100, facecolor=self.color_card)
        self.ax = self.figure.add_subplot(111)
        self.ax.set_facecolor(self.color_card)
        self.ax.tick_params(colors=self.color_text_muted, labelsize=8)
        self.ax.spines['bottom'].set_color('#333333')
        self.ax.spines['top'].set_color('#333333') 
        self.ax.spines['right'].set_color('#333333')
        self.ax.spines['left'].set_color('#333333')
        self.ax.set_title("Live Price Ratio (Asset Correlation Tracking)", color='white', fontsize=10)
        
        self.chart_canvas = FigureCanvasTkAgg(self.figure, master=self.panel_chart)
        self.chart_canvas.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

        self.chart_data_ratio = collections.deque([0.0]*120, maxlen=120)
        
        # --- Row 5: Sanitized Log Viewer ---
        row5_frame = ctk.CTkFrame(self.frm_dash, fg_color="transparent")
        row5_frame.grid(row=5, column=0, columnspan=2, sticky="nsew", padx=30, pady=5)
        row5_frame.grid_columnconfigure(0, weight=1)
        
        panel_logs = ctk.CTkFrame(row5_frame, **card_kwargs)
        panel_logs.grid(row=0, column=0, sticky="nsew")
        ctk.CTkLabel(panel_logs, text="Sanitized Live System Logs (Math Protected)", font=ctk.CTkFont(family="Inter", size=14), text_color="white").pack(anchor="w", padx=15, pady=(10, 0))
        
        self.txt_logs = ctk.CTkTextbox(panel_logs, height=100, fg_color="#1E1E1E", text_color="#A0A0A0", font=ctk.CTkFont(family="Consolas", size=11))
        self.txt_logs.pack(fill="both", expand=True, padx=15, pady=10)
        self.txt_logs.configure(state="disabled")
        
        self.lbl_build_version = ctk.CTkLabel(self.frm_dash, text=f"Build: {CURRENT_VERSION}", font=ctk.CTkFont(family="Inter", size=10), text_color="#555555")
        self.lbl_build_version.grid(row=6, column=1, sticky="se", padx=30, pady=(0, 5))
        
        # Start update check
        threading.Thread(target=self.check_for_updates, daemon=True).start()

    def check_for_updates(self):
        import urllib.request, json
        try:
            req = urllib.request.Request("https://apexbotagz.com/version.json", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
                live_version = data.get("version", "0.0.0")
                try:
                    live_parts = tuple(map(int, live_version.split('.')))
                    curr_parts = tuple(map(int, CURRENT_VERSION.split('.')))
                    is_newer = live_parts > curr_parts
                except ValueError:
                    is_newer = live_version != CURRENT_VERSION and live_version > CURRENT_VERSION
                if is_newer:
                    self.after(0, lambda: self.btn_update.pack(side="right", padx=20))
        except Exception as e:
            print(f"Failed to check for updates: {e}")
    def fetch_tickers_news_loop(self):
        import requests, time, threading
        
        def _loop():
            news_items = ["Live Market Tracking Active..."]
            news_idx = 0
            
            while True:
                try:
                    # Fetch Tickers
                    r = requests.get("https://fapi.binance.com/fapi/v1/ticker/price?symbols=[\"SOLUSDT\",\"BNBUSDT\"]", timeout=5).json()
                    for tick in r:
                        if tick['symbol'] == 'SOLUSDT':
                            self.val_mark_sol.configure(text=f"SOL\n${float(tick['price']):.2f}")
                        elif tick['symbol'] == 'BNBUSDT':
                            self.val_mark_bnb.configure(text=f"BNB\n${float(tick['price']):.2f}")
                except:
                    pass
                    
                try:
                    # Fetch News every 50 loops (250s)
                    if news_idx % 50 == 0:
                        import xml.etree.ElementTree as ET
                        nr = requests.get("https://cointelegraph.com/rss", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).text
                        root = ET.fromstring(nr)
                        news_items = [item.find('title').text for item in root.findall('.//item')[:5] if item.find('title') is not None]
                    
                    if news_items:
                        title = news_items[(news_idx % len(news_items))]
                        # Decode HTML entities if any
                        title = title.replace("&apos;", "'").replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
                        # Truncate
                        if len(title) > 90: title = title[:87] + "..."
                        self.lbl_news.configure(text=f"📰 {title}")
                except:
                    pass
                    
                news_idx += 1
                time.sleep(5)
                
        threading.Thread(target=_loop, daemon=True).start()

    def fetch_binance_pairs(self):
        import requests
        try:
            r = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=5).json()
            pairs = [s['symbol'].replace("USDT", "/USDT:USDT") for s in r['symbols'] if s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT']
            pairs.sort()
            if pairs:
                self.combo_sym_a.configure(values=pairs)
                self.combo_sym_b.configure(values=pairs)
        except Exception as e:
            print("Failed to fetch pairs:", e)

    def _on_strategy_change(self, choice):
        if "BTC vs ETH" in choice:
            self.combo_sym_a.set("BTC/USDT:USDT")
            self.combo_sym_b.set("ETH/USDT:USDT")
            self.frm_symbols.pack_forget()
            self.lbl_zthresh.pack_forget()
            self.entry_zthresh.pack_forget()
            self.save_yaml_config()
            print(f"Strategy changed to {choice}")
        elif choice == "Custom Pairs":
            self.frm_symbols.pack(fill="x", pady=0, after=self.warn_frame)
            self.lbl_zthresh.pack(anchor="w", padx=20, pady=(20, 0))
            self.entry_zthresh.pack(anchor="w", padx=20)
            self.entry_zthresh.configure(state="normal")
            print("Strategy changed to Custom Pairs. Please select symbols manually.")

    def verify_sizing(self):
        import threading, ccxt, os
        from dotenv import load_dotenv
        self.lbl_sizing_warn.configure(text="Fetching wallet balance from Binance...", text_color="white")
        
        def fetch():
            try:
                load_dotenv(os.path.join(APP_DIR, "key.env"))
                api_key = os.getenv("BINANCE_API_KEY")
                secret = os.getenv("BINANCE_API_SECRET")
                if not api_key or not secret:
                    self.lbl_sizing_warn.configure(text="Missing API Keys! Cannot verify limits.", text_color=self.color_red)
                    return
                
                ex = ccxt.binanceusdm({'apiKey': api_key, 'secret': secret, 'enableRateLimit': True, 'options': {'broker': {'future': 'x-XSY2ZGS8', 'spot': 'x-XSY2ZGS8', 'swap': 'x-XSY2ZGS8', 'linear': 'x-XSY2ZGS8', 'delivery': 'x-XSY2ZGS8'}}})
                bal = ex.fetch_balance()
                total_usdt = float(bal.get('USDT', {}).get('total', 0.0))
                
                try:
                    val = float(self.entry_notional.get())
                except:
                    val = 0.0
                    
                hedge_ratio = 1.0
                try:
                    import trader_pairs
                    sym_a = self.combo_sym_a.get()
                    sym_b = self.combo_sym_b.get()
                    profile = trader_pairs.PairsTrader.PAIR_PROFILES.get((sym_a, sym_b)) or trader_pairs.PairsTrader.PAIR_PROFILES.get((sym_b, sym_a))
                    if profile and "HEDGE_RATIO" in profile:
                        hedge_ratio = float(profile["HEDGE_RATIO"])
                except Exception as e:
                    print(f"Error reading hedge ratio for GUI check: {e}")
                    
                if hedge_ratio < 1.0 and hedge_ratio > 0.0:
                    total_notional = val + (val / hedge_ratio)
                else:
                    total_notional = val + (val * hedge_ratio)
                    
                cap_used = total_notional / 5.0 # 5x leverage
                pct = (cap_used / total_usdt) * 100 if total_usdt > 0 else 999
                
                if pct <= 80:
                    self.lbl_sizing_warn.configure(text=f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (SAFE TIER)", text_color=self.color_green)
                elif pct <= 95:
                    self.lbl_sizing_warn.configure(text=f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (CAUTION TIER)", text_color="yellow")
                else:
                    self.lbl_sizing_warn.configure(text=f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (BLOCKED: Exceeds 95% Hard Cap)", text_color=self.color_red)
                    
            except Exception as e:
                self.lbl_sizing_warn.configure(text=f"Error fetching balance: {e}", text_color=self.color_red)
                
        threading.Thread(target=fetch, daemon=True).start()

    def _build_settings(self):
        import threading
        self.frm_settings = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        
        ctk.CTkLabel(self.frm_settings, text="Strategy & Configuration", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, anchor="w", padx=20)

        ctk.CTkLabel(self.frm_settings, text="Select Predefined Strategy Template:", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", padx=20, pady=(0, 5))
        self.strategy_var = ctk.StringVar(value="BTC vs ETH (Optimized)")
        self.strategy_dropdown = ctk.CTkOptionMenu(
            self.frm_settings, 
            values=["BTC vs ETH (Optimized)", "Custom Pairs"],
            variable=self.strategy_var,
            command=self._on_strategy_change,
            width=300
        )
        self.strategy_dropdown.pack(anchor="w", padx=20, pady=(0, 15))

        self.warn_frame = ctk.CTkFrame(self.frm_settings, fg_color="#4a3e00")
        self.warn_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(self.warn_frame, text="⚠️ CAUTION: BTC/USDT vs ETH/USDT is mathematically optimized.\nSelecting custom pairs enforces safe fallbacks (Z=3.0, Hedge=1.0)!", text_color="yellow").pack(pady=10)

        self.frm_symbols = ctk.CTkFrame(self.frm_settings, fg_color="transparent")
        
        ctk.CTkLabel(self.frm_symbols, text="Symbol A (USDT Perpetual):").pack(anchor="w", padx=20, pady=(10, 0))
        self.combo_sym_a = ctk.CTkComboBox(self.frm_symbols, values=["BTC/USDT:USDT", "ETH/USDT:USDT"], width=300)
        self.combo_sym_a.pack(anchor="w", padx=20)

        ctk.CTkLabel(self.frm_symbols, text="Symbol B (USDT Perpetual):").pack(anchor="w", padx=20, pady=(10, 0))
        self.combo_sym_b = ctk.CTkComboBox(self.frm_symbols, values=["BTC/USDT:USDT", "ETH/USDT:USDT"], width=300)
        self.combo_sym_b.pack(anchor="w", padx=20)

        ctk.CTkLabel(self.frm_settings, text="Notional Per Leg ($):").pack(anchor="w", padx=20, pady=(20, 0))
        not_frame = ctk.CTkFrame(self.frm_settings, fg_color="transparent")
        not_frame.pack(fill="x", padx=20)
        self.entry_notional = ctk.CTkEntry(not_frame, width=200)
        self.entry_notional.pack(side="left")
        
        self.btn_check_lim = ctk.CTkButton(not_frame, text="Verify Sizing Limits", command=self.verify_sizing)
        self.btn_check_lim.pack(side="left", padx=10)
        
        self.lbl_sizing_warn = ctk.CTkLabel(self.frm_settings, text="", text_color="yellow")
        self.lbl_sizing_warn.pack(anchor="w", padx=20)

        self.frm_z_container = ctk.CTkFrame(self.frm_settings, fg_color="transparent")
        self.frm_z_container.pack(fill="x")
        self.lbl_zthresh = ctk.CTkLabel(self.frm_z_container, text="Z-Score Entry Threshold (Optional Override):")
        self.lbl_zthresh.pack(anchor="w", padx=20, pady=(20, 0))
        self.entry_zthresh = ctk.CTkEntry(self.frm_z_container, width=300)
        self.entry_zthresh.pack(anchor="w", padx=20)

        ctk.CTkLabel(self.frm_settings, text="Risk Mode (Hedge Ratio):").pack(anchor="w", padx=20, pady=(20, 0))
        self.combo_risk_mode = ctk.CTkOptionMenu(self.frm_settings, values=["Conservative (Hedge 0.5)", "Pure Neutral (Hedge 1.0)"], width=300)
        self.combo_risk_mode.pack(anchor="w", padx=20)

        ctk.CTkButton(self.frm_settings, text="Save Settings", command=self.save_yaml_config).pack(anchor="w", padx=20, pady=30)
        threading.Thread(target=self.fetch_binance_pairs, daemon=True).start()
    def _build_apikeys(self):
        self.frm_keys = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        
        ctk.CTkLabel(self.frm_keys, text="API Keys (key.env)", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, anchor="w", padx=20)

        # Try to fetch IP address for Binance whitelisting
        try:
            import urllib.request
            ip = urllib.request.urlopen('https://api.ipify.org', timeout=3).read().decode('utf8')
            ip_text = f"Your Public IP Address (For Binance Whitelist): {ip}"
        except Exception:
            ip_text = "Your Public IP Address: [Unable to fetch - check internet]"

        ip_frame = ctk.CTkFrame(self.frm_keys, fg_color="#1f538d")
        ip_frame.pack(fill="x", padx=20, pady=10)
        ctk.CTkLabel(ip_frame, text=ip_text, text_color="white", font=ctk.CTkFont(weight="bold")).pack(pady=10)

        ctk.CTkLabel(self.frm_keys, text="Binance API Key:").pack(anchor="w", padx=20, pady=(10, 0))
        self.entry_api_key = ctk.CTkEntry(self.frm_keys, width=500)
        self.entry_api_key.pack(anchor="w", padx=20)

        ctk.CTkLabel(self.frm_keys, text="Binance API Secret:").pack(anchor="w", padx=20, pady=(10, 0))
        self.entry_api_secret = ctk.CTkEntry(self.frm_keys, width=500, show="*")
        self.entry_api_secret.pack(anchor="w", padx=20)

        ctk.CTkButton(self.frm_keys, text="Save Keys", command=self.save_env_config).pack(anchor="w", padx=20, pady=30)

    def _build_history(self):
        self.frm_history = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        ctk.CTkLabel(self.frm_history, text="Data & Diagnostics", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, anchor="w", padx=20)
        
        desc = ("Manage your trade history and export diagnostic logs for support.\n"
                "Diagnostic logs are obfuscated to protect your proprietary mathematical algorithms.")
        ctk.CTkLabel(self.frm_history, text=desc, font=ctk.CTkFont(size=14), justify="left", text_color=self.color_text_muted).pack(anchor="w", padx=20, pady=(0, 20))
        
        btn_csv = ctk.CTkButton(self.frm_history, text="📂 Open CSV History Folder", command=self.open_history_folder, fg_color="#2A2A2A", width=250)
        btn_csv.pack(anchor="w", padx=20, pady=10)
        
        btn_diag = ctk.CTkButton(self.frm_history, text="🛡️ Export Diagnostic Logs", command=self.export_diagnostics, fg_color="#4A2000", width=250)
        btn_diag.pack(anchor="w", padx=20, pady=10)
        
    def open_history_folder(self):
        import os
        data_dir = os.path.join(APP_DIR, "data")
        if not os.path.exists(data_dir):
            os.makedirs(data_dir, exist_ok=True)
        os.startfile(data_dir)
        
    def export_diagnostics(self):
        import os, tkinter.messagebox
        log_path = os.path.join(APP_DIR, "system.apexlog")
        if not os.path.exists(log_path):
            tkinter.messagebox.showinfo("Export", "No diagnostic logs found yet.")
            return
            
        desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
        export_path = os.path.join(desktop, "apex_diagnostics.apexlog")
        try:
            import shutil
            shutil.copy2(log_path, export_path)
            tkinter.messagebox.showinfo("Export Success", f"Obfuscated diagnostics exported to Desktop:\n{export_path}")
        except Exception as e:
            tkinter.messagebox.showerror("Export Failed", f"Failed to export: {e}")

    def toggle_startup(self):
        import os, win32com.client
        import sys
        startup_path = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup\ApexBot.lnk")
        if self.sw_startup.get() == 1:
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(startup_path)
            shortcut.Targetpath = sys.executable
            shortcut.WorkingDirectory = os.path.dirname(sys.executable)
            shortcut.save()
        else:
            if os.path.exists(startup_path):
                os.remove(startup_path)

    def toggle_sound(self):
        self.save_yaml_config()

    def _build_app_settings(self):
        self.frm_app_settings = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        ctk.CTkLabel(self.frm_app_settings, text="App Settings", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, anchor="w", padx=20)
        
        def set_mode(mode):
            ctk.set_appearance_mode(mode)
            
        ctk.CTkLabel(self.frm_app_settings, text="UI Theme:").pack(anchor="w", padx=20, pady=(10, 0))
        self.theme_combo = ctk.CTkComboBox(self.frm_app_settings, values=["Dark", "Light"], command=set_mode)
        self.theme_combo.pack(anchor="w", padx=20)
        self.theme_combo.set("Dark")
        
        self.sw_startup = ctk.CTkSwitch(self.frm_app_settings, text="Start Bot on Windows Startup", command=self.toggle_startup)
        self.sw_startup.pack(anchor="w", padx=20, pady=20)
        
        self.sw_sound = ctk.CTkSwitch(self.frm_app_settings, text="Enable Sound Alerts on Trade", command=self.toggle_sound)
        self.sw_sound.pack(anchor="w", padx=20, pady=(0, 20))

    def _build_help(self):
        self.frm_help = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        ctk.CTkLabel(self.frm_help, text="Help & Support", font=ctk.CTkFont(size=24, weight="bold")).pack(pady=20, anchor="w", padx=20)
        
        help_text = (
            "Apex Crypto Bot uses Statistical Arbitrage to trade correlated perpetual futures.\n\n"
            "Z-Score Metrics:\n"
            "- Live Z-Score: The current deviation from the mean.\n"
            "- Anchored Z-Score: Protects against extreme structural market breaks.\n\n"
            "Notional Sizing:\n"
            "The Notional setting represents the dollar value placed on EACH leg of the trade. "
            "A $160 setting means $160 Long and $160 Short simultaneously.\n\n"
            "Support Details:\n"
            "Telegram: @AGZ2806\n"
            "Email: support@apexbotagz.com\n"
            "Website: https://apexbotagz.com/"
        )
        lbl = ctk.CTkLabel(self.frm_help, text=help_text, justify="left", font=ctk.CTkFont(size=14))
        lbl.pack(anchor="w", padx=20, pady=10)
    def ping_loop(self):
        while True:
            try:
                start = time.time()
                resp = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
                if resp.status_code == 200:
                    self.latency_ms = int((time.time() - start) * 1000)
                    self.is_unstable = self.latency_ms > 5000
                else:
                    self.latency_ms = 9999
                    self.is_unstable = True
            except:
                self.latency_ms = 9999
                self.is_unstable = True
            time.sleep(3)

    def shm_tail_loop(self):
        import time
        try:
            from phase23_shm import SharedMemoryManager, SIGNALS_SHM_NAME
        except ImportError:
            return
            
        reader = None
        while True:
            if not self.bot_running:
                self.live_status_shm = "STOPPED"
                time.sleep(1)
                continue
                
            if reader is None:
                try:
                    reader = SharedMemoryManager(is_writer=False, name=SIGNALS_SHM_NAME)
                except Exception:
                    time.sleep(1)
                    continue
                    
            try:
                data = reader.read()
                if data:
                    self.live_status_shm = data.get("status", "UNKNOWN")
                    self.live_btc = f"{data.get('mark_a', 0):.2f}"
                    self.live_eth = f"{data.get('mark_b', 0):.2f}"
                    self.live_sym_a = data.get('sym_a', "SymA").replace("/USDT:USDT", "")
                    self.live_sym_b = data.get('sym_b', "SymB").replace("/USDT:USDT", "")
                    
                    z_raw = data.get('z_score', 0.0)
                    az_raw = data.get('anchored_z', 0.0)
                    self.live_z_score = f"{z_raw:+.2f}" if abs(z_raw) > 0.0001 else "Gathering Data..."
                    self.live_anchored_z = f"{az_raw:+.2f}" if abs(az_raw) > 0.0001 else "Gathering Data..."
                    
                    pnl_raw = data.get('estimated_pnl', 0)
                    self.live_pnl = f"${pnl_raw:+.2f}"
                    
                    self.wallet_advice = data.get("wallet_advice", "No wallet data...")
                    
                    btc_price = float(data.get('mark_a', 0))
                    eth_price = float(data.get('mark_b', 0))
                    if hasattr(self, 'chart_data_ratio') and eth_price > 0:
                        self.chart_data_ratio.append(btc_price / eth_price)
            except Exception:
                reader = None
            
            time.sleep(1)

    def update_chart_loop(self):
        try:
            if getattr(self, 'bot_running', False) and hasattr(self, 'chart_data_ratio'):
                self.ax.clear()
                
                color_ratio = "#AA00FF"
                
                self.ax.plot(self.chart_data_ratio, color=color_ratio, linewidth=1.5, label="Price Ratio")
                self.ax.set_facecolor(self.color_card)
                self.ax.tick_params(colors=self.color_text_muted, labelsize=8)
                
                self.ax.spines['bottom'].set_color('#333333')
                self.ax.spines['top'].set_color('#333333') 
                self.ax.spines['right'].set_color('#333333')
                self.ax.spines['left'].set_color('#333333')
                
                if len(self.chart_data_ratio) > 0:
                    self.ax.fill_between(range(len(self.chart_data_ratio)), self.chart_data_ratio, min(self.chart_data_ratio), color=color_ratio, alpha=0.1)
                
                self.chart_canvas.draw()
        except Exception:
            pass
            
        self.after(2000, self.update_chart_loop)

    def update_ui_loop(self):
        try:
            # Update Clock
            from datetime import datetime
            now = datetime.now()
            self.lbl_clock.configure(text=now.strftime("%I:%M %p\n%b %d, %Y"))

            # Bot Status & Buttons
            if self.bot_running:
                self.btn_toggle.configure(text="STOP BOT\n(RUNNING)", text_color=self.color_red, border_color=self.color_red, fg_color="#330011")
            else:
                state_lbl = "START BOT\n(SHUTDOWN)" if not self.is_unstable else "START BOT\n(HIGH LATENCY)"
                self.btn_toggle.configure(text=state_lbl, text_color=self.color_green, border_color=self.color_green, fg_color="transparent", state="normal")

            # Update Market Data
            if hasattr(self, 'live_sym_a'):
                self.val_mark_a.configure(text=f"{self.live_sym_a}\n${self.live_btc}")
                self.val_mark_b.configure(text=f"{self.live_sym_b}\n${self.live_eth}")
            
            # Balance
            if hasattr(self, 'wallet_advice'):
                if "??" in self.wallet_advice:
                    self.val_balance.configure(text=self.wallet_advice.replace("?? ", "⚠️ "), text_color=self.color_red)
                else:
                    self.val_balance.configure(text=self.wallet_advice, text_color="white")
            
            # Symbol Locking Logic
            is_orphaned = getattr(self, 'positions_kept_open', False)
            if self.bot_running or is_orphaned:
                self.combo_sym_a.configure(state="disabled")
                self.combo_sym_b.configure(state="disabled")
                self.strategy_dropdown.configure(state="disabled")
                if is_orphaned and not self.bot_running:
                    self.lbl_sizing_warn.configure(text="WARNING: Positions kept open! Symbols locked.", text_color="yellow")
            else:
                self.combo_sym_a.configure(state="normal")
                self.combo_sym_b.configure(state="normal")
                self.strategy_dropdown.configure(state="normal")
            
            # Update Z-Score & Status
            if self.bot_running and self.live_status_shm != "STOPPED":
                self.val_z.configure(text=self.live_z_score)
                self.val_az.configure(text=self.live_anchored_z)
                self.val_pnl.configure(text=self.live_pnl)
                
                if "IN SPREAD" in self.live_status_shm:
                    if not getattr(self, 'was_in_spread', False):
                        self.was_in_spread = True
                        if hasattr(self, 'sw_sound') and self.sw_sound.get() == 1:
                            import threading
                            def _play():
                                import winsound, time
                                winsound.Beep(1000, 200)
                                time.sleep(0.1)
                                winsound.Beep(1500, 200)
                            threading.Thread(target=_play, daemon=True).start()
                    self.lbl_z_sub.configure(text="In Spread (Active)", text_color=self.color_green)
                else:
                    self.was_in_spread = False
                    self.lbl_z_sub.configure(text="Scanning Market", text_color=self.color_text_muted)
            else:
                self.val_z.configure(text="N/A", text_color="#555555")
                self.val_az.configure(text="N/A", text_color="#555555")
                self.val_pnl.configure(text="N/A", text_color="#555555")
                self.lbl_z_sub.configure(text="Bot Offline", text_color="#555555")

        except Exception as e:
            pass
            
        # Process logs
        if hasattr(self, 'log_queue'):
            import queue
            logs_processed = 0
            self.txt_logs.configure(state="normal")
            while logs_processed < 50:
                try:
                    msg = self.log_queue.get_nowait()
                    # Sanitize message
                    sensitive_words = ["z-score", "half-life", "mean", "deviation", "ratio", "kalman", "zscore", "shm", "math", "z=", "dynz", "decay", "hedge", "hold", "corrmin", "sizing", "notional", "stoploss", "profile"]
                    if any(w in msg.lower() for w in sensitive_words):
                        continue
                    
                    self.txt_logs.insert("end", msg)
                    logs_processed += 1
                except queue.Empty:
                    break
            
            if logs_processed > 0:
                # Trim log to prevent GUI lag over time (Do this once per batch)
                line_count = int(float(self.txt_logs.index('end-1c')))
                if line_count > 1000:
                    self.txt_logs.delete("1.0", f"{line_count - 1000 + 1}.0")
                self.txt_logs.see("end")
            self.txt_logs.configure(state="disabled")
            
        self.after(500, self.update_ui_loop)

    def load_yaml_config(self):
        try:
            import yaml
            import os
            CONFIG_PATH = os.path.join(APP_DIR, "user_settings.yaml")
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r") as f:
                    cfg = yaml.safe_load(f)
                
                sym_a = cfg.get("symbols", ["BTC/USDT:USDT", "ETH/USDT:USDT"])[0]
                sym_b = cfg.get("symbols", ["BTC/USDT:USDT", "ETH/USDT:USDT"])[1]
                
                self.combo_sym_a.set(sym_a)
                self.combo_sym_b.set(sym_b)
                self.loaded_sym_a = sym_a
                self.loaded_sym_b = sym_b
                
                if sym_a == "BTC/USDT:USDT" and sym_b == "ETH/USDT:USDT":
                    self.strategy_var.set("BTC vs ETH (Optimized)")
                    self.frm_symbols.pack_forget()
                else:
                    self.strategy_var.set("Custom Pairs")
                    self.frm_symbols.pack(fill="x", pady=0, after=self.warn_frame)
                
                pt = cfg.get("pairs_trading", {})
                self.entry_notional.delete(0, "end")
                self.entry_notional.insert(0, str(pt.get("notional_per_leg", 160.0)))
                
                self.entry_zthresh.configure(state="normal")
                self.entry_zthresh.delete(0, "end")
                self.entry_zthresh.insert(0, str(pt.get("z_entry_threshold", 3.0)))
                if sym_a == "BTC/USDT:USDT" and sym_b == "ETH/USDT:USDT":
                    self.lbl_zthresh.pack_forget()
                    self.entry_zthresh.pack_forget()
                else:
                    self.lbl_zthresh.pack(anchor="w", padx=20, pady=(20, 0))
                    self.entry_zthresh.pack(anchor="w", padx=20)
                
                hr = pt.get("hedge_ratio", 0.5)
                if hr == 1.0:
                    self.combo_risk_mode.set("Pure Neutral (Hedge 1.0)")
                else:
                    self.combo_risk_mode.set("Conservative (Hedge 0.5)")
                    
                app_cfg = cfg.get("app_settings", {})
                if app_cfg.get("sound_alerts", False):
                    self.sw_sound.select()
                else:
                    self.sw_sound.deselect()
        except Exception as e:
            print(f"Failed to load yaml config: {e}")

    def save_yaml_config(self):
        try:
            import yaml
            import os
            sym_a = self.combo_sym_a.get().strip()
            sym_b = self.combo_sym_b.get().strip()
            
            # 5-Min Cooldown Check
            if sym_a != getattr(self, 'loaded_sym_a', "") or sym_b != getattr(self, 'loaded_sym_b', ""):
                import time
                last_change = getattr(self, 'last_pair_change_time', 0)
                if time.time() - last_change < 300:
                    import tkinter.messagebox
                    left = int(300 - (time.time() - last_change))
                    tkinter.messagebox.showerror("Rate Limit Error", f"Binance API Protection: You can only change trading pairs once every 5 minutes.\nPlease wait {left} seconds.")
                    return
                self.last_pair_change_time = time.time()
                self.loaded_sym_a = sym_a
                self.loaded_sym_b = sym_b

            hr_str = self.combo_risk_mode.get()
            hr = 1.0 if "1.0" in hr_str else 0.5
            
            # Hard Cap Check (Sync)
            try:
                import ccxt, os
                from dotenv import load_dotenv
                load_dotenv(os.path.join(APP_DIR, "key.env"))
                api_key = os.getenv("BINANCE_API_KEY")
                secret = os.getenv("BINANCE_API_SECRET")
                if api_key and secret:
                    ex = ccxt.binanceusdm({'apiKey': api_key, 'secret': secret, 'enableRateLimit': True, 'options': {'broker': {'future': 'x-XSY2ZGS8', 'spot': 'x-XSY2ZGS8', 'swap': 'x-XSY2ZGS8', 'linear': 'x-XSY2ZGS8', 'delivery': 'x-XSY2ZGS8'}}})
                    bal = ex.fetch_balance()
                    total_usdt = float(bal.get('USDT', {}).get('total', 0.0))
                    val = float(self.entry_notional.get())
                    if hr < 1.0 and hr > 0.0:
                        cap_used = (val + (val / hr)) / 5.0
                    else:
                        cap_used = (val + (val * hr)) / 5.0
                    pct = (cap_used / total_usdt) * 100 if total_usdt > 0 else 0
                    if pct > 95:
                        import tkinter.messagebox
                        tkinter.messagebox.showerror("Hard Cap Exceeded", f"You are attempting to use {pct:.1f}% of your wallet capacity.\nThe maximum allowed is 95%.\nPlease lower your Notional Per Leg or contact the developer for 100% margin overrides.")
                        return
            except Exception:
                pass
            
            cfg = {
                "symbols": [sym_a, sym_b],
                "pairs_trading": {
                    "notional_per_leg": float(self.entry_notional.get()),
                    "z_entry_threshold": float(self.entry_zthresh.get()) if self.entry_zthresh.get() else 3.0,
                    "hedge_ratio": hr
                },
                "app_settings": {
                    "sound_alerts": bool(self.sw_sound.get())
                }
            }
            CONFIG_PATH = os.path.join(APP_DIR, "user_settings.yaml")
            with open(CONFIG_PATH, "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
            print("Config Saved!")
        except Exception as e:
            print(f"Failed to save config: {e}")

    def load_env_config(self):
        try:
            import os
            ENV_PATH = os.path.join(APP_DIR, "key.env")
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            if k == "BINANCE_API_KEY":
                                self.entry_api_key.delete(0, "end")
                                self.entry_api_key.insert(0, v)
                            elif k == "BINANCE_API_SECRET":
                                self.entry_api_secret.delete(0, "end")
                                self.entry_api_secret.insert(0, v)
        except Exception as e:
            print(f"Failed to load env: {e}")

    def save_env_config(self):
        try:
            import os
            ENV_PATH = os.path.join(APP_DIR, "key.env")
            out_lines = []
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    out_lines = f.readlines()
            
            # Remove existing keys
            out_lines = [l for l in out_lines if not l.startswith("BINANCE_API_KEY=") and not l.startswith("BINANCE_API_SECRET=")]
            
            # Append new keys
            out_lines.append(f"BINANCE_API_KEY={self.entry_api_key.get().strip()}\n")
            out_lines.append(f"BINANCE_API_SECRET={self.entry_api_secret.get().strip()}\n")
            
            with open(ENV_PATH, "w") as f:
                f.writelines(out_lines)
            print("Env config saved!")
        except Exception as e:
            print(f"Failed to save env: {e}")


    def select_frame(self, name):
        # Update sidebar button colors
        self.btn_dash.configure(fg_color="#2A2A2A" if name == "dashboard" else "transparent")
        self.btn_settings.configure(fg_color="#2A2A2A" if name == "settings" else "transparent")
        self.btn_keys.configure(fg_color="#2A2A2A" if name == "keys" else "transparent")
        self.btn_history.configure(fg_color="#2A2A2A" if name == "history" else "transparent")
        self.btn_settings_app.configure(fg_color="#2A2A2A" if name == "app_settings" else "transparent")
        self.btn_help.configure(fg_color="#2A2A2A" if name == "help" else "transparent")

        # Hide all frames
        for frame in [self.frm_dash, self.frm_settings, self.frm_keys, self.frm_history, self.frm_app_settings, self.frm_help]:
            frame.grid_forget()

        # Show selected frame
        if name == "dashboard":
            self.frm_dash.grid(row=0, column=1, sticky="nsew")
        elif name == "settings":
            self.frm_settings.grid(row=0, column=1, sticky="nsew")
        elif name == "keys":
            self.frm_keys.grid(row=0, column=1, sticky="nsew")
        elif name == "history":
            self.frm_history.grid(row=0, column=1, sticky="nsew")
        elif name == "app_settings":
            self.frm_app_settings.grid(row=0, column=1, sticky="nsew")
        elif name == "help":
            self.frm_help.grid(row=0, column=1, sticky="nsew")

    def toggle_bot(self):
        if getattr(self, 'bot_running', False):
            if hasattr(self, 'live_status_shm') and "IN SPREAD" in self.live_status_shm:
                import tkinter.messagebox
                ans = tkinter.messagebox.askyesnocancel(
                    "Active Trade Warning",
                    "A trade is currently open!\n\n"
                    "YES: Force Close Positions immediately and stop the bot.\n"
                    "NO: Stop the bot, but KEEP positions open.\n"
                    "CANCEL: Cancel stop request."
                )
                if ans is True:
                    import threading
                    threading.Thread(target=self.force_close_all, daemon=True).start()
                    self.stop_bot()
                elif ans is False:
                    self.positions_kept_open = True
                    self.stop_bot()
                elif ans is None:
                    return # Cancel
            else:
                self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        try:
            self.positions_kept_open = False
            if getattr(self, 'is_unstable', False):
                print("Warning: API latency is high or unstable, but attempting to start anyway...")
                
            import multiprocessing
            import os
            
            # Load env explicitly
            env_path = os.path.join(APP_DIR, "key.env")
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    for line in f:
                        if "=" in line:
                            k, v = line.strip().split("=", 1)
                            if k.startswith("BINANCE"):
                                os.environ[k] = v

            self.bot_processes = []
            if not hasattr(self, 'log_queue'):
                # Used to stream sanitized logs from trader. Max size prevents OOM during log bursts.
                self.log_queue = multiprocessing.Queue(maxsize=200)
                
            is_scribe_running = False
            import phase23_shm as sm
            from multiprocessing import shared_memory
            try:
                existing_shm = shared_memory.SharedMemory(name=sm.SHM_NAME)
                existing_shm.close()
                is_scribe_running = True
                print("CLI Scribe detected! GUI will not launch internal Scribe.")
            except FileNotFoundError:
                print("No CLI Scribe detected. GUI launching internal Scribe.")
                
            if not is_scribe_running:
                p_scribe = multiprocessing.Process(target=run_scribe)
                p_scribe.start()
                self.bot_processes.append(p_scribe)
                
            p_trader = multiprocessing.Process(target=run_trader, args=(self.log_queue,))
            p_trader.start()
            self.bot_processes.append(p_trader)
            
            self.bot_running = True
            print("Bot processes started successfully!")
        except Exception as e:
            import traceback
            traceback.print_exc()
            print("START BOT ERROR:", e)

    def panic_action(self):
        import tkinter.messagebox
        ans = tkinter.messagebox.askyesno("PANIC CLOSE", "Are you sure you want to market close all open positions immediately?")
        if ans:
            import threading
            threading.Thread(target=self.force_close_all, daemon=True).start()

    def force_close_all(self):
        import ccxt, os
        from dotenv import load_dotenv
        self.positions_kept_open = False
        
        try:
            load_dotenv(os.path.join(APP_DIR, "key.env"))
            api_key = os.getenv("BINANCE_API_KEY")
            secret = os.getenv("BINANCE_API_SECRET")
            if not api_key or not secret:
                print("Missing API keys for force close!")
                return
            ex = ccxt.binanceusdm({'apiKey': api_key, 'secret': secret, 'enableRateLimit': True, 'options': {'broker': {'future': 'x-XSY2ZGS8', 'spot': 'x-XSY2ZGS8', 'swap': 'x-XSY2ZGS8', 'linear': 'x-XSY2ZGS8', 'delivery': 'x-XSY2ZGS8'}}})
            
            # Fetch positions
            positions = ex.fetch_positions()
            for pos in positions:
                raw_info = pos.get("info", {})
                amt = float(raw_info.get("positionAmt", 0.0))
                sym = pos.get("symbol")
                if abs(amt) > 0:
                    side = "buy" if amt < 0 else "sell"
                    print(f"Force closing {abs(amt)} of {sym} ({side})")
                    try:
                        ex.create_order(sym, "market", side, abs(amt), params={"reduceOnly": True})
                    except Exception as order_e:
                        print(f"Failed to create closing order for {sym}: {order_e}")
            print("All open positions force closed.")
        except Exception as e:
            print(f"Failed to force close positions: {e}")

    def stop_bot(self):
        for p in self.bot_processes:
            try:
                p.terminate()
                p.join(timeout=1.0)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=1.0)
            except Exception:
                pass
        self.bot_processes = []
        self.bot_running = False
        


if __name__ == "__main__":
    multiprocessing.freeze_support()
    app = PairsTraderGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
