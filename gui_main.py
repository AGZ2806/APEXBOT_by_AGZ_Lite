import os,sys,time,json,threading,subprocess,requests,yaml,customtkinter as ctk
from datetime import datetime
import multiprocessing,webbrowser,matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import numpy as np
CURRENT_VERSION='3.0.7'
if getattr(sys,'frozen',False):APP_DIR=os.path.dirname(sys.executable)
else:APP_DIR=os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)
from scribe import main as scribe_main
from trader_pairs import main as trader_main
def run_scribe():
	import asyncio as B,sys as A,traceback as C;from phase23_lib import TeeLogger as D;import os;os.makedirs('logs',exist_ok=True);A.stdout=D('logs/scribe.log');A.stderr=A.stdout
	try:from scribe import main as E;B.run(E())
	except KeyboardInterrupt:pass
	except Exception as F:print(f"CRITICAL ERROR IN SCRIBE: {F}");C.print_exc()
class ObfuscatedLogger:
	def __init__(A,filename,queue=None):import sys;A.filename=filename;A.queue=queue;A.terminal=sys.stdout
	def write(A,message):
		B=message;import zlib,base64 as C,time;A.terminal.write(B)
		if not B.strip():return
		if A.queue:
			try:A.queue.put_nowait(B)
			except:pass
		try:
			with open(A.filename,'a')as D:E=f"[{time.strftime("%Y-%m-%dT%H:%M:%SZ")}] {B}";F=zlib.compress(E.encode('utf-8'));G=C.b64encode(F).decode('utf-8');D.write(G+'\n')
		except:pass
	def flush(A):A.terminal.flush()
def run_trader(log_queue=None):
	import asyncio as B,sys as A,traceback as C,os;D=os.path.join(APP_DIR,'system.apexlog');A.stdout=ObfuscatedLogger(D,log_queue);A.stderr=A.stdout
	try:from trader_pairs import main as E;B.run(E())
	except KeyboardInterrupt:pass
	except Exception as F:print(f"CRITICAL ERROR IN TRADER: {F}");C.print_exc()
def bootstrap_configs():
	import shutil as B
	if not os.path.exists('key.env'):
		with open('key.env','w')as C:C.write('BINANCE_API_KEY=\nBINANCE_API_SECRET=\n')
	if getattr(sys,'frozen',False):
		D=getattr(sys,'_MEIPASS',os.path.dirname(sys.executable));A=os.path.join(D,'user_settings.yaml')
		if not os.path.exists('user_settings.yaml')and os.path.exists(A):
			try:B.copy2(A,'user_settings.yaml')
			except Exception:pass
bootstrap_configs()
CONFIG_PATH='user_settings.yaml'
ENV_PATH='key.env'
TRADER_LOG='logs/trader_pairs.log'
ctk.set_appearance_mode('Dark')
ctk.set_default_color_theme('blue')
class PairsTraderGUI(ctk.CTk):
	def __init__(A):super().__init__();A.title('Apex Crypto Bot by AGZ');A.geometry('1200x850');A.protocol('WM_DELETE_WINDOW',A.on_closing);A.latency_ms=0;A.is_unstable=False;A.bot_processes=[];A.bot_running=False;A.live_z_score='N/A';A.live_div='N/A';A.live_btc='N/A';A.live_eth='N/A';A.wallet_advice='No wallet data yet...';A.live_pnl='N/A';A.live_mean_ratio='N/A';A.live_std_ratio='N/A';A.live_current_ratio='N/A';A.live_anchored_z='N/A';A.live_status_shm='STOPPED';A.color_bg='#121212';A.color_card='#1E1E1E';A.color_cyan='#00E5FF';A.color_green='#00FF88';A.color_red='#FF3366';A.color_text_muted='#888888';A.grid_rowconfigure(0,weight=1);A.grid_columnconfigure(1,weight=1);A._build_sidebar();A._build_dashboard();A._build_settings();A._build_apikeys();A._build_history();A._build_app_settings();A._build_help();A.select_frame('dashboard');A.load_env_config();A.load_yaml_config();A.fetch_tickers_news_loop();A.load_env_config();threading.Thread(target=A.ping_loop,daemon=True).start();threading.Thread(target=A.shm_tail_loop,daemon=True).start();A.update_ui_loop();A.update_chart_loop()
	def on_closing(A):A.stop_bot();A.destroy();import sys;sys.exit(0)
	def _build_sidebar(A):A.sidebar=ctk.CTkFrame(A,width=220,corner_radius=0,fg_color='#181818');A.sidebar.grid(row=0,column=0,sticky='nsew');A.sidebar.grid_rowconfigure(9,weight=1);C=ctk.CTkFrame(A.sidebar,fg_color='transparent');C.grid(row=0,column=0,padx=20,pady=(20,30),sticky='w');ctk.CTkLabel(C,text='▲',font=ctk.CTkFont(size=24,weight='bold'),text_color=A.color_cyan).pack(side='left',padx=(0,10));ctk.CTkLabel(C,text='APEX',font=ctk.CTkFont(size=20,weight='bold'),text_color='white').pack(side='left');B={'fg_color':'transparent','text_color':'white','anchor':'w','font':ctk.CTkFont(size=14)};A.btn_dash=ctk.CTkButton(A.sidebar,text='Dashboard',fg_color='#2A2A2A',text_color=A.color_cyan,anchor='w',font=ctk.CTkFont(size=14,weight='bold'),command=lambda:A.select_frame('dashboard'));A.btn_dash.grid(row=1,column=0,padx=20,pady=5,sticky='ew');A.btn_settings=ctk.CTkButton(A.sidebar,text='Strategy & Config',command=lambda:A.select_frame('settings'),**B);A.btn_settings.grid(row=2,column=0,padx=20,pady=5,sticky='ew');A.btn_keys=ctk.CTkButton(A.sidebar,text='API Keys',command=lambda:A.select_frame('keys'),**B);A.btn_keys.grid(row=3,column=0,padx=20,pady=5,sticky='ew');A.btn_history=ctk.CTkButton(A.sidebar,text='History',command=lambda:A.select_frame('history'),**B);A.btn_history.grid(row=4,column=0,padx=20,pady=5,sticky='ew');A.btn_settings_app=ctk.CTkButton(A.sidebar,text='Settings',command=lambda:A.select_frame('app_settings'),**B);A.btn_settings_app.grid(row=5,column=0,padx=20,pady=5,sticky='ew');A.btn_help=ctk.CTkButton(A.sidebar,text='Help',command=lambda:A.select_frame('help'),**B);A.btn_help.grid(row=6,column=0,padx=20,pady=5,sticky='ew')
	def _build_dashboard(A):import collections as R;A.frm_dash=ctk.CTkFrame(A,corner_radius=0,fg_color=A.color_bg);A.frm_dash.grid_columnconfigure((0,1),weight=1);A.frm_dash.grid_rowconfigure(4,weight=1);B={'fg_color':A.color_card,'corner_radius':10};C=ctk.CTkFrame(A.frm_dash,fg_color='transparent');C.grid(row=0,column=0,columnspan=2,pady=(20,10),sticky='ew',padx=30);ctk.CTkLabel(C,text='Dashboard - Real-Time Stats',font=ctk.CTkFont(family='Inter',size=24,weight='bold'),text_color='#FFFFFF').pack(side='left');A.lbl_news=ctk.CTkLabel(C,text='Loading Live Crypto News...',font=ctk.CTkFont(family='Inter',size=16,slant='italic'),text_color=A.color_cyan);A.lbl_news.pack(side='left',padx=30);A.btn_panic=ctk.CTkButton(C,text='🚨 PANIC CLOSE ALL',fg_color='#AA0000',hover_color='#FF0000',font=ctk.CTkFont(weight='bold'),command=A.panic_action);A.btn_panic.pack(side='left',padx=20);A.lbl_clock=ctk.CTkLabel(C,text='--:-- --\n--- --, ----',font=ctk.CTkFont(family='Inter',size=12),text_color=A.color_text_muted,justify='right');A.lbl_clock.pack(side='right');A.btn_update=ctk.CTkButton(C,text='⚠️ Update Available!',fg_color='#FFA500',hover_color='#FF8C00',text_color='black',font=ctk.CTkFont(weight='bold'),command=lambda:webbrowser.open('https://apexbotagz.com/'));F=ctk.CTkFrame(A.frm_dash,fg_color='transparent');F.grid(row=1,column=0,columnspan=2,sticky='ew',padx=30,pady=5);F.grid_columnconfigure((0,1),weight=1);G=ctk.CTkFrame(F,**B);G.grid(row=0,column=0,sticky='nsew',padx=(0,10));ctk.CTkLabel(G,text='Market Overview',font=ctk.CTkFont(family='Inter',size=16),text_color='white').pack(anchor='w',padx=15,pady=(10,5));D=ctk.CTkFrame(G,fg_color='transparent');D.pack(fill='x',padx=15,pady=(0,10));D.grid_columnconfigure((0,1),weight=1);A.val_mark_a=ctk.CTkLabel(D,text='Sym A\n$---',font=ctk.CTkFont(family='Inter',size=14),justify='left');A.val_mark_a.grid(row=0,column=0,sticky='w');A.val_mark_b=ctk.CTkLabel(D,text='Sym B\n$---',font=ctk.CTkFont(family='Inter',size=14),justify='left');A.val_mark_b.grid(row=0,column=1,sticky='w');A.val_mark_sol=ctk.CTkLabel(D,text='SOL\n$---',font=ctk.CTkFont(family='Inter',size=14),justify='left');A.val_mark_sol.grid(row=0,column=2,sticky='w',padx=10);A.val_mark_bnb=ctk.CTkLabel(D,text='BNB\n$---',font=ctk.CTkFont(family='Inter',size=14),justify='left');A.val_mark_bnb.grid(row=0,column=3,sticky='w');H=ctk.CTkFrame(F,**B);H.grid(row=0,column=1,sticky='nsew',padx=(10,0));A.lbl_balance=ctk.CTkLabel(H,text='Account Balance',font=ctk.CTkFont(family='Inter',size=14,weight='bold'),text_color='#A0A0A0');A.lbl_balance.pack(anchor='w',padx=15,pady=(10,5));A.val_balance=ctk.CTkLabel(H,text='Scanning...',font=ctk.CTkFont(family='Inter',size=16,weight='bold'),text_color='white');A.val_balance.pack(anchor='w',padx=15,pady=(0,10));E=ctk.CTkFrame(A.frm_dash,fg_color='transparent');E.grid(row=2,column=0,columnspan=2,sticky='ew',padx=30,pady=10);E.grid_columnconfigure((0,1,2,3),weight=1);I=ctk.CTkFrame(E,fg_color=A.color_card,corner_radius=10,border_width=2,border_color=A.color_cyan);I.grid(row=0,column=0,sticky='nsew',padx=(0,5));ctk.CTkLabel(I,text='Live Z-Score:',font=ctk.CTkFont(family='Inter',size=14),text_color='white').pack(anchor='w',padx=15,pady=(10,0));A.val_z=ctk.CTkLabel(I,text='N/A',font=ctk.CTkFont(family='Inter',size=36,weight='bold'),text_color=A.color_cyan);A.val_z.pack(pady=(5,5));J=ctk.CTkFrame(E,fg_color=A.color_card,corner_radius=10,border_width=2,border_color='#AA00FF');J.grid(row=0,column=1,sticky='nsew',padx=5);ctk.CTkLabel(J,text='Anchored Z:',font=ctk.CTkFont(family='Inter',size=14),text_color='white').pack(anchor='w',padx=15,pady=(10,0));A.val_az=ctk.CTkLabel(J,text='N/A',font=ctk.CTkFont(family='Inter',size=36,weight='bold'),text_color='#AA00FF');A.val_az.pack(pady=(5,5));K=ctk.CTkFrame(E,**B);K.grid(row=0,column=2,sticky='nsew',padx=5);ctk.CTkLabel(K,text='Estimated PnL:',font=ctk.CTkFont(family='Inter',size=14),text_color='white').pack(anchor='w',padx=15,pady=(10,0));A.val_pnl=ctk.CTkLabel(K,text='N/A',font=ctk.CTkFont(family='Inter',size=36,weight='bold'),text_color=A.color_green);A.val_pnl.pack(pady=(10,5));L=ctk.CTkFrame(E,**B);L.grid(row=0,column=3,sticky='nsew',padx=(5,0));ctk.CTkLabel(L,text='Status:',font=ctk.CTkFont(family='Inter',size=14),text_color='white').pack(anchor='w',padx=15,pady=(10,0));A.lbl_z_sub=ctk.CTkLabel(L,text='Bot Offline',font=ctk.CTkFont(family='Inter',size=16,weight='bold'),text_color=A.color_text_muted,justify='left');A.lbl_z_sub.pack(anchor='w',padx=15,pady=(10,5));M=ctk.CTkFrame(A.frm_dash,fg_color='transparent');M.grid(row=3,column=0,columnspan=2,sticky='ew',padx=30,pady=5);M.grid_columnconfigure(0,weight=1);N=ctk.CTkFrame(M,fg_color='transparent');N.grid(row=0,column=0,sticky='nsew');N.grid_columnconfigure(0,weight=1);A.btn_toggle=ctk.CTkButton(N,text='SHUTDOWN (Click to Start)',font=ctk.CTkFont(size=18,weight='bold'),fg_color='transparent',border_width=2,border_color=A.color_red,text_color=A.color_red,hover_color='#330011',height=60,command=A.toggle_bot);A.btn_toggle.grid(row=0,column=0,sticky='nsew');O=ctk.CTkFrame(A.frm_dash,fg_color='transparent');O.grid(row=4,column=0,columnspan=2,sticky='nsew',padx=30,pady=10);O.grid_columnconfigure(0,weight=1);A.panel_chart=ctk.CTkFrame(O,**B);A.panel_chart.grid(row=0,column=0,sticky='nsew');A.figure=Figure(figsize=(5,3),dpi=100,facecolor=A.color_card);A.ax=A.figure.add_subplot(111);A.ax.set_facecolor(A.color_card);A.ax.tick_params(colors=A.color_text_muted,labelsize=8);A.ax.spines['bottom'].set_color('#333333');A.ax.spines['top'].set_color('#333333');A.ax.spines['right'].set_color('#333333');A.ax.spines['left'].set_color('#333333');A.ax.set_title('Live Price Ratio (Asset Correlation Tracking)',color='white',fontsize=10);A.chart_canvas=FigureCanvasTkAgg(A.figure,master=A.panel_chart);A.chart_canvas.get_tk_widget().pack(fill='both',expand=True,padx=5,pady=5);A.chart_data_ratio=R.deque([.0]*120,maxlen=120);P=ctk.CTkFrame(A.frm_dash,fg_color='transparent');P.grid(row=5,column=0,columnspan=2,sticky='nsew',padx=30,pady=5);P.grid_columnconfigure(0,weight=1);Q=ctk.CTkFrame(P,**B);Q.grid(row=0,column=0,sticky='nsew');ctk.CTkLabel(Q,text='Sanitized Live System Logs (Math Protected)',font=ctk.CTkFont(family='Inter',size=14),text_color='white').pack(anchor='w',padx=15,pady=(10,0));A.txt_logs=ctk.CTkTextbox(Q,height=100,fg_color='#1E1E1E',text_color='#A0A0A0',font=ctk.CTkFont(family='Consolas',size=11));A.txt_logs.pack(fill='both',expand=True,padx=15,pady=10);A.txt_logs.configure(state='disabled');A.lbl_build_version=ctk.CTkLabel(A.frm_dash,text=f"Build: {CURRENT_VERSION}",font=ctk.CTkFont(family='Inter',size=10),text_color='#555555');A.lbl_build_version.grid(row=6,column=1,sticky='se',padx=30,pady=(0,5));threading.Thread(target=A.check_for_updates,daemon=True).start()
	def check_for_updates(B):
		import urllib.request,json
		try:
			D=urllib.request.Request('https://apexbotagz.com/version.json',headers={'User-Agent':'Mozilla/5.0'})
			with urllib.request.urlopen(D,timeout=5)as E:
				F=json.loads(E.read().decode());A=F.get('version','0.0.0')
				try:G=tuple(map(int,A.split('.')));H=tuple(map(int,CURRENT_VERSION.split('.')));C=G>H
				except ValueError:C=A!=CURRENT_VERSION and A>CURRENT_VERSION
				if C:B.after(0,lambda:B.btn_update.pack(side='right',padx=20))
		except Exception as I:print(f"Failed to check for updates: {I}")
	def fetch_tickers_news_loop(D):
		import requests as F,time,threading as A
		def B():
			B=['Live Market Tracking Active...'];E=0
			while True:
				try:
					G=F.get('https://fapi.binance.com/fapi/v1/ticker/price?symbols=["SOLUSDT","BNBUSDT"]',timeout=5).json()
					for C in G:
						if C['symbol']=='SOLUSDT':D.val_mark_sol.configure(text=f"SOL\n${float(C["price"]):.2f}")
						elif C['symbol']=='BNBUSDT':D.val_mark_bnb.configure(text=f"BNB\n${float(C["price"]):.2f}")
				except:pass
				try:
					if E%50==0:import xml.etree.ElementTree as H;I=F.get('https://cointelegraph.com/rss',headers={'User-Agent':'Mozilla/5.0'},timeout=5).text;J=H.fromstring(I);B=[A.find('title').text for A in J.findall('.//item')[:5]if A.find('title')is not None]
					if B:
						A=B[E%len(B)];A=A.replace('&apos;',"'").replace('&quot;','"').replace('&#39;',"'").replace('&amp;','&')
						if len(A)>90:A=A[:87]+'...'
						D.lbl_news.configure(text=f"📰 {A}")
				except:pass
				E+=1;time.sleep(5)
		A.Thread(target=B,daemon=True).start()
	def fetch_binance_pairs(B):
		import requests as C
		try:
			D=C.get('https://fapi.binance.com/fapi/v1/exchangeInfo',timeout=5).json();A=[A['symbol'].replace('USDT','/USDT:USDT')for A in D['symbols']if A['contractType']=='PERPETUAL'and A['quoteAsset']=='USDT'];A.sort()
			if A:B.combo_sym_a.configure(values=A);B.combo_sym_b.configure(values=A)
		except Exception as E:print('Failed to fetch pairs:',E)
	def _on_strategy_change(A,choice):
		B=choice
		if'BTC vs ETH'in B:A.combo_sym_a.set('BTC/USDT:USDT');A.combo_sym_b.set('ETH/USDT:USDT');A.frm_symbols.pack_forget();A.lbl_zthresh.pack_forget();A.entry_zthresh.pack_forget();A.save_yaml_config();print(f"Strategy changed to {B}")
		elif B=='Custom Pairs':A.frm_symbols.pack(fill='x',pady=0,after=A.warn_frame);A.lbl_zthresh.pack(anchor='w',padx=20,pady=(20,0));A.entry_zthresh.pack(anchor='w',padx=20);A.entry_zthresh.configure(state='normal');print('Strategy changed to Custom Pairs. Please select symbols manually.')
	def verify_sizing(A):
		import threading as B,ccxt,os;from dotenv import load_dotenv as N;A.lbl_sizing_warn.configure(text='Fetching wallet balance from Binance...',text_color='white')
		def C():
			try:
				N(os.path.join(APP_DIR,'key.env'));H=os.getenv('BINANCE_API_KEY');I=os.getenv('BINANCE_API_SECRET')
				if not H or not I:A.lbl_sizing_warn.configure(text='Missing API Keys! Cannot verify limits.',text_color=A.color_red);return
				O=ccxt.binanceusdm({'apiKey':H,'secret':I,'enableRateLimit':True,'options':{'broker':{'future':'x-XSY2ZGS8','spot':'x-XSY2ZGS8','swap':'x-XSY2ZGS8','linear':'x-XSY2ZGS8','delivery':'x-XSY2ZGS8'}}});P=O.fetch_balance();B=float(P.get('USDT',{}).get('total',.0))
				try:C=float(A.entry_notional.get())
				except:C=.0
				D=1.
				try:
					import trader_pairs as J;K=A.combo_sym_a.get();L=A.combo_sym_b.get();F=J.PairsTrader.PAIR_PROFILES.get((K,L))or J.PairsTrader.PAIR_PROFILES.get((L,K))
					if F and'HEDGE_RATIO'in F:D=float(F['HEDGE_RATIO'])
				except Exception as G:print(f"Error reading hedge ratio for GUI check: {G}")
				if D<1. and D>.0:M=C+C/D
				else:M=C+C*D
				Q=M/5.;E=Q/B*100 if B>0 else 999
				if E<=80:A.lbl_sizing_warn.configure(text=f"Wallet: ${B:.2f} | Usage: {E:.1f}% (SAFE TIER)",text_color=A.color_green)
				elif E<=95:A.lbl_sizing_warn.configure(text=f"Wallet: ${B:.2f} | Usage: {E:.1f}% (CAUTION TIER)",text_color='yellow')
				else:A.lbl_sizing_warn.configure(text=f"Wallet: ${B:.2f} | Usage: {E:.1f}% (BLOCKED: Exceeds 95% Hard Cap)",text_color=A.color_red)
			except Exception as G:A.lbl_sizing_warn.configure(text=f"Error fetching balance: {G}",text_color=A.color_red)
		B.Thread(target=C,daemon=True).start()
	def _build_settings(A):import threading as C;A.frm_settings=ctk.CTkFrame(A,corner_radius=0,fg_color='transparent');ctk.CTkLabel(A.frm_settings,text='Strategy & Configuration',font=ctk.CTkFont(size=24,weight='bold')).pack(pady=20,anchor='w',padx=20);ctk.CTkLabel(A.frm_settings,text='Select Predefined Strategy Template:',font=ctk.CTkFont(size=14,weight='bold')).pack(anchor='w',padx=20,pady=(0,5));A.strategy_var=ctk.StringVar(value='BTC vs ETH (Optimized)');A.strategy_dropdown=ctk.CTkOptionMenu(A.frm_settings,values=['BTC vs ETH (Optimized)','Custom Pairs'],variable=A.strategy_var,command=A._on_strategy_change,width=300);A.strategy_dropdown.pack(anchor='w',padx=20,pady=(0,15));A.warn_frame=ctk.CTkFrame(A.frm_settings,fg_color='#4a3e00');A.warn_frame.pack(fill='x',padx=20,pady=10);ctk.CTkLabel(A.warn_frame,text='⚠️ CAUTION: BTC/USDT vs ETH/USDT is mathematically optimized.\nSelecting custom pairs enforces safe fallbacks (Z=3.0, Hedge=1.0)!',text_color='yellow').pack(pady=10);A.frm_symbols=ctk.CTkFrame(A.frm_settings,fg_color='transparent');ctk.CTkLabel(A.frm_symbols,text='Symbol A (USDT Perpetual):').pack(anchor='w',padx=20,pady=(10,0));A.combo_sym_a=ctk.CTkComboBox(A.frm_symbols,values=['BTC/USDT:USDT','ETH/USDT:USDT'],width=300);A.combo_sym_a.pack(anchor='w',padx=20);ctk.CTkLabel(A.frm_symbols,text='Symbol B (USDT Perpetual):').pack(anchor='w',padx=20,pady=(10,0));A.combo_sym_b=ctk.CTkComboBox(A.frm_symbols,values=['BTC/USDT:USDT','ETH/USDT:USDT'],width=300);A.combo_sym_b.pack(anchor='w',padx=20);ctk.CTkLabel(A.frm_settings,text='Notional Per Leg ($):').pack(anchor='w',padx=20,pady=(20,0));B=ctk.CTkFrame(A.frm_settings,fg_color='transparent');B.pack(fill='x',padx=20);A.entry_notional=ctk.CTkEntry(B,width=200);A.entry_notional.pack(side='left');A.btn_check_lim=ctk.CTkButton(B,text='Verify Sizing Limits',command=A.verify_sizing);A.btn_check_lim.pack(side='left',padx=10);A.lbl_sizing_warn=ctk.CTkLabel(A.frm_settings,text='',text_color='yellow');A.lbl_sizing_warn.pack(anchor='w',padx=20);A.frm_z_container=ctk.CTkFrame(A.frm_settings,fg_color='transparent');A.frm_z_container.pack(fill='x');A.lbl_zthresh=ctk.CTkLabel(A.frm_z_container,text='Z-Score Entry Threshold (Optional Override):');A.lbl_zthresh.pack(anchor='w',padx=20,pady=(20,0));A.entry_zthresh=ctk.CTkEntry(A.frm_z_container,width=300);A.entry_zthresh.pack(anchor='w',padx=20);ctk.CTkLabel(A.frm_settings,text='Risk Mode (Hedge Ratio):').pack(anchor='w',padx=20,pady=(20,0));A.combo_risk_mode=ctk.CTkOptionMenu(A.frm_settings,values=['Conservative (Hedge 0.5)','Pure Neutral (Hedge 1.0)'],width=300);A.combo_risk_mode.pack(anchor='w',padx=20);ctk.CTkButton(A.frm_settings,text='Save Settings',command=A.save_yaml_config).pack(anchor='w',padx=20,pady=30);C.Thread(target=A.fetch_binance_pairs,daemon=True).start()
	def _build_apikeys(A):
		A.frm_keys=ctk.CTkFrame(A,corner_radius=0,fg_color='transparent');ctk.CTkLabel(A.frm_keys,text='API Keys (key.env)',font=ctk.CTkFont(size=24,weight='bold')).pack(pady=20,anchor='w',padx=20)
		try:import urllib.request;D=urllib.request.urlopen('https://api.ipify.org',timeout=3).read().decode('utf8');B=f"Your Public IP Address (For Binance Whitelist): {D}"
		except Exception:B='Your Public IP Address: [Unable to fetch - check internet]'
		C=ctk.CTkFrame(A.frm_keys,fg_color='#1f538d');C.pack(fill='x',padx=20,pady=10);ctk.CTkLabel(C,text=B,text_color='white',font=ctk.CTkFont(weight='bold')).pack(pady=10);ctk.CTkLabel(A.frm_keys,text='Binance API Key:').pack(anchor='w',padx=20,pady=(10,0));A.entry_api_key=ctk.CTkEntry(A.frm_keys,width=500);A.entry_api_key.pack(anchor='w',padx=20);ctk.CTkLabel(A.frm_keys,text='Binance API Secret:').pack(anchor='w',padx=20,pady=(10,0));A.entry_api_secret=ctk.CTkEntry(A.frm_keys,width=500,show='*');A.entry_api_secret.pack(anchor='w',padx=20);ctk.CTkButton(A.frm_keys,text='Save Keys',command=A.save_env_config).pack(anchor='w',padx=20,pady=30)
	def _build_history(A):A.frm_history=ctk.CTkFrame(A,corner_radius=0,fg_color='transparent');ctk.CTkLabel(A.frm_history,text='Data & Diagnostics',font=ctk.CTkFont(size=24,weight='bold')).pack(pady=20,anchor='w',padx=20);B='Manage your trade history and export diagnostic logs for support.\nDiagnostic logs are obfuscated to protect your proprietary mathematical algorithms.';ctk.CTkLabel(A.frm_history,text=B,font=ctk.CTkFont(size=14),justify='left',text_color=A.color_text_muted).pack(anchor='w',padx=20,pady=(0,20));C=ctk.CTkButton(A.frm_history,text='📂 Open CSV History Folder',command=A.open_history_folder,fg_color='#2A2A2A',width=250);C.pack(anchor='w',padx=20,pady=10);D=ctk.CTkButton(A.frm_history,text='🛡️ Export Diagnostic Logs',command=A.export_diagnostics,fg_color='#4A2000',width=250);D.pack(anchor='w',padx=20,pady=10)
	def open_history_folder(B):
		import os;A=os.path.join(APP_DIR,'data')
		if not os.path.exists(A):os.makedirs(A,exist_ok=True)
		os.startfile(A)
	def export_diagnostics(G):
		import os as A,tkinter.messagebox;B=A.path.join(APP_DIR,'system.apexlog')
		if not A.path.exists(B):tkinter.messagebox.showinfo('Export','No diagnostic logs found yet.');return
		D=A.path.join(A.environ['USERPROFILE'],'Desktop');C=A.path.join(D,'apex_diagnostics.apexlog')
		try:import shutil as E;E.copy2(B,C);tkinter.messagebox.showinfo('Export Success',f"Obfuscated diagnostics exported to Desktop:\n{C}")
		except Exception as F:tkinter.messagebox.showerror('Export Failed',f"Failed to export: {F}")
	def toggle_startup(D):
		import os as A,win32com.client,sys;B=A.path.join(A.environ['APPDATA'],'Microsoft\\Windows\\Start Menu\\Programs\\Startup\\ApexBot.lnk')
		if D.sw_startup.get()==1:E=win32com.client.Dispatch('WScript.Shell');C=E.CreateShortCut(B);C.Targetpath=sys.executable;C.WorkingDirectory=A.path.dirname(sys.executable);C.save()
		elif A.path.exists(B):A.remove(B)
	def toggle_sound(A):A.save_yaml_config()
	def _build_app_settings(A):
		A.frm_app_settings=ctk.CTkFrame(A,corner_radius=0,fg_color='transparent');ctk.CTkLabel(A.frm_app_settings,text='App Settings',font=ctk.CTkFont(size=24,weight='bold')).pack(pady=20,anchor='w',padx=20)
		def B(mode):ctk.set_appearance_mode(mode)
		ctk.CTkLabel(A.frm_app_settings,text='UI Theme:').pack(anchor='w',padx=20,pady=(10,0));A.theme_combo=ctk.CTkComboBox(A.frm_app_settings,values=['Dark','Light'],command=B);A.theme_combo.pack(anchor='w',padx=20);A.theme_combo.set('Dark');A.sw_startup=ctk.CTkSwitch(A.frm_app_settings,text='Start Bot on Windows Startup',command=A.toggle_startup);A.sw_startup.pack(anchor='w',padx=20,pady=20);A.sw_sound=ctk.CTkSwitch(A.frm_app_settings,text='Enable Sound Alerts on Trade',command=A.toggle_sound);A.sw_sound.pack(anchor='w',padx=20,pady=(0,20))
	def _build_help(A):A.frm_help=ctk.CTkFrame(A,corner_radius=0,fg_color='transparent');ctk.CTkLabel(A.frm_help,text='Help & Support',font=ctk.CTkFont(size=24,weight='bold')).pack(pady=20,anchor='w',padx=20);B='Apex Crypto Bot uses Statistical Arbitrage to trade correlated perpetual futures.\n\nZ-Score Metrics:\n- Live Z-Score: The current deviation from the mean.\n- Anchored Z-Score: Protects against extreme structural market breaks.\n\nNotional Sizing:\nThe Notional setting represents the dollar value placed on EACH leg of the trade. A $160 setting means $160 Long and $160 Short simultaneously.\n\nSupport Details:\nTelegram: @AGZ2806\nEmail: support@apexbotagz.com\nWebsite: https://apexbotagz.com/';C=ctk.CTkLabel(A.frm_help,text=B,justify='left',font=ctk.CTkFont(size=14));C.pack(anchor='w',padx=20,pady=10)
	def ping_loop(A):
		while True:
			try:
				B=time.time();C=requests.get('https://fapi.binance.com/fapi/v1/ping',timeout=5)
				if C.status_code==200:A.latency_ms=int((time.time()-B)*1000);A.is_unstable=A.latency_ms>5000
				else:A.latency_ms=9999;A.is_unstable=True
			except:A.latency_ms=9999;A.is_unstable=True
			time.sleep(3)
	def shm_tail_loop(A):
		import time as D
		try:from phase23_shm import SharedMemoryManager as H,SIGNALS_SHM_NAME as I
		except ImportError:return
		C=None
		while True:
			if not A.bot_running:A.live_status_shm='STOPPED';D.sleep(1);continue
			if C is None:
				try:C=H(is_writer=False,name=I)
				except Exception:D.sleep(1);continue
			try:
				B=C.read()
				if B:
					A.live_status_shm=B.get('status','UNKNOWN');A.live_btc=f"{B.get("mark_a",0):.2f}";A.live_eth=f"{B.get("mark_b",0):.2f}";A.live_sym_a=B.get('sym_a','SymA').replace('/USDT:USDT','');A.live_sym_b=B.get('sym_b','SymB').replace('/USDT:USDT','');E=B.get('z_score',.0);F=B.get('anchored_z',.0);A.live_z_score=f"{E:+.2f}"if abs(E)>.0001 else'Gathering Data...';A.live_anchored_z=f"{F:+.2f}"if abs(F)>.0001 else'Gathering Data...';J=B.get('estimated_pnl',0);A.live_pnl=f"${J:+.2f}";A.wallet_advice=B.get('wallet_advice','No wallet data...');K=float(B.get('mark_a',0));G=float(B.get('mark_b',0))
					if hasattr(A,'chart_data_ratio')and G>0:A.chart_data_ratio.append(K/G)
			except Exception:C=None
			D.sleep(1)
	def update_chart_loop(A):
		try:
			if getattr(A,'bot_running',False)and hasattr(A,'chart_data_ratio')and len(A.chart_data_ratio)>0:
				B='#AA00FF'
				if not hasattr(A,'ratio_line'):A.ax.clear();A.ratio_line,=A.ax.plot(range(len(A.chart_data_ratio)),A.chart_data_ratio,color=B,linewidth=1.5,label='Price Ratio');A.ax.set_facecolor(A.color_card);A.ax.tick_params(colors=A.color_text_muted,labelsize=8);A.ax.spines['bottom'].set_color('#333333');A.ax.spines['top'].set_color('#333333');A.ax.spines['right'].set_color('#333333');A.ax.spines['left'].set_color('#333333')
				else:A.ratio_line.set_ydata(A.chart_data_ratio);A.ratio_line.set_xdata(range(len(A.chart_data_ratio)));A.ax.relim();A.ax.autoscale_view()
				if hasattr(A,'fill_col'):
					try:A.fill_col.remove()
					except:pass
				A.fill_col=A.ax.fill_between(range(len(A.chart_data_ratio)),A.chart_data_ratio,min(A.chart_data_ratio),color=B,alpha=.1);A.chart_canvas.draw_idle()
		except Exception:pass
		A.after(2000,A.update_chart_loop)
	def update_ui_loop(A):
		try:
			from datetime import datetime as F;G=F.now();A.lbl_clock.configure(text=G.strftime('%I:%M %p\n%b %d, %Y'))
			if A.bot_running:A.btn_toggle.configure(text='STOP BOT\n(RUNNING)',text_color=A.color_red,border_color=A.color_red,fg_color='#330011')
			else:H='START BOT\n(SHUTDOWN)'if not A.is_unstable else'START BOT\n(HIGH LATENCY)';A.btn_toggle.configure(text=H,text_color=A.color_green,border_color=A.color_green,fg_color='transparent',state='normal')
			if hasattr(A,'live_sym_a'):A.val_mark_a.configure(text=f"{A.live_sym_a}\n${A.live_btc}");A.val_mark_b.configure(text=f"{A.live_sym_b}\n${A.live_eth}")
			if hasattr(A,'wallet_advice'):
				if'??'in A.wallet_advice:A.val_balance.configure(text=A.wallet_advice.replace('?? ','⚠️ '),text_color=A.color_red)
				else:A.val_balance.configure(text=A.wallet_advice,text_color='white')
			C=getattr(A,'positions_kept_open',False)
			if A.bot_running or C:
				A.combo_sym_a.configure(state='disabled');A.combo_sym_b.configure(state='disabled');A.strategy_dropdown.configure(state='disabled')
				if C and not A.bot_running:A.lbl_sizing_warn.configure(text='WARNING: Positions kept open! Symbols locked.',text_color='yellow')
			else:A.combo_sym_a.configure(state='normal');A.combo_sym_b.configure(state='normal');A.strategy_dropdown.configure(state='normal')
			if A.bot_running and A.live_status_shm!='STOPPED':
				A.val_z.configure(text=A.live_z_score);A.val_az.configure(text=A.live_anchored_z);A.val_pnl.configure(text=A.live_pnl)
				if'IN SPREAD'in A.live_status_shm:
					if not getattr(A,'was_in_spread',False):
						A.was_in_spread=True
						if hasattr(A,'sw_sound')and A.sw_sound.get()==1:
							import threading as I
							def J():import winsound as A,time;A.Beep(1000,200);time.sleep(.1);A.Beep(1500,200)
							I.Thread(target=J,daemon=True).start()
					A.lbl_z_sub.configure(text='In Spread (Active)',text_color=A.color_green)
				else:A.was_in_spread=False;A.lbl_z_sub.configure(text='Scanning Market',text_color=A.color_text_muted)
			else:A.val_z.configure(text='N/A',text_color='#555555');A.val_az.configure(text='N/A',text_color='#555555');A.val_pnl.configure(text='N/A',text_color='#555555');A.lbl_z_sub.configure(text='Bot Offline',text_color='#555555')
		except Exception as L:pass
		if hasattr(A,'log_queue'):
			import queue;B=0;A.txt_logs.configure(state='normal')
			while B<50:
				try:
					D=A.log_queue.get_nowait();K=['z-score','half-life','mean','deviation','ratio','kalman','zscore','shm','math','z=','dynz','decay','hedge','hold','corrmin','sizing','notional','stoploss','profile']
					if any(A in D.lower()for A in K):continue
					A.txt_logs.insert('end',D);B+=1
				except queue.Empty:break
			if B>0:
				E=int(float(A.txt_logs.index('end-1c')))
				if E>1000:A.txt_logs.delete('1.0',f"{E-1000+1}.0")
				A.txt_logs.see('end')
			A.txt_logs.configure(state='disabled')
		A.after(500,A.update_ui_loop)
	def load_yaml_config(A):
		try:
			import yaml,os;F=os.path.join(APP_DIR,'user_settings.yaml')
			if os.path.exists(F):
				with open(F,'r')as G:B=yaml.safe_load(G)
				C=B.get('symbols',['BTC/USDT:USDT','ETH/USDT:USDT'])[0];D=B.get('symbols',['BTC/USDT:USDT','ETH/USDT:USDT'])[1];A.combo_sym_a.set(C);A.combo_sym_b.set(D);A.loaded_sym_a=C;A.loaded_sym_b=D
				if C=='BTC/USDT:USDT'and D=='ETH/USDT:USDT':A.strategy_var.set('BTC vs ETH (Optimized)');A.frm_symbols.pack_forget()
				else:A.strategy_var.set('Custom Pairs');A.frm_symbols.pack(fill='x',pady=0,after=A.warn_frame)
				E=B.get('pairs_trading',{});A.entry_notional.delete(0,'end');A.entry_notional.insert(0,str(E.get('notional_per_leg',16e1)));A.entry_zthresh.configure(state='normal');A.entry_zthresh.delete(0,'end');A.entry_zthresh.insert(0,str(E.get('z_entry_threshold',3.)))
				if C=='BTC/USDT:USDT'and D=='ETH/USDT:USDT':A.lbl_zthresh.pack_forget();A.entry_zthresh.pack_forget()
				else:A.lbl_zthresh.pack(anchor='w',padx=20,pady=(20,0));A.entry_zthresh.pack(anchor='w',padx=20)
				H=E.get('hedge_ratio',.5)
				if H==1.:A.combo_risk_mode.set('Pure Neutral (Hedge 1.0)')
				else:A.combo_risk_mode.set('Conservative (Hedge 0.5)')
				I=B.get('app_settings',{})
				if I.get('sound_alerts',False):A.sw_sound.select()
				else:A.sw_sound.deselect()
		except Exception as J:print(f"Failed to load yaml config: {J}")
	def save_yaml_config(A):
		try:
			import yaml,os;D=A.combo_sym_a.get().strip();E=A.combo_sym_b.get().strip()
			if D!=getattr(A,'loaded_sym_a','')or E!=getattr(A,'loaded_sym_b',''):
				import time as F;G=getattr(A,'last_pair_change_time',0)
				if F.time()-G<300:import tkinter.messagebox;M=int(300-(F.time()-G));tkinter.messagebox.showerror('Rate Limit Error',f"Binance API Protection: You can only change trading pairs once every 5 minutes.\nPlease wait {M} seconds.");return
				A.last_pair_change_time=F.time();A.loaded_sym_a=D;A.loaded_sym_b=E
			N=A.combo_risk_mode.get();B=1. if'1.0'in N else .5
			try:
				import ccxt,os;from dotenv import load_dotenv as O;O(os.path.join(APP_DIR,'key.env'));H=os.getenv('BINANCE_API_KEY');I=os.getenv('BINANCE_API_SECRET')
				if H and I:
					P=ccxt.binanceusdm({'apiKey':H,'secret':I,'enableRateLimit':True,'options':{'broker':{'future':'x-XSY2ZGS8','spot':'x-XSY2ZGS8','swap':'x-XSY2ZGS8','linear':'x-XSY2ZGS8','delivery':'x-XSY2ZGS8'}}});Q=P.fetch_balance();J=float(Q.get('USDT',{}).get('total',.0));C=float(A.entry_notional.get())
					if B<1. and B>.0:K=(C+C/B)/5.
					else:K=(C+C*B)/5.
					L=K/J*100 if J>0 else 0
					if L>95:import tkinter.messagebox;tkinter.messagebox.showerror('Hard Cap Exceeded',f"You are attempting to use {L:.1f}% of your wallet capacity.\nThe maximum allowed is 95%.\nPlease lower your Notional Per Leg or contact the developer for 100% margin overrides.");return
			except Exception:pass
			R={'symbols':[D,E],'pairs_trading':{'notional_per_leg':float(A.entry_notional.get()),'z_entry_threshold':float(A.entry_zthresh.get())if A.entry_zthresh.get()else 3.,'hedge_ratio':B},'app_settings':{'sound_alerts':bool(A.sw_sound.get())}};S=os.path.join(APP_DIR,'user_settings.yaml')
			with open(S,'w')as T:yaml.dump(R,T,default_flow_style=False)
			print('Config Saved!')
		except Exception as U:print(f"Failed to save config: {U}")
	def load_env_config(A):
		try:
			import os;B=os.path.join(APP_DIR,'key.env')
			if os.path.exists(B):
				with open(B,'r')as F:
					for C in F:
						if'='in C:
							D,E=C.strip().split('=',1)
							if D=='BINANCE_API_KEY':A.entry_api_key.delete(0,'end');A.entry_api_key.insert(0,E)
							elif D=='BINANCE_API_SECRET':A.entry_api_secret.delete(0,'end');A.entry_api_secret.insert(0,E)
		except Exception as G:print(f"Failed to load env: {G}")
	def save_env_config(D):
		try:
			import os;B=os.path.join(APP_DIR,'key.env');A=[]
			if os.path.exists(B):
				with open(B,'r')as C:A=C.readlines()
			A=[A for A in A if not A.startswith('BINANCE_API_KEY=')and not A.startswith('BINANCE_API_SECRET=')];A.append(f"BINANCE_API_KEY={D.entry_api_key.get().strip()}\n");A.append(f"BINANCE_API_SECRET={D.entry_api_secret.get().strip()}\n")
			with open(B,'w')as C:C.writelines(A)
			print('Env config saved!')
		except Exception as E:print(f"Failed to save env: {E}")
	def select_frame(A,name):
		B=name;A.btn_dash.configure(fg_color='#2A2A2A'if B=='dashboard'else'transparent');A.btn_settings.configure(fg_color='#2A2A2A'if B=='settings'else'transparent');A.btn_keys.configure(fg_color='#2A2A2A'if B=='keys'else'transparent');A.btn_history.configure(fg_color='#2A2A2A'if B=='history'else'transparent');A.btn_settings_app.configure(fg_color='#2A2A2A'if B=='app_settings'else'transparent');A.btn_help.configure(fg_color='#2A2A2A'if B=='help'else'transparent')
		for C in[A.frm_dash,A.frm_settings,A.frm_keys,A.frm_history,A.frm_app_settings,A.frm_help]:C.grid_forget()
		if B=='dashboard':A.frm_dash.grid(row=0,column=1,sticky='nsew')
		elif B=='settings':A.frm_settings.grid(row=0,column=1,sticky='nsew')
		elif B=='keys':A.frm_keys.grid(row=0,column=1,sticky='nsew')
		elif B=='history':A.frm_history.grid(row=0,column=1,sticky='nsew')
		elif B=='app_settings':A.frm_app_settings.grid(row=0,column=1,sticky='nsew')
		elif B=='help':A.frm_help.grid(row=0,column=1,sticky='nsew')
	def toggle_bot(A):
		if getattr(A,'bot_running',False):
			if hasattr(A,'live_status_shm')and'IN SPREAD'in A.live_status_shm:
				import tkinter.messagebox;B=tkinter.messagebox.askyesnocancel('Active Trade Warning','A trade is currently open!\n\nYES: Force Close Positions immediately and stop the bot.\nNO: Stop the bot, but KEEP positions open.\nCANCEL: Cancel stop request.')
				if B is True:import threading as C;C.Thread(target=A.force_close_all,daemon=True).start();A.stop_bot()
				elif B is False:A.positions_kept_open=True;A.stop_bot()
				elif B is None:return
			else:A.stop_bot()
		else:A.start_bot()
	def start_bot(A):
		try:
			A.positions_kept_open=False
			if getattr(A,'is_unstable',False):print('Warning: API latency is high or unstable, but attempting to start anyway...')
			import multiprocessing as B,os;C=os.path.join(APP_DIR,'key.env')
			if os.path.exists(C):
				with open(C,'r')as I:
					for D in I:
						if'='in D:
							E,J=D.strip().split('=',1)
							if E.startswith('BINANCE'):os.environ[E]=J
			A.bot_processes=[]
			if not hasattr(A,'log_queue'):A.log_queue=B.Queue(maxsize=200)
			F=False;import phase23_shm as K;from multiprocessing import shared_memory as L
			try:M=L.SharedMemory(name=K.SHM_NAME);M.close();F=True;print('CLI Scribe detected! GUI will not launch internal Scribe.')
			except FileNotFoundError:print('No CLI Scribe detected. GUI launching internal Scribe.')
			if not F:G=B.Process(target=run_scribe);G.start();A.bot_processes.append(G)
			H=B.Process(target=run_trader,args=(A.log_queue,));H.start();A.bot_processes.append(H);A.bot_running=True;print('Bot processes started successfully!')
		except Exception as N:import traceback as O;O.print_exc();print('START BOT ERROR:',N)
	def panic_action(A):
		import tkinter.messagebox;B=tkinter.messagebox.askyesno('PANIC CLOSE','Are you sure you want to market close all open positions immediately?')
		if B:import threading as C;C.Thread(target=A.force_close_all,daemon=True).start()
	def force_close_all(H):
		import ccxt,os;from dotenv import load_dotenv as I;H.positions_kept_open=False
		try:
			I(os.path.join(APP_DIR,'key.env'));C=os.getenv('BINANCE_API_KEY');D=os.getenv('BINANCE_API_SECRET')
			if not C or not D:print('Missing API keys for force close!');return
			E=ccxt.binanceusdm({'apiKey':C,'secret':D,'enableRateLimit':True,'options':{'broker':{'future':'x-XSY2ZGS8','spot':'x-XSY2ZGS8','swap':'x-XSY2ZGS8','linear':'x-XSY2ZGS8','delivery':'x-XSY2ZGS8'}}});J=E.fetch_positions()
			for F in J:
				K=F.get('info',{});A=float(K.get('positionAmt',.0));B=F.get('symbol')
				if abs(A)>0:
					G='buy'if A<0 else'sell';print(f"Force closing {abs(A)} of {B} ({G})")
					try:E.create_order(B,'market',G,abs(A),params={'reduceOnly':True})
					except Exception as L:print(f"Failed to create closing order for {B}: {L}")
			print('All open positions force closed.')
		except Exception as M:print(f"Failed to force close positions: {M}")
	def stop_bot(B):
		for A in B.bot_processes:
			try:
				A.terminate();A.join(timeout=1.)
				if A.is_alive():A.kill();A.join(timeout=1.)
			except Exception:pass
		B.bot_processes=[];B.bot_running=False
if __name__=='__main__':multiprocessing.freeze_support();app=PairsTraderGUI();app.protocol('WM_DELETE_WINDOW',app.on_closing);app.mainloop()