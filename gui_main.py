import os
import sys
import time
import json
import requests
import yaml
import multiprocessing
import webbrowser
import collections
import zlib
import base64
from datetime import datetime
from dotenv import load_dotenv

from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QFrame, QStackedWidget, QComboBox, 
                               QLineEdit, QCheckBox, QTextEdit, QMessageBox, QSpacerItem, 
                               QSizePolicy)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot
from PySide6.QtGui import QFont, QIcon, QColor

import pyqtgraph as pg
import qdarkstyle

# Force Current Working Directory
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)

from scribe import main as scribe_main
from trader_pairs import main as trader_main

CURRENT_VERSION = "4.5.0"

# ---------------------------------------------------------
# Subprocesses & Multiprocessing Logging Handlers
# ---------------------------------------------------------
def run_scribe():
    import asyncio, sys, traceback, os
    from phase23_lib import TeeLogger
    os.makedirs("logs", exist_ok=True)
    sys.stdout = TeeLogger("logs/scribe.log")
    is_frozen = getattr(sys, 'frozen', False)
    if is_frozen:
        sys.stdout.terminal = None
    sys.stderr = sys.stdout
    try:
        asyncio.run(scribe_main(cli_mode=not is_frozen))
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"CRITICAL ERROR IN SCRIBE: {e}")
        traceback.print_exc()

class ObfuscatedLogger:
    def __init__(self, filename, queue=None):
        import sys, time
        self.filename = filename
        self.queue = queue
        self.terminal = sys.stdout
        self.buffer = []
        self.queue_buffer = ""
        self.last_write = time.time()

    def write(self, message):
        import time, sys
        is_frozen = getattr(sys, 'frozen', False)
        if not is_frozen and self.terminal:
            try:
                self.terminal.write(message)
                self.terminal.flush()
            except: pass
            
        if self.queue:
            self.queue_buffer += message
            while "\n" in self.queue_buffer:
                line, self.queue_buffer = self.queue_buffer.split("\n", 1)
                try:
                    self.queue.put_nowait(line + "\n")
                except:
                    pass
        if message.strip():
            self.buffer.append(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ')}] {message}")
        else:
            self.buffer.append(message)
        if len(self.buffer) > 100 or (time.time() - self.last_write) > 2.0:
            self.flush_buffer()

    def flush_buffer(self):
        if not self.buffer: return
        import zlib, base64, time, os
        try:
            if os.path.exists(self.filename) and os.path.getsize(self.filename) > 20 * 1024 * 1024:
                os.remove(self.filename)
            with open(self.filename, 'a') as f:
                joined = "".join(self.buffer)
                b64 = base64.b64encode(zlib.compress(joined.encode('utf-8'))).decode('utf-8')
                f.write(b64 + "\n")
        except: pass
        self.buffer.clear()
        self.last_write = time.time()
            
    def flush(self):
        self.flush_buffer()
        self.terminal.flush()

def run_trader(log_queue=None):
    import asyncio, sys, traceback, os
    sys.stdout = ObfuscatedLogger(os.path.join(APP_DIR, "system.apexlog"), log_queue)
    sys.stderr = sys.stdout
    try:
        asyncio.run(trader_main())
    except KeyboardInterrupt: pass
    except Exception as e:
        print(f"CRITICAL ERROR IN TRADER: {e}")
        traceback.print_exc()

def bootstrap_configs():
    import shutil
    if not os.path.exists("key.env"):
        with open("key.env", "w") as f: f.write("BINANCE_API_KEY=\nBINANCE_API_SECRET=\n")
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
        default_yaml = os.path.join(meipass, "user_settings.yaml")
        if not os.path.exists("user_settings.yaml") and os.path.exists(default_yaml):
            try: shutil.copy2(default_yaml, "user_settings.yaml")
            except: pass
bootstrap_configs()

# ---------------------------------------------------------
# QThreads for non-blocking UI updates
# ---------------------------------------------------------
class PingThread(QThread):
    ping_updated = Signal(int, bool)
    def run(self):
        session = requests.Session()
        session_start = time.time()
        while True:
            if time.time() - session_start > 43200:
                session.close()
                session = requests.Session()
                session_start = time.time()
            try:
                start = time.time()
                resp = session.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
                if resp.status_code == 200:
                    lat = int((time.time() - start) * 1000)
                    self.ping_updated.emit(lat, lat > 5000)
                else:
                    self.ping_updated.emit(9999, True)
            except:
                self.ping_updated.emit(9999, True)
            time.sleep(3)

class ShmTailThread(QThread):
    shm_data = Signal(dict)
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
    def run(self):
        try:
            from phase23_shm import SharedMemoryManager, SIGNALS_SHM_NAME
        except: return
        reader = None
        while True:
            if not getattr(self.main_window, 'bot_running', False):
                self.shm_data.emit({"status": "STOPPED"})
                time.sleep(1)
                continue
            if reader is None:
                try: reader = SharedMemoryManager(is_writer=False, name=SIGNALS_SHM_NAME)
                except:
                    time.sleep(1)
                    continue
            try:
                data = reader.read()
                if data: self.shm_data.emit(data)
            except: pass
            time.sleep(0.5)

class TickerThread(QThread):
    ticker_data = Signal(dict)
    news_data = Signal(str)
    def run(self):
        session = requests.Session()
        session_start = time.time()
        news_idx = 0
        news_items = ["Live Market Tracking Active..."]
        while True:
            if time.time() - session_start > 43200:
                session.close(); session = requests.Session(); session_start = time.time()
            try:
                r = session.get("https://fapi.binance.com/fapi/v1/ticker/price?symbols=[\"SOLUSDT\",\"BNBUSDT\"]", timeout=5).json()
                data = {}
                for tick in r:
                    if tick['symbol'] == 'SOLUSDT': data['SOL'] = float(tick['price'])
                    elif tick['symbol'] == 'BNBUSDT': data['BNB'] = float(tick['price'])
                if data: self.ticker_data.emit(data)
            except: pass
            
            try:
                if news_idx % 50 == 0:
                    import xml.etree.ElementTree as ET
                    nr = session.get("https://cointelegraph.com/rss", headers={'User-Agent': 'Mozilla/5.0'}, timeout=5).text
                    root = ET.fromstring(nr)
                    news_items = [item.find('title').text for item in root.findall('.//item')[:5] if item.find('title') is not None]
                if news_items:
                    title = news_items[(news_idx % len(news_items))].replace("&apos;", "'").replace("&quot;", '"').replace("&#39;", "'").replace("&amp;", "&")
                    if len(title) > 90: title = title[:87] + "..."
                    self.news_data.emit(f"📰 {title}")
            except: pass
            news_idx += 1
            time.sleep(5)

class WalletThread(QThread):
    wallet_data = Signal(str)
    def __init__(self, is_testnet, key, secret):
        super().__init__()
        self.is_testnet = is_testnet
        self.key = key
        self.secret = secret
    def run(self):
        import ccxt
        try:
            if not self.key or not self.secret:
                self.wallet_data.emit("$0.00 (No Keys)")
                return
            ex = ccxt.binanceusdm({'apiKey': self.key, 'secret': self.secret, 'enableRateLimit': True})
            if self.is_testnet: ex.set_sandbox_mode(True)
            bal = ex.fetch_balance()
            tot = float(bal.get('USDT', {}).get('total', 0.0))
            self.wallet_data.emit(f"${tot:,.2f}")
        except Exception as e:
            self.wallet_data.emit("Error fetching balance")

class SizingCheckThread(QThread):
    sizing_data = Signal(str, str) # text, color
    def __init__(self, main_window):
        super().__init__()
        self.w = main_window
    def run(self):
        import ccxt
        try:
            is_testnet = self.w.cb_testnet.isChecked()
            key = self.w.entry_testnet_api_key.text().strip() if is_testnet else self.w.entry_api_key.text().strip()
            secret = self.w.entry_testnet_api_secret.text().strip() if is_testnet else self.w.entry_api_secret.text().strip()
            if not key or not secret:
                self.sizing_data.emit("Missing API Keys! Cannot verify limits.", "red")
                return
            ex = ccxt.binanceusdm({'apiKey': key, 'secret': secret, 'enableRateLimit': True})
            if is_testnet: ex.set_sandbox_mode(True)
            bal = ex.fetch_balance()
            total_usdt = float(bal.get('USDT', {}).get('total', 0.0))
            
            val = float(self.w.entry_notional.text()) if self.w.entry_notional.text() else 0.0
            hr_str = self.w.combo_risk_mode.currentText()
            hedge_ratio = 1.0 if "1.0" in hr_str else 0.5
            
            if 0.0 < hedge_ratio < 1.0: total_notional = val + (val / hedge_ratio)
            else: total_notional = val + (val * hedge_ratio)
            
            cap_used = total_notional / 5.0
            pct = (cap_used / total_usdt) * 100 if total_usdt > 0 else 999
            
            if pct <= 80: self.sizing_data.emit(f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (SAFE TIER)", "#00FF88")
            elif pct <= 95: self.sizing_data.emit(f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (CAUTION TIER)", "yellow")
            else: self.sizing_data.emit(f"Wallet: ${total_usdt:.2f} | Usage: {pct:.1f}% (BLOCKED: Exceeds 95% Hard Cap)", "#FF3366")
        except Exception as e:
            self.sizing_data.emit(f"Error: {e}", "#FF3366")


# ---------------------------------------------------------
# Main GUI
# ---------------------------------------------------------
class ApexCryptoApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AGZ ApexBot - Build 4.5.0 (PySide6 Edition)")
        self.setMinimumSize(900, 600)
        self.resize(960, 640)
        
        # Set App Icon
        icon_path = os.path.join(APP_DIR, "apex_logo.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        self.bot_processes = []
        self.bot_running = False
        self.chart_data_ratio = collections.deque([0.0]*120, maxlen=120)
        self.log_queue = multiprocessing.Queue(maxsize=200)

        # Style colors
        self.c_bg = "#121212"
        self.c_card = "#1E1E1E"
        self.c_cyan = "#00E5FF"
        self.c_green = "#00FF88"
        self.c_red = "#FF3366"
        self.c_mut = "#888888"
        
        self._init_ui()
        self.load_env_config()
        self.load_yaml_config()
        
        # Threads
        self.ping_th = PingThread()
        self.ping_th.ping_updated.connect(self.on_ping)
        self.ping_th.start()
        
        self.shm_th = ShmTailThread(self)
        self.shm_th.shm_data.connect(self.on_shm)
        self.shm_th.start()
        
        self.tick_th = TickerThread()
        self.tick_th.ticker_data.connect(self.on_ticker)
        self.tick_th.news_data.connect(self.on_news)
        self.tick_th.start()
        
        # Timers
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.update_clock)
        self.clock_timer.start(1000)
        
        self.log_timer = QTimer(self)
        self.log_timer.timeout.connect(self.update_logs)
        self.log_timer.start(200)
        
        self.wallet_timer = QTimer(self)
        self.wallet_timer.timeout.connect(self.fetch_wallet_poll)
        self.wallet_timer.start(10000)
        
        self.chart_timer = QTimer(self)
        self.chart_timer.timeout.connect(self.update_chart)
        self.chart_timer.start(500)

    def _init_ui(self):
        main_widget = QWidget()
        main_widget.setObjectName("CentralWidget")
        main_widget.setStyleSheet("#CentralWidget { background-color: #121212; }")
        self.setCentralWidget(main_widget)
        main_layout = QHBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Sidebar
        self.sidebar = QFrame()
        self.sidebar.setFixedWidth(220)
        self.sidebar.setStyleSheet(f"background-color: #181818; border-right: 1px solid #333;")
        side_layout = QVBoxLayout(self.sidebar)
        
        from PySide6.QtGui import QPixmap
        logo_path = os.path.join(APP_DIR, "apex_logo.png")
        if os.path.exists(logo_path):
            logo_lbl = QLabel()
            pixmap = QPixmap(logo_path)
            pixmap = pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            logo_lbl.setPixmap(pixmap)
            logo_lbl.setAlignment(Qt.AlignCenter)
            logo_lbl.setStyleSheet("border: none; margin-top: 15px; margin-bottom: 5px;")
            side_layout.addWidget(logo_lbl)
        else:
            brand = QLabel("AGZ ApexBot")
            brand.setStyleSheet(f"color: {self.c_cyan}; font-size: 24px; font-weight: bold; border: none; margin: 15px;")
            brand.setAlignment(Qt.AlignCenter)
            side_layout.addWidget(brand)
            
        web_link = QLabel('<a href="https://apexbotagz.com/" style="color: #00E5FF; text-decoration: none;">apexbotagz.com</a>')
        web_link.setOpenExternalLinks(True)
        web_link.setAlignment(Qt.AlignCenter)
        web_link.setStyleSheet("font-size: 14px; border: none; margin-bottom: 15px;")
        side_layout.addWidget(web_link)
        
        self.nav_btns = {}
        nav_items = [("dashboard", "Dashboard"), ("settings", "Strategy & Config"), 
                     ("keys", "API Keys"), ("history", "History"), 
                     ("app_settings", "Settings"), ("help", "Help")]
                     
        for sid, text in nav_items:
            btn = QPushButton(text)
            btn.setStyleSheet(f"""
                QPushButton {{ text-align: left; padding: 10px 20px; font-size: 14px; color: white; background: transparent; border: none; }}
                QPushButton:hover {{ background: #2A2A2A; color: {self.c_cyan}; }}
                QPushButton:checked {{ background: #2A2A2A; color: {self.c_cyan}; font-weight: bold; }}
            """)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s=sid: self.switch_page(s))
            self.nav_btns[sid] = btn
            side_layout.addWidget(btn)
        side_layout.addStretch()
        
        # Main Content
        self.stack = QStackedWidget()
        self.stack.setStyleSheet(f"background-color: {self.c_bg};")
        
        self._build_dashboard()
        self._build_settings()
        self._build_keys()
        self._build_history()
        self._build_app_settings()
        self._build_help()
        
        main_layout.addWidget(self.sidebar)
        main_layout.addWidget(self.stack)
        
        self.switch_page("dashboard")

    def switch_page(self, page_id):
        for pid, btn in self.nav_btns.items():
            btn.setChecked(pid == page_id)
            
        pages = {"dashboard": 0, "settings": 1, "keys": 2, "history": 3, "app_settings": 4, "help": 5}
        self.stack.setCurrentIndex(pages[page_id])

    # --- Frame Builders ---
    def _create_card(self, title, color="white", border=None):
        card = QFrame()
        style = f"background-color: {self.c_card}; border-radius: 10px;"
        if border: style += f" border: 2px solid {border};"
        card.setStyleSheet(style)
        layout = QVBoxLayout(card)
        lbl = QLabel(title)
        lbl.setStyleSheet(f"color: {color}; font-size: 14px; border: none; background: transparent;")
        layout.addWidget(lbl)
        return card, layout

    def _build_dashboard(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        
        # Header
        h_layout = QHBoxLayout()
        title = QLabel("Dashboard - Real-Time Stats")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        h_layout.addWidget(title)
        
        self.lbl_news = QLabel("Loading Live Crypto News...")
        self.lbl_news.setStyleSheet(f"color: {self.c_cyan}; font-size: 16px; font-style: italic;")
        h_layout.addWidget(self.lbl_news)
        
        self.lbl_testnet_badge = QLabel("⚠️ PAPER TRADING MODE ACTIVE")
        self.lbl_testnet_badge.setStyleSheet("color: #ffa500; font-weight: bold;")
        h_layout.addWidget(self.lbl_testnet_badge)
        
        btn_panic = QPushButton("🚨 PANIC CLOSE ALL")
        btn_panic.setStyleSheet("background: #AA0000; color: white; font-weight: bold; padding: 5px 15px; border-radius: 5px;")
        btn_panic.clicked.connect(self.panic_action)
        h_layout.addWidget(btn_panic)
        
        self.lbl_clock = QLabel("--:--\n---")
        self.lbl_clock.setStyleSheet(f"color: {self.c_mut};")
        self.lbl_clock.setAlignment(Qt.AlignRight)
        h_layout.addWidget(self.lbl_clock)
        layout.addLayout(h_layout)
        
        # Row 1 (Market, Bal)
        r1 = QHBoxLayout()
        c_mark, l_mark = self._create_card("Market Overview")
        h_mark = QHBoxLayout()
        self.val_mark_a = QLabel("Sym A\n$---")
        self.val_mark_b = QLabel("Sym B\n$---")
        self.val_mark_sol = QLabel("SOL\n$---")
        self.val_mark_bnb = QLabel("BNB\n$---")
        for w in [self.val_mark_a, self.val_mark_b, self.val_mark_sol, self.val_mark_bnb]:
            w.setStyleSheet("color: white; font-size: 14px; background: transparent; border:none;")
            h_mark.addWidget(w)
        l_mark.addLayout(h_mark)
        r1.addWidget(c_mark)
        
        c_bal, l_bal = self._create_card("Account Balance", color="#A0A0A0")
        self.val_balance = QLabel("Scanning...")
        self.val_balance.setStyleSheet("color: white; font-size: 16px; font-weight: bold; background: transparent; border:none;")
        l_bal.addWidget(self.val_balance)
        r1.addWidget(c_bal)
        layout.addLayout(r1)
        
        # Row 2 (Stats)
        r2 = QHBoxLayout()
        c_z, l_z = self._create_card("Live Z-Score:", border=self.c_cyan)
        self.val_z = QLabel("N/A")
        self.val_z.setStyleSheet(f"color: {self.c_cyan}; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        l_z.addWidget(self.val_z)
        r2.addWidget(c_z)
        
        c_az, l_az = self._create_card("Anchored Z:", border="#AA00FF")
        self.val_az = QLabel("N/A")
        self.val_az.setStyleSheet("color: #AA00FF; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        l_az.addWidget(self.val_az)
        r2.addWidget(c_az)
        
        c_pnl, l_pnl = self._create_card("Estimated PnL:")
        self.val_pnl = QLabel("N/A")
        self.val_pnl.setStyleSheet(f"color: {self.c_green}; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        l_pnl.addWidget(self.val_pnl)
        r2.addWidget(c_pnl)
        
        c_stat, l_stat = self._create_card("Status:")
        self.val_stat = QLabel("Bot Offline")
        self.val_stat.setStyleSheet(f"color: {self.c_mut}; font-size: 16px; font-weight: bold; background: transparent; border:none;")
        l_stat.addWidget(self.val_stat)
        r2.addWidget(c_stat)
        layout.addLayout(r2)
        
        # Row 3 (Controls)
        self.btn_toggle = QPushButton("SHUTDOWN (Click to Start)")
        self.btn_toggle.setFixedHeight(60)
        self.btn_toggle.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {self.c_red}; border: 2px solid {self.c_red}; border-radius: 10px; background: transparent;")
        self.btn_toggle.clicked.connect(self.toggle_bot)
        layout.addWidget(self.btn_toggle)
        
        # Chart (PyQtGraph)
        pg.setConfigOption('background', self.c_card)
        pg.setConfigOption('foreground', 'w')
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideAxis('bottom')
        self.plot_widget.hideAxis('left')
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color=self.c_cyan, width=2))
        self.baseline = pg.InfiniteLine(angle=0, pen=pg.mkPen('w', width=1, style=Qt.DashLine))
        self.plot_widget.addItem(self.baseline)
        layout.addWidget(self.plot_widget, stretch=1)
        
        # Logs
        c_log, l_log = self._create_card("Sanitized Live System Logs")
        self.txt_logs = QTextEdit()
        self.txt_logs.document().setMaximumBlockCount(1000)
        self.txt_logs.setReadOnly(True)
        self.txt_logs.setStyleSheet("background: #121212; color: #A0A0A0; font-family: Consolas; font-size: 11px; border: 1px solid #333;")
        self.txt_logs.setFixedHeight(120)
        l_log.addWidget(self.txt_logs)
        layout.addWidget(c_log)
        
        self.stack.addWidget(page)

    def _build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setAlignment(Qt.AlignTop)
        
        lbl = QLabel("Strategy & Configuration")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(lbl)
        
        self.cb_testnet = QCheckBox("Enable Paper Trading Mode (Binance Testnet)")
        self.cb_testnet.setStyleSheet("color: #ffa500; font-weight: bold; font-size: 14px;")
        layout.addWidget(self.cb_testnet)
        
        layout.addWidget(QLabel("Select Predefined Strategy Template:"))
        self.combo_strategy = QComboBox()
        self.combo_strategy.addItems(["BTC vs ETH (Ultra-Safe)", "BTC vs ETH (Max Profit)", "ETH vs SOL (Optimized)", "Custom Pairs"])
        self.combo_strategy.currentTextChanged.connect(self.on_strategy_change)
        layout.addWidget(self.combo_strategy)
        
        warn = QLabel("⚠️ CAUTION: Predefined profiles are mathematically optimized.\nSelecting custom pairs enforces safe fallbacks (Z=3.0, Hedge=1.0)!")
        warn.setStyleSheet("background: #4a3e00; color: yellow; padding: 10px; border-radius: 5px;")
        layout.addWidget(warn)
        
        self.frm_custom = QFrame()
        c_layout = QVBoxLayout(self.frm_custom)
        c_layout.setContentsMargins(0, 0, 0, 0)
        
        c_layout.addWidget(QLabel("Symbol A (USDT Perpetual):"))
        self.combo_sym_a = QComboBox()
        self.combo_sym_a.addItems(["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT"])
        c_layout.addWidget(self.combo_sym_a)
        
        c_layout.addWidget(QLabel("Symbol B (USDT Perpetual):"))
        self.combo_sym_b = QComboBox()
        self.combo_sym_b.addItems(["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT"])
        c_layout.addWidget(self.combo_sym_b)
        
        c_layout.addWidget(QLabel("Z-Score Entry Threshold (Optional Override):"))
        self.entry_zthresh = QLineEdit("3.0")
        c_layout.addWidget(self.entry_zthresh)
        
        c_layout.addWidget(QLabel("Risk Mode (Hedge Ratio):"))
        self.combo_risk_mode = QComboBox()
        self.combo_risk_mode.addItems(["Conservative (Hedge 0.5)", "Pure Neutral (Hedge 1.0)"])
        c_layout.addWidget(self.combo_risk_mode)
        layout.addWidget(self.frm_custom)
        self.frm_custom.hide()
        
        layout.addWidget(QLabel("Notional Per Leg ($):"))
        h_not = QHBoxLayout()
        self.entry_notional = QLineEdit("160.0")
        self.entry_notional.setFixedWidth(200)
        h_not.addWidget(self.entry_notional)
        
        btn_verify = QPushButton("Verify Sizing Limits")
        btn_verify.clicked.connect(self.verify_sizing)
        h_not.addWidget(btn_verify)
        h_not.addStretch()
        layout.addLayout(h_not)
        
        self.lbl_sizing_warn = QLabel("")
        layout.addWidget(self.lbl_sizing_warn)
        
        btn_save = QPushButton("Save Settings")
        btn_save.clicked.connect(self.save_yaml_config)
        btn_save.setFixedWidth(150)
        layout.addWidget(btn_save)
        
        self.stack.addWidget(page)

    def _build_keys(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(30, 20, 30, 20)
        layout.setAlignment(Qt.AlignTop)
        
        lbl = QLabel("API Keys (key.env)")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(lbl)
        
        layout.addWidget(QLabel("Binance API Key (LIVE):"))
        self.entry_api_key = QLineEdit()
        self.entry_api_key.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.entry_api_key)
        
        layout.addWidget(QLabel("Binance API Secret (LIVE):"))
        self.entry_api_secret = QLineEdit()
        self.entry_api_secret.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.entry_api_secret)
        
        lbl_tk = QLabel("Binance API Key (TESTNET):")
        lbl_tk.setStyleSheet("color: #ffa500;")
        layout.addWidget(lbl_tk)
        self.entry_testnet_api_key = QLineEdit()
        self.entry_testnet_api_key.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.entry_testnet_api_key)
        
        lbl_ts = QLabel("Binance API Secret (TESTNET):")
        lbl_ts.setStyleSheet("color: #ffa500;")
        layout.addWidget(lbl_ts)
        self.entry_testnet_api_secret = QLineEdit()
        self.entry_testnet_api_secret.setEchoMode(QLineEdit.Password)
        layout.addWidget(self.entry_testnet_api_secret)
        
        btn_save = QPushButton("Save Keys")
        btn_save.clicked.connect(self.save_env_config)
        btn_save.setFixedWidth(150)
        layout.addWidget(btn_save)
        
        self.stack.addWidget(page)
        
    def _build_history(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(30, 20, 30, 20)
        
        lbl = QLabel("Data & Diagnostics")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(lbl)
        
        desc = QLabel("Manage your trade history and export diagnostic logs for support.\nDiagnostic logs are obfuscated to protect your proprietary mathematical algorithms.")
        desc.setStyleSheet(f"color: {self.c_mut};")
        layout.addWidget(desc)
        
        btn_csv = QPushButton("📂 Open CSV History Folder")
        btn_csv.clicked.connect(lambda: os.startfile(os.path.join(APP_DIR, "data")) if os.path.exists(os.path.join(APP_DIR, "data")) else None)
        btn_csv.setFixedWidth(250)
        layout.addWidget(btn_csv)
        
        btn_diag = QPushButton("🛡️ Export Diagnostic Logs")
        btn_diag.clicked.connect(self.export_diagnostics)
        btn_diag.setFixedWidth(250)
        btn_diag.setStyleSheet(f"background: #4A2000; color: white;")
        layout.addWidget(btn_diag)
        
        self.stack.addWidget(page)
        
    def export_diagnostics(self):
        log_path = os.path.join(APP_DIR, "system.apexlog")
        if not os.path.exists(log_path):
            QMessageBox.information(self, "Export", "No diagnostic logs found yet.")
            return
            
        desktop = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
        export_path = os.path.join(desktop, "apex_diagnostics.apexlog")
        try:
            import shutil
            shutil.copy2(log_path, export_path)
            QMessageBox.information(self, "Export Success", f"Obfuscated diagnostics exported to Desktop:\n{export_path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export: {e}")
        
    def _build_app_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(30, 20, 30, 20)
        
        lbl = QLabel("App Settings")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(lbl)
        
        self.sw_sound = QCheckBox("Enable Sound Alerts on Trade")
        layout.addWidget(self.sw_sound)
        
        self.stack.addWidget(page)

    def _build_help(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setAlignment(Qt.AlignTop)
        layout.setContentsMargins(30, 20, 30, 20)
        
        lbl = QLabel("Help & Support")
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; color: white;")
        layout.addWidget(lbl)
        
        help_text = (
            "Apex Crypto Bot uses Statistical Arbitrage to trade correlated perpetual futures.\n\n"
            "Support Details:\n"
            "Telegram: @AGZ2806\n"
            "Website: https://apexbotagz.com/"
        )
        layout.addWidget(QLabel(help_text))
        
        self.stack.addWidget(page)

    # --- Logic ---
    def on_strategy_change(self, choice):
        if "Custom Pairs" in choice:
            self.frm_custom.show()
        else:
            self.frm_custom.hide()
            if "BTC vs ETH" in choice:
                self.combo_sym_a.setCurrentText("BTC/USDT:USDT")
                self.combo_sym_b.setCurrentText("ETH/USDT:USDT")
            elif "ETH vs SOL" in choice:
                self.combo_sym_a.setCurrentText("ETH/USDT:USDT")
                self.combo_sym_b.setCurrentText("SOL/USDT:USDT")

    @Slot(int, bool)
    def on_ping(self, lat, unstable):
        self.latency_ms = lat
        self.is_unstable = unstable

    @Slot(dict)
    def on_shm(self, data):
        st = data.get("status", "UNKNOWN")
        self.val_stat.setText(st)
        self.val_mark_a.setText(f"{data.get('sym_a', 'SymA').replace('/USDT:USDT','')}\n${data.get('mark_a', 0):.2f}")
        self.val_mark_b.setText(f"{data.get('sym_b', 'SymB').replace('/USDT:USDT','')}\n${data.get('mark_b', 0):.2f}")
        self.val_z.setText(f"{data.get('z_score', 0.0):.3f}")
        self.val_az.setText(f"{data.get('anchored_z', 0.0):.3f}")
        
        pnl = float(data.get('unrealized_pnl', 0.0))
        self.val_pnl.setText(f"${pnl:+.2f}")
        if pnl > 0: self.val_pnl.setStyleSheet(f"color: {self.c_green}; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        elif pnl < 0: self.val_pnl.setStyleSheet(f"color: {self.c_red}; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        else: self.val_pnl.setStyleSheet(f"color: white; font-size: 32px; font-weight: bold; background: transparent; border:none;")
        
        r = data.get("current_ratio", 0.0)
        if r > 0.0:
            self.chart_data_ratio.append(r)

    @Slot(dict)
    def on_ticker(self, data):
        if 'SOL' in data: self.val_mark_sol.setText(f"SOL\n${data['SOL']:.2f}")
        if 'BNB' in data: self.val_mark_bnb.setText(f"BNB\n${data['BNB']:.2f}")

    @Slot(str)
    def on_news(self, text):
        self.lbl_news.setText(text)

    def fetch_wallet_poll(self):
        is_tk = self.cb_testnet.isChecked()
        key = self.entry_testnet_api_key.text().strip() if is_tk else self.entry_api_key.text().strip()
        sec = self.entry_testnet_api_secret.text().strip() if is_tk else self.entry_api_secret.text().strip()
        
        self.wt = WalletThread(is_tk, key, sec)
        self.wt.wallet_data.connect(self.val_balance.setText)
        self.wt.start()

    def verify_sizing(self):
        self.lbl_sizing_warn.setText("Fetching...")
        self.lbl_sizing_warn.setStyleSheet("color: white;")
        self.sc = SizingCheckThread(self)
        self.sc.sizing_data.connect(lambda txt, col: (self.lbl_sizing_warn.setText(txt), self.lbl_sizing_warn.setStyleSheet(f"color: {col};")))
        self.sc.start()

    def update_clock(self):
        now = datetime.now()
        self.lbl_clock.setText(now.strftime("%H:%M:%S\n%b %d, %Y"))
        
        if self.cb_testnet.isChecked():
            self.lbl_testnet_badge.show()
        else:
            self.lbl_testnet_badge.hide()

    def update_logs(self):
        if not self.bot_running: return
        msgs = []
        import queue
        try:
            while True:
                msg = self.log_queue.get_nowait()
                is_sensitive = False
                sensitive_words = ["z-score", "half-life", "mean", "deviation", "ratio", "kalman", "zscore", "shm", "math", "z=", "dynz", "decay", "hedge", "hold", "corrmin", "sizing", "notional", "stoploss", "profile"]
                for w in sensitive_words:
                    if w in msg.lower():
                        is_sensitive = True
                        break
                
                if is_sensitive:
                    parts = msg.strip("\n").split(" | ")
                    safe_parts = [p if not any(w in p.lower() for w in sensitive_words) else "[MATH REDACTED]" for p in parts]
                    msgs.append(" | ".join(safe_parts))
                else:
                    msgs.append(msg.strip())
        except queue.Empty: pass
        except Exception: pass
        
        if msgs:
            vbar = self.txt_logs.verticalScrollBar()
            at_bottom = vbar.value() == vbar.maximum()
            for m in msgs:
                self.txt_logs.append(m)
            if at_bottom:
                vbar.setValue(vbar.maximum())

    def update_chart(self):
        if not self.bot_running or len(self.chart_data_ratio) < 2: return
        y = list(self.chart_data_ratio)
        x = list(range(len(y)))
        self.curve.setData(x, y)
        self.baseline.setValue(y[-1] if y else 0)

    def load_env_config(self):
        try:
            load_dotenv(os.path.join(APP_DIR, "key.env"))
            self.entry_api_key.setText(os.getenv("BINANCE_API_KEY", ""))
            self.entry_api_secret.setText(os.getenv("BINANCE_API_SECRET", ""))
            self.entry_testnet_api_key.setText(os.getenv("BINANCE_TESTNET_API_KEY", ""))
            self.entry_testnet_api_secret.setText(os.getenv("BINANCE_TESTNET_API_SECRET", ""))
        except: pass

    def save_env_config(self):
        try:
            ENV_PATH = os.path.join(APP_DIR, "key.env")
            out_lines = []
            if os.path.exists(ENV_PATH):
                with open(ENV_PATH, "r") as f:
                    out_lines = [l for l in f.readlines() if not l.startswith("BINANCE")]
            
            k = self.entry_api_key.text().strip()
            s = self.entry_api_secret.text().strip()
            tk = self.entry_testnet_api_key.text().strip()
            ts = self.entry_testnet_api_secret.text().strip()
            
            if k:
                out_lines.append(f"BINANCE_API_KEY={k}\n")
                os.environ["BINANCE_API_KEY"] = k
            if s:
                out_lines.append(f"BINANCE_API_SECRET={s}\n")
                os.environ["BINANCE_API_SECRET"] = s
            if tk:
                out_lines.append(f"BINANCE_TESTNET_API_KEY={tk}\n")
                os.environ["BINANCE_TESTNET_API_KEY"] = tk
            if ts:
                out_lines.append(f"BINANCE_TESTNET_API_SECRET={ts}\n")
                os.environ["BINANCE_TESTNET_API_SECRET"] = ts
            
            with open(ENV_PATH, "w") as f:
                f.writelines(out_lines)
        except: pass

    def load_yaml_config(self):
        try:
            CONFIG_PATH = os.path.join(APP_DIR, "user_settings.yaml")
            d = {}
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    d = yaml.safe_load(f) or {}

            self.cb_testnet.setChecked(d.get("testnet", False))
            
            syms = d.get("symbols", ["BTC/USDT:USDT", "ETH/USDT:USDT"])
            sym_a = syms[0] if len(syms) > 0 else "BTC/USDT:USDT"
            sym_b = syms[1] if len(syms) > 1 else "ETH/USDT:USDT"
            
            self.combo_sym_a.setCurrentText(sym_a)
            self.combo_sym_b.setCurrentText(sym_b)
            
            pt = d.get("pairs_trading", {})
            self.entry_notional.setText(str(pt.get("notional_per_leg", 160.0)))
            self.entry_zthresh.setText(str(pt.get("z_entry_threshold", 3.0)))
            
            hr = pt.get("hedge_ratio", 0.5)
            if hr == 1.0:
                self.combo_risk_mode.setCurrentText("Pure Neutral (Hedge 1.0)")
            else:
                self.combo_risk_mode.setCurrentText("Conservative (Hedge 0.5)")
                
            if sym_a == "BTC/USDT:USDT" and sym_b == "ETH/USDT:USDT":
                curr = self.combo_strategy.currentText()
                if curr not in ["BTC vs ETH (Ultra-Safe)", "BTC vs ETH (Max Profit)"]:
                    self.combo_strategy.setCurrentText("BTC vs ETH (Ultra-Safe)")
                self.frm_custom.hide()
            elif sym_a == "ETH/USDT:USDT" and sym_b == "SOL/USDT:USDT":
                self.combo_strategy.setCurrentText("ETH vs SOL (Optimized)")
                self.frm_custom.hide()
            else:
                self.combo_strategy.setCurrentText("Custom Pairs")
                self.frm_custom.show()
            
            app_cfg = d.get("app_settings", {})
            self.sw_sound.setChecked(app_cfg.get("sound_alerts", False))
        except: pass

    def save_yaml_config(self):
        try:
            sym_a = self.combo_sym_a.currentText().strip()
            sym_b = self.combo_sym_b.currentText().strip()
            
            if sym_a == sym_b and self.combo_strategy.currentText() == "Custom Pairs":
                QMessageBox.warning(self, "Invalid Pair", "Symbol A and Symbol B cannot be identical!\nThis results in a permanently flat spread.")
                self.combo_strategy.setCurrentText("BTC vs ETH (Ultra-Safe)")
                return
                
            hr_str = self.combo_risk_mode.currentText()
            hr = 1.0 if "1.0" in hr_str else 0.5
            try: z_thresh = float(self.entry_zthresh.text())
            except: z_thresh = 3.0
            
            strat = self.combo_strategy.currentText()
            max_hold_sec = 24 * 3600
            stop_loss_pct = 0.20
            
            if strat == "BTC vs ETH (Ultra-Safe)":
                sym_a = "BTC/USDT:USDT"; sym_b = "ETH/USDT:USDT"
                hr = 0.5; z_thresh = 4.5; max_hold_sec = 24 * 3600; stop_loss_pct = 0.125
            elif strat == "BTC vs ETH (Max Profit)":
                sym_a = "BTC/USDT:USDT"; sym_b = "ETH/USDT:USDT"
                hr = 1.5; z_thresh = 4.0; max_hold_sec = 24 * 3600; stop_loss_pct = 0.125
            elif strat == "ETH vs SOL (Optimized)":
                sym_a = "ETH/USDT:USDT"; sym_b = "SOL/USDT:USDT"
                hr = 0.5; z_thresh = 3.0; max_hold_sec = 12 * 3600; stop_loss_pct = 0.25
            
            cfg = {
                "testnet": self.cb_testnet.isChecked(),
                "symbols": [sym_a, sym_b],
                "context_symbols": ["BTC/USDT:USDT", "SOL/USDT:USDT", "BNB/USDT:USDT", "ETH/USDT:USDT"],
                "pairs_trading": {
                    "notional_per_leg": float(self.entry_notional.text()),
                    "z_entry_threshold": z_thresh,
                    "hedge_ratio": hr,
                    "max_hold_sec": max_hold_sec,
                    "stop_loss_pct": stop_loss_pct
                },
                "app_settings": {
                    "sound_alerts": self.sw_sound.isChecked()
                }
            }
            with open(os.path.join(APP_DIR, "user_settings.yaml"), "w") as f:
                yaml.dump(cfg, f, default_flow_style=False)
        except: pass

    def toggle_bot(self):
        if self.bot_running:
            if "IN SPREAD" in self.val_stat.text():
                reply = QMessageBox.question(self, 'Active Trade Warning', 'A trade is currently open!\n\nForce Close Positions immediately?', QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    import threading
                    threading.Thread(target=self.force_close_all, daemon=True).start()
            self.stop_bot()
        else:
            self.start_bot()

    def start_bot(self):
        try:
            self.save_yaml_config()
            self.save_env_config()
            
            for env_filename in ["key.env", "key_testnet.env"]:
                env_path = os.path.join(APP_DIR, env_filename)
                if os.path.exists(env_path):
                    with open(env_path, "r") as f:
                        for line in f:
                            if "=" in line:
                                k, v = line.strip().split("=", 1)
                                if k.startswith("BINANCE"):
                                    if env_filename == "key_testnet.env" and k in ["BINANCE_API_KEY", "BINANCE_API_SECRET"]:
                                        k = k.replace("BINANCE_", "BINANCE_TESTNET_")
                                    os.environ[k] = v

            self.bot_processes = []
            
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
            
            self.btn_toggle.setText("STOP BOT")
            self.btn_toggle.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {self.c_green}; border: 2px solid {self.c_green}; border-radius: 10px; background: transparent;")
            self.txt_logs.clear()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"START BOT ERROR: {e}")

    def stop_bot(self):
        for p in self.bot_processes:
            try:
                p.terminate()
                p.join(timeout=1.0)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=1.0)
            except: pass
        self.bot_processes = []
        self.bot_running = False
        
        self.btn_toggle.setText("SHUTDOWN (Click to Start)")
        self.btn_toggle.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {self.c_red}; border: 2px solid {self.c_red}; border-radius: 10px; background: transparent;")
        self.val_stat.setText("Bot Offline")

    def panic_action(self):
        reply = QMessageBox.question(self, 'PANIC CLOSE', 'Are you sure you want to market close all open positions immediately?', QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            import threading
            threading.Thread(target=self.force_close_all, daemon=True).start()

    def force_close_all(self):
        import ccxt
        try:
            is_testnet = self.cb_testnet.isChecked()
            api_key = self.entry_testnet_api_key.text().strip() if is_testnet else self.entry_api_key.text().strip()
            secret = self.entry_testnet_api_secret.text().strip() if is_testnet else self.entry_api_secret.text().strip()
            if not api_key or not secret: return
            ex = ccxt.binanceusdm({'apiKey': api_key, 'secret': secret, 'enableRateLimit': True})
            if is_testnet: ex.set_sandbox_mode(True)
            
            positions = ex.fetch_positions()
            for pos in positions:
                amt = float(pos.get("info", {}).get("positionAmt", 0.0))
                sym = pos.get("symbol")
                if abs(amt) > 0:
                    side = "buy" if amt < 0 else "sell"
                    try:
                        ex.create_order(sym, "market", side, abs(amt), params={"reduceOnly": True})
                    except: pass
        except: pass

def main():
    multiprocessing.freeze_support()
    app = QApplication(sys.argv)
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyside6'))
    
    # Global overrides to match exact visual theme
    app.setStyleSheet(app.styleSheet() + """
        QWidget { font-family: Inter, Segoe UI, sans-serif; }
        QFrame { border: none; }
        QTextEdit, QLineEdit { border-radius: 5px; background: #121212; border: 1px solid #333; padding: 5px; }
        QPushButton { border-radius: 5px; background: #2b2b2b; border: 1px solid #444; padding: 8px; color: white; }
        QPushButton:hover { background: #3b3b3b; }
        QComboBox { border-radius: 5px; background: #121212; border: 1px solid #333; padding: 5px; }
    """)
    
    window = ApexCryptoApp()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
