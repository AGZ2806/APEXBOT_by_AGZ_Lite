import os,time,asyncio
from dataclasses import dataclass,field
from collections import deque
from typing import Dict,Optional,Deque,Tuple,Any,List
import numpy as np,ccxt.async_support as ccxt
def ts_iso(t=None):return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime(t))
class TeeLogger:
	def __init__(A,filepath):B=filepath;import sys;A.terminal=sys.stdout;A.filepath=B;os.makedirs(os.path.dirname(B),exist_ok=True);A.log=open(B,'a',encoding='utf-8');A.last_was_cr=False
	def write(A,message):
		B=message
		if not B:return
		if A.last_was_cr and B!='\n'and not B.startswith('\r'):
			if A.terminal:A.terminal.write('\n')
			A.log.write('\n')
		if'\r'in B and not B.endswith('\n'):A.last_was_cr=True
		elif'\n'in B:A.last_was_cr=False
		if A.terminal:A.terminal.write(B)
		A.log.write(B);A.log.flush()
	def flush(A):
		if A.terminal:A.terminal.flush()
		A.log.flush()
	def __enter__(A):import sys;sys.stdout=A;sys.stderr=A;return A
	def __exit__(A,exc_type,exc_value,traceback):import sys;sys.stdout=A.terminal;sys.stderr=A.terminal;A.log.close()
@dataclass
class BotConfig:api_key:str;api_secret:str;symbols:list;context_symbols:list;testnet:bool;start_balance:float;usdt_per_level:float;auto_scale_qty:bool;max_usdt_per_level:float;min_notional_usdt:float;min_profit_pct:float;dd_soft:float;dd_hard:float;lambda_dd:float;lambda_trade:float;leverage:int;prometheus_port:int;loop_interval_sec:float;save_interval_sec:int;rebuild_interval_sec:float;price_dev_limit:float;max_pos_notional:float=5e2;inventory_gate_pct:float=.6;max_loss_per_stop_usdt:float=.15;slippage_gate_pct:float=.002;symbol_configs:Dict[str,Dict[str,float]]=field(default_factory=dict);shock_drawdown_limit:float=.05;trend_ema_alpha:float=.01;max_global_leverage:float=8.;max_corr_exposure:float=.5;corr_threshold:float=.75;trailing_stop_activation_sigma:float=1.;trailing_stop_callback_sigma:float=.3;trailing_stop_callback_ratio:float=.1;trailing_stop_min_callback:float=.0005;raw_config:Dict[str,Any]=field(default_factory=dict);stop_loss_pct:float=.005;stop_loss_sigma:float=5.;ws_seed_snapshot:bool=True
def load_cfg(path='user_settings.yaml'):
	A=path;import os,yaml
	try:from dotenv import load_dotenv as D;D('key.env')
	except ImportError:pass
	B={}
	if os.path.exists(A):
		try:
			with open(A,'r',encoding='utf-8')as E:B=yaml.safe_load(E)or{}
		except Exception as F:print(f"Error loading {A}: {F}")
	G=os.getenv('BINANCE_API_KEY','');H=os.getenv('BINANCE_API_SECRET','');C=B.get('pairs_trading',{});return BotConfig(api_key=G,api_secret=H,symbols=B.get('symbols',['BTC/USDT:USDT','ETH/USDT:USDT']),context_symbols=['BTC/USDT:USDT','SOL/USDT:USDT','BNB/USDT:USDT','ETH/USDT:USDT'],testnet=False,start_balance=15e1,usdt_per_level=5.,auto_scale_qty=False,max_usdt_per_level=1e3,max_pos_notional=55.,min_notional_usdt=5.,min_profit_pct=.001,dd_soft=.03,dd_hard=.05,lambda_dd=2.,lambda_trade=1.,leverage=5,prometheus_port=8000,loop_interval_sec=1.,save_interval_sec=1800,rebuild_interval_sec=25.,price_dev_limit=.035,slippage_gate_pct=.002,symbol_configs={'SOL/USDT:USDT':{'min_notional':6.},'ETH/USDT:USDT':{'min_notional':21.}},shock_drawdown_limit=.05,trend_ema_alpha=.01,max_global_leverage=5.,max_corr_exposure=.5,corr_threshold=.75,trailing_stop_callback_ratio=.1,trailing_stop_min_callback=.0005,raw_config={'pairs_trading':{'notional_per_leg':float(C.get('notional_per_leg',16e1)),'z_entry_threshold':float(C.get('z_entry_threshold',4.))}},stop_loss_pct=.005,ws_seed_snapshot=True,inventory_gate_pct=.6,max_loss_per_stop_usdt=.15)
class PerformanceTracker:
	def __init__(A,maxlen_steps,loop_interval_sec):B=maxlen_steps;A.equity_hist=deque(maxlen=B);A.mark_hist=deque(maxlen=B);A.ret_hist=deque(maxlen=B);A.loop_interval_sec=loop_interval_sec;A.peak_equity=-1e18
	def update(A,equity,mark):
		C=equity
		if A.equity_hist:J=A.equity_hist[-1];G=(C-J)/(abs(J)+1e-08)
		else:G=.0
		A.equity_hist.append(C);A.mark_hist.append(mark);A.ret_hist.append(G)
		if C>A.peak_equity:A.peak_equity=C
		L=(A.peak_equity-C)/(abs(A.peak_equity)+1e-08)if A.peak_equity>0 else .0;D=np.array(A.ret_hist,dtype=np.float64);H=float(np.std(D))if len(D)>5 else .0;M=float(np.mean(D))if len(D)>5 else .0;N=864e2/max(.5,A.loop_interval_sec);O=M/(H+1e-08)*np.sqrt(N)if H>0 else .0;K=np.array(A.mark_hist,dtype=np.float64)
		if len(K)>25:B=np.diff(np.log(K+1e-08));I=float(np.std(B[-20:]))if len(B)>=20 else float(np.std(B));E=float(np.std(B[-120:]))if len(B)>=120 else float(np.std(B));F=I/(E+1e-08)if E>0 else .0
		else:I=E=F=.0
		P=1. if F<.8 else .0;Q=1. if F>1.25 else .0;return{'ret':G,'vol':H,'sharpe':float(O),'dd':float(L),'sig_s':I,'sig_l':E,'vol_ratio':float(F),'quiet':P,'highvol':Q}
from abc import ABC,abstractmethod
class ExchangeAdapter(ABC):
	def __init__(A,cfg,symbol):A.cfg=cfg;A.symbol=symbol;A.maker_fee=.0002;A.taker_fee=.0004;A.min_notional=float(cfg.min_notional_usdt)
	@abstractmethod
	async def init(self):0
	@abstractmethod
	def should_backoff(self):0
	@abstractmethod
	async def cancel_all(self):0
	@abstractmethod
	async def place_order(self,side,price,qty,reduce_only=False):0
	@abstractmethod
	async def fetch_open_orders(self):0
	@abstractmethod
	async def cancel_order(self,order_id):0
	@abstractmethod
	async def fetch_position(self):0
	@abstractmethod
	async def sync_time(self):0
class BinanceAdapter(ExchangeAdapter):
	def __init__(A,cfg,symbol):
		C=symbol;B=cfg;super().__init__(B,C);A.exchange=ccxt.binanceusdm({'apiKey':os.getenv('BINANCE_API_KEY',B.api_key),'secret':os.getenv('BINANCE_API_SECRET',B.api_secret),'enableRateLimit':True,'timeout':20000,'options':{'defaultType':'future','adjustForTimeDifference':True,'recvWindow':20000,'broker':{'future':'x-XSY2ZGS8','spot':'x-XSY2ZGS8','swap':'x-XSY2ZGS8','linear':'x-XSY2ZGS8','delivery':'x-XSY2ZGS8'}}})
		if B.testnet:A.exchange.set_sandbox_mode(True)
		A._backoff_until=.0;A._inited=False;A._lock=asyncio.Lock();A._init_lock=asyncio.Lock();D=B.symbol_configs.get(C,{})
		if'min_notional'in D:A.min_notional=float(D['min_notional'])
	async def init(A):
		if A._inited:return
		async with A._init_lock:
			if A._inited:return
			await A.exchange.load_markets()
		try:await A.exchange.set_leverage(A.cfg.leverage,A.symbol)
		except Exception as B:print(f"[{ts_iso()}] [INIT] Set Leverage failed for {A.symbol}: {B}")
		try:
			F=A.exchange.market(A.symbol);C=F.get('limits',{}).get('cost',{}).get('min')
			if C:A.min_notional=max(A.min_notional,float(C));print(f"[{ts_iso()}] [INIT] {A.symbol} Min Notional set to {A.min_notional} (API: {C})")
			G=F.get('limits',{}).get('amount',{}).get('min')
			if G:A.min_qty=float(G);print(f"[{ts_iso()}] [INIT] {A.symbol} Min Qty set to {A.min_qty} (from market info)")
		except Exception:pass
		try:
			D=await A.exchange.fetch_trading_fees()
			if isinstance(D,dict):
				E=D.get(A.symbol)or D.get('ETH/USDT:USDT')
				if E:A.maker_fee=float(E.get('maker',A.maker_fee));A.taker_fee=float(E.get('taker',A.taker_fee));print(f"[{ts_iso()}] Fees for {A.symbol}: Maker={A.maker_fee:.5f} Taker={A.taker_fee:.5f}")
		except Exception as B:print(f"[{ts_iso()}] Fee fetch failed for {A.symbol}: {B}")
		A._inited=True
	async def sync_time(A):
		C=A.exchange.timeout;A.exchange.timeout=5000
		try:
			for D in range(2):
				try:
					B=await A.exchange.load_time_difference()
					if abs(B)>2000:print(f"[{ts_iso()}] [TIME SYNC] LARGE drift: {B:+d}ms. Auto-corrected.")
					elif abs(B)>500:print(f"[{ts_iso()}] [TIME SYNC] Clock drift: {B:+d}ms. Auto-corrected.")
					return
				except Exception:
					if D==0:await asyncio.sleep(1.);continue
					pass
		finally:A.exchange.timeout=C
	def should_backoff(A):return time.time()<A._backoff_until
	def _set_backoff(A,sec=5.):A._backoff_until=time.time()+sec
	async def cancel_all(A):
		async with A._lock:
			try:await A.exchange.cancel_all_orders(A.symbol)
			except Exception as B:print(f"[{ts_iso()}] cancel_all failed for {A.symbol}: {B}");A._set_backoff(5.)
	async def place_order(A,side,price,qty,reduce_only=False,order_type='limit',post_only=True):
		F=reduce_only;D=order_type;C=qty;B=price
		async with A._lock:
			try:
				await A.init()
				if D=='limit':
					B=float(max(B,1e-06))
					if not F and C*B<A.min_notional:return 0
				if B and B>0:
					G=C*B
					if G<21.:print(f"[{ts_iso()}] [DEBUG] Placing Order {A.symbol} {side} {C} @ {B} = {G:.2f} (Min: {A.min_notional})")
				E={'reduceOnly':F}
				if D=='limit':
					E['timeInForce']='GTC'
					if post_only:E['postOnly']=True
				H=B if D=='limit'else None;await A.exchange.create_order(A.symbol,D,side,C,H,E);return 1
			except Exception as I:print(f"[{ts_iso()}] Order failed: {repr(I)}");A._set_backoff(8.);return 0
	async def fetch_open_orders(A):return await A.exchange.fetch_open_orders(A.symbol)
	async def cancel_order(A,order_id):
		B=order_id
		async with A._lock:
			try:await A.exchange.cancel_order(B,A.symbol)
			except Exception as C:print(f"[{ts_iso()}] [WARN] Cancel failed {B}: {C}")
	async def fetch_position(A):B=await A.exchange.fetch_positions([A.symbol]);return B[0]if B else None
class MemoryManager:
	def __init__(A,interval_sec=36e2):A.interval=interval_sec;A.last_clean_ts=time.time();import gc;A.gc=gc
	def check(A):
		B=time.time()
		if B-A.last_clean_ts>A.interval:A.clean();A.last_clean_ts=B
	def clean(A):B=A.gc.collect();print(f"[{ts_iso()}] [MEM] GC collected {B} objects. VRAM cache cleared.")
class ExchangeManager:
	def __init__(A,cfg):A.cfg=cfg;A.adapters={}
	def get_adapter(A,symbol):
		B=symbol
		if B not in A.adapters:A.adapters[B]=BinanceAdapter(A.cfg,B)
		return A.adapters[B]
	async def close_all(B):
		for A in B.adapters.values():
			if hasattr(A,'exchange'):await A.exchange.close()