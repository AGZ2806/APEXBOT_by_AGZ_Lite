import asyncio,os,sys,time,traceback,json
from phase23_lib import load_cfg,ts_iso,TeeLogger
from concurrent.futures import ThreadPoolExecutor
from phase23_data_collector import Phase23DataCollector
from phase23_shm import SharedMemoryManager
CONFIG_PATH='user_settings.yaml'if os.path.exists('user_settings.yaml')else'phase23.yaml'
CONFIG_CHECK_INTERVAL=6e1
HTF_ENABLED=False
class Recorder:
	def __init__(A,data_dir,cli_mode=False):A.history_dir=os.path.join(data_dir,'history');os.makedirs(A.history_dir,exist_ok=True);A.current_file=None;A.current_hour=-1;A.cli_mode=cli_mode
	def _get_filepath(A,ts):
		B=time.gmtime(ts);C=B.tm_hour
		if C!=A.current_hour:
			A.current_hour=C;D=time.strftime('%Y-%m-%d_%H',B);E=f"scribe_{D}.jsonl"
			if A.current_file:A.current_file.close()
			A.current_file=open(os.path.join(A.history_dir,E),'a',encoding='utf-8')
			if not A.cli_mode:A._prune_history()
		return A.current_file
	def _prune_history(B):
		try:
			D=time.time()-1209600
			for A in os.listdir(B.history_dir):
				if A.startswith('scribe_')and A.endswith('.jsonl'):
					C=os.path.join(B.history_dir,A)
					if os.path.getmtime(C)<D:os.remove(C);print(f"[{ts_iso()}] Pruned old history file: {A}")
		except Exception as E:print(f"[{ts_iso()}] Error pruning history: {E}")
	def write(E,frame):
		B=frame
		try:
			C=E._get_filepath(B.ts);D={'ts':B.ts,'data':{}}
			for(F,A)in B.data.items():D['data'][F]={'bids':A['book']['bids'],'asks':A['book']['asks'],'trades':A['trades'],'mark':A['mark'].get('mark_price',.0),'funding':A['mark'].get('funding_rate',.0),'htf':A.get('htf',{})}
			C.write(json.dumps(D)+'\n');C.flush()
		except Exception as G:print(f"[{ts_iso()}] Recorder Error: {G}")
def _load_config():A=load_cfg(CONFIG_PATH);B=os.path.getmtime(CONFIG_PATH);return A,B
async def _build_collector(cfg):
	A=cfg;B=Phase23DataCollector(api_key=A.api_key,api_secret=A.api_secret,symbols=A.symbols,context_symbols=A.context_symbols,testnet=A.testnet,leverage=A.leverage,frame_interval_ms=100,depth_levels=20,ws_seed_snapshot=True)
	if not HTF_ENABLED:B._run_htf_loop=lambda:asyncio.sleep(1e9)
	await B.connect_ws();C=sorted(set(A.symbols+A.context_symbols));print(f"[{ts_iso()}] Collector connected for symbols: {C}");return B
async def main(cli_mode=False):
	print(f"[{ts_iso()}] Starting Scribe Service...");A,N=_load_config();O=time.time();P=os.path.join(os.getcwd(),'data');os.makedirs(P,exist_ok=True);B=await _build_collector(A);V=Recorder(P,cli_mode=cli_mode);Q=SharedMemoryManager(is_writer=True);print(f"[{ts_iso()}] Shared Memory initialized (Writer).");print(f"[{ts_iso()}] Scribe initialized. Collecting {len(A.symbols+A.context_symbols)} symbols.")
	while len(B.frames)<10:await asyncio.sleep(1.);print(f"[{ts_iso()}] Frames collected: {len(B.frames)}")
	E=.0;I=ThreadPoolExecutor(max_workers=2);F=0;J=0;G=0;K=time.time();W=6e1;print(f"[{ts_iso()}] Scribe ready. Recording + SHM active.")
	try:
		while True:
			X=time.time()
			try:
				D=time.time()
				if D-O>=CONFIG_CHECK_INTERVAL:
					O=D
					try:
						L=os.path.getmtime(CONFIG_PATH)
						if L!=N:
							print(f"[{ts_iso()}] [HOT-RELOAD] Config change detected. Reloading...");H,L=_load_config();R=set(A.symbols+A.context_symbols);S=set(H.symbols+H.context_symbols)
							if R!=S:
								print(f"[{ts_iso()}] [HOT-RELOAD] Symbol set changed: {R} → {S}. Reconnecting WS...");await B.stop();B=await _build_collector(H)
								while len(B.frames)<5:await asyncio.sleep(.5)
							else:print(f"[{ts_iso()}] [HOT-RELOAD] Config reloaded (no symbol change — no WS reconnect needed).")
							A=H;N=L
					except Exception as M:print(f"[{ts_iso()}] [HOT-RELOAD] Error reading config: {M}")
				T=B.get_recent_frames(120)
				if T:
					C=T[-1]
					if C.ts>E:
						if E>0 and C.ts-E>5.:J+=1
						E=C.ts;F+=1;Y={'ts':C.ts,'marks':B.marks,'books':{A:C.data[A]['book']for A in A.symbols if A in C.data},'funding':{A:B.marks.get(A,{}).get('funding_rate',.0)for A in A.symbols}};Q.write(Y)
						if not I._shutdown:I.submit(V.write,C)
						else:G+=1
				if D-K>W:U=D-K;Z=F/U if U>0 else 0;a=f" | Dropped: {G}"if G>0 else'';print(f"[{ts_iso()}] [HEARTBEAT] {F} frames ({Z:.1f}/s) | Gaps>5s: {J}{a}");F=0;J=0;G=0;K=D
			except Exception as M:print(f"[{ts_iso()}] Scribe Error: {M}");traceback.print_exc()
			b=time.time()-X;c=max(.0,A.loop_interval_sec-b);await asyncio.sleep(c)
	finally:print(f"[{ts_iso()}] Scribe shutting down...");await B.stop();I.shutdown(wait=True);Q.cleanup();print(f"[{ts_iso()}] Shutdown complete.")
if __name__=='__main__':
	sys.stdout=TeeLogger('logs/scribe.log')
	try:asyncio.run(main(cli_mode=True))
	except KeyboardInterrupt:print(f"[{ts_iso()}] Scribe stopped by user.")