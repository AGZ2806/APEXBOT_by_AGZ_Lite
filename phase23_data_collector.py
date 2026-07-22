import asyncio,json,time
from dataclasses import dataclass,field
from typing import Any,Dict,List,Optional
import numpy as np,websockets
try:import ccxt.async_support as ccxt
except Exception as e:ccxt=None;_ccxt_import_error=e
@dataclass
class Frame:ts:float;data:Dict[str,Dict[str,Any]]=field(default_factory=dict);context:Dict[str,Any]=field(default_factory=dict)
class OrderBook:
	def __init__(A):A.bids={};A.asks={}
	def apply_snapshot(A,bids,asks):
		A.bids.clear();A.asks.clear()
		for(B,C)in bids:A.bids[float(B)]=float(C)
		for(B,C)in asks:A.asks[float(B)]=float(C)
	def apply_delta(C,bids,asks):
		for(D,E)in bids:
			A=float(D);B=float(E)
			if B<=0:C.bids.pop(A,None)
			else:C.bids[A]=B
		for(D,E)in asks:
			A=float(D);B=float(E)
			if B<=0:C.asks.pop(A,None)
			else:C.asks[A]=B
	def top_levels(A,n):B=sorted(A.bids.items(),key=lambda x:-x[0])[:n];C=sorted(A.asks.items(),key=lambda x:x[0])[:n];return B,C
	def mid(A):
		if not A.bids or not A.asks:return .0
		return .5*(max(A.bids.keys())+min(A.asks.keys()))
class Phase23DataCollector:
	def __init__(A,api_key,api_secret,symbols,context_symbols=[],testnet=False,leverage=10,frame_interval_ms=100,depth_levels=20,ws_seed_snapshot=True):
		D=context_symbols;C=symbols
		if ccxt is None:raise ImportError(f"ccxt.async_support not available: {_ccxt_import_error}")
		A.api_key=api_key;A.api_secret=api_secret;A.trading_symbols=C;A.context_symbols=D;A.all_symbols=list(set(C+D));A.testnet=testnet;A.leverage=leverage;A.frame_interval=frame_interval_ms/1e3;A.depth_levels=depth_levels;A.ws_seed_snapshot=ws_seed_snapshot;A.books={A:OrderBook()for A in A.all_symbols};A.marks={A:{}for A in A.all_symbols};A.trades={A:[]for A in A.all_symbols};A.htf_candles={A:{}for A in A.all_symbols};A.last_u={A:0 for A in A.all_symbols};A.stream_map={}
		for B in A.all_symbols:F=B.split('/')[0].lower();G=B.split('/')[1].split(':')[0].lower();E=f"{F}{G}";A.stream_map[E]=B;A.stream_map[E.upper()]=B
		A.queue=asyncio.Queue(maxsize=5000);A.frames=[];A._task=None;A._running=False;A._last_frame_ts=.0;A._last_msg_ts=.0;A.last_depth_log=.0;A.first_frame_event=asyncio.Event();A.exchange=A._make_exchange();A._htf_task=None
	def _make_exchange(A):
		B=ccxt.binanceusdm({'apiKey':A.api_key,'secret':A.api_secret,'enableRateLimit':True,'options':{'defaultType':'future','broker':{'future':'x-XSY2ZGS8','spot':'x-XSY2ZGS8','swap':'x-XSY2ZGS8','linear':'x-XSY2ZGS8','delivery':'x-XSY2ZGS8'}}})
		if A.testnet:B.set_sandbox_mode(True)
		return B
	async def _configure_exchange(A):
		for B in A.trading_symbols:
			try:await A.exchange.set_leverage(A.leverage,B)
			except Exception as C:print(f"Leverage set failed {B}: {C}")
	async def initialize_books(A):
		for B in A.all_symbols:
			try:
				C=await A.exchange.fetch_order_book(B,limit=A.depth_levels);D=[[str(A),str(B)]for(A,B)in C.get('bids',[])];E=[[str(A),str(B)]for(A,B)in C.get('asks',[])];A.books[B].apply_snapshot(D,E)
				if'lastUpdateId'in C:A.last_u[B]=C['lastUpdateId']
				print(f"[{ts()}] Initial snapshot loaded for {B} (last_u={A.last_u[B]})")
			except Exception as F:print(f"Initial snapshot failed {B}: {F}")
	def _ws_base(A):return'wss://stream.binancefuture.com/stream?streams='if A.testnet else'wss://fstream.binance.com/stream?streams='
	def _stream_url(B):
		C=[]
		for D in B.all_symbols:E=D.split('/')[0].lower();F=D.split('/')[1].split(':')[0].lower();A=f"{E}{F}";C+=[f"{A}@depth20@100ms",f"{A}@aggTrade",f"{A}@markPrice@1s"]
		return B._ws_base()+'/'.join(C)
	async def connect_ws(A):await A.start()
	async def start(A):
		if A._running:return
		A._running=True;await A._configure_exchange()
		if A.ws_seed_snapshot:await A.initialize_books()
		print(f"[{ts()}] Phase23DataCollector connecting WS...");A._task=asyncio.create_task(A._run_ws());A._htf_task=asyncio.create_task(A._run_htf_loop())
	async def stop(A):
		A._running=False
		if A._task:
			A._task.cancel()
			try:await A._task
			except:pass
			A._task=None
		if A._htf_task:
			A._htf_task.cancel()
			try:await A._htf_task
			except:pass
			A._htf_task=None
		if A.exchange:
			try:await A.exchange.close()
			except:pass
	async def next_frame(A,timeout=1.):
		try:return await asyncio.wait_for(A.queue.get(),timeout=timeout)
		except asyncio.TimeoutError:return
	def get_recent_frames(A,n):return A.frames[-n:]if len(A.frames)>=n else None
	def get_mark_price(A,symbol):
		B=symbol
		if B in A.marks and A.marks[B].get('mark_price',.0)>0:return float(A.marks[B]['mark_price'])
		if B in A.books:return float(A.books[B].mid())
		return .0
	async def fetch_balance(A):
		try:return await A.exchange.fetch_balance()
		except Exception:return
	async def fetch_position(B,symbol):
		try:A=await B.exchange.fetch_positions([symbol]);return A[0]if A else None
		except Exception:return
	async def fetch_htf_candles(B):
		for C in B.all_symbols:
			try:
				D=await B.exchange.fetch_ohlcv(C,timeframe='1m',limit=2)
				if D and len(D)>=1:A=D[-2]if len(D)>1 else D[-1];B.htf_candles[C]['1m']={'close':float(A[4]),'vol':float(A[5]),'high':float(A[2]),'low':float(A[3])}
				E=await B.exchange.fetch_ohlcv(C,timeframe='5m',limit=2)
				if E and len(E)>=1:A=E[-2]if len(E)>1 else E[-1];B.htf_candles[C]['5m']={'close':float(A[4]),'vol':float(A[5]),'high':float(A[2]),'low':float(A[3])}
				F=await B.exchange.fetch_ohlcv(C,timeframe='1h',limit=2)
				if F and len(F)>=1:A=F[-2]if len(F)>1 else F[-1];B.htf_candles[C]['1h']={'close':float(A[4]),'open':float(A[1]),'vol':float(A[5]),'high':float(A[2]),'low':float(A[3])}
				G=await B.exchange.fetch_ohlcv(C,timeframe='4h',limit=2)
				if G and len(G)>=1:A=G[-2]if len(G)>1 else G[-1];B.htf_candles[C]['4h']={'close':float(A[4]),'open':float(A[1]),'vol':float(A[5]),'high':float(A[2]),'low':float(A[3])}
			except Exception as H:print(f"[{ts()}] HTF fetch failed for {C}: {H}");await asyncio.sleep(.1)
	async def _run_htf_loop(A):
		while A._running:await A.fetch_htf_candles();await asyncio.sleep(30)
	async def _run_ws(A):
		F=A._stream_url()
		while A._running:
			try:
				async with websockets.connect(F,ping_interval=20,ping_timeout=60)as G:
					print(f"[{ts()}] WS connected.");A._last_msg_ts=time.time();H=time.time()
					while A._running:
						I=await G.recv();A._last_msg_ts=time.time();J=json.loads(I);B=J.get('data',{});C=B.get('e','')
						if C=='depthUpdate':A._handle_depth(B)
						elif C=='aggTrade':A._handle_trade(B)
						elif C=='markPriceUpdate':
							if not hasattr(A,'_logged_mark'):print(f"[{ts()}] First mark event: {B}");A._logged_mark=True
							A._handle_mark(B)
						D=time.time()
						if D-A._last_frame_ts>=A.frame_interval:
							A._last_frame_ts=D;E=A._build_frame(D)
							if E is not None:
								if not A.first_frame_event.is_set():A.first_frame_event.set()
								if not A.queue.full():await A.queue.put(E)
								A.frames.append(E)
								if len(A.frames)>5000:A.frames=A.frames[-4000:]
						if time.time()-A._last_msg_ts>1e1:raise RuntimeError('WS watchdog timeout')
						if time.time()-H>82800:print(f"[{ts()}] Proactive 23h Reconnect...");raise RuntimeError('Scheduled 23h Reconnect')
			except Exception as K:print(f"[{ts()}] WS error/reconnect: {K}");await asyncio.sleep(2.)
	def _get_symbol_from_stream(A,stream_symbol):return A.stream_map.get(stream_symbol)
	async def _reset_symbol(A,sym):
		B=sym;D=time.time();E=getattr(A,'_last_reset_log',{}).get(B,0)
		if D-E>300:
			print(f"[{ts()}] [CRITICAL] Resetting book for {B} due to sequence gap/error. (Suppressing further logs for 5m)")
			if not hasattr(A,'_last_reset_log'):A._last_reset_log={}
			A._last_reset_log[B]=D
		try:
			C=await A.exchange.fetch_order_book(B,limit=A.depth_levels);F=[[str(A),str(B)]for(A,B)in C.get('bids',[])];G=[[str(A),str(B)]for(A,B)in C.get('asks',[])];A.books[B].apply_snapshot(F,G)
			if'lastUpdateId'in C:A.last_u[B]=C['lastUpdateId'];print(f"[{ts()}] Reset complete. New last_u={A.last_u[B]}")
			else:A.last_u[B]=0
		except Exception as H:print(f"[{ts()}] Reset failed for {B}: {H}")
	def _handle_depth(B,p):
		D=p.get('s','');C=B._get_symbol_from_stream(D)
		if not C:return
		A=p.get('u')
		if A is None:A=p.get('lastUpdateId')
		if A is None:return
		E=B.last_u.get(C,0)
		if A<=E:return
		B.last_u[C]=A;F=p.get('b',[]);G=p.get('a',[]);B.books[C].apply_snapshot(F,G)
	def _handle_trade(A,p):
		C=p.get('s','');B=A._get_symbol_from_stream(C)
		if not B:return
		D={'price':float(p.get('p',.0)),'qty':float(p.get('q',.0)),'is_buyer_maker':bool(p.get('m',False)),'ts':float(p.get('T',0))/1e3};A.trades[B].append(D)
		if len(A.trades[B])>400:A.trades[B]=A.trades[B][-300:]
	def _handle_mark(A,p):
		C=p.get('s','');B=A._get_symbol_from_stream(C)
		if not B:return
		D={'mark_price':float(p.get('p',.0)),'index_price':float(p.get('i',.0)),'funding_rate':float(p.get('r',.0)),'event_ts':float(p.get('E',0))/1e3,'next_funding_ts':float(p.get('T',0))/1e3};A.marks[B]=D
	def _book_dict(A,ob):B,C=ob.top_levels(A.depth_levels);return{'bids':B,'asks':C,'mid':ob.mid()}
	def _build_frame(A,ts_now):
		for B in A.trading_symbols:
			if A.books[B].mid()<=0:return
		C={}
		for B in A.all_symbols:
			D=A._book_dict(A.books[B])
			if A.marks[B]:D.update(A.marks[B])
			E=A.trades[B];A.trades[B]=[];C[B]={'book':D,'trades':E,'mark':A.marks[B].copy(),'htf':A.htf_candles[B].copy()}
			if time.time()-A.last_depth_log>60:
				if B==A.trading_symbols[0]:F=len(A.books[B].bids);G=len(A.books[B].asks);print(f"[{ts()}] [L2 CHECK] {B} Depth: Bids={F} Asks={G} (Should be ~20)");A.last_depth_log=time.time()
		return Frame(ts=ts_now,data=C,context={})
def ts():return time.strftime('%Y-%m-%dT%H:%M:%S',time.gmtime())