import multiprocessing.shared_memory as sm,numpy as np,pickle,time,struct
SHM_NAME='eth_bot_shm_v1'
SHM_SIZE=20971520
LOCK_NAME='eth_bot_shm_lock'
SIGNALS_SHM_NAME='eth_bot_signals_v1'
SIGNALS_SHM_SIZE=2097152
class SharedMemoryManager:
	def __init__(A,is_writer=False,name=SHM_NAME,size=SHM_SIZE):A.is_writer=is_writer;A.name=name;A.size=size;A.shm=None;A._ensure_shm()
	def _ensure_shm(A):
		try:
			if A.is_writer:
				try:B=sm.SharedMemory(name=A.name);B.close();B.unlink()
				except FileNotFoundError:pass
				try:A.shm=sm.SharedMemory(name=A.name,create=True,size=A.size);A.shm.buf[:12]=b'\x00'*12
				except FileExistsError:print('SHM exists, attaching to existing...');A.shm=sm.SharedMemory(name=A.name)
			else:A.shm=sm.SharedMemory(name=A.name)
		except FileNotFoundError:
			if not A.is_writer:A.shm=None
		except Exception as C:print(f"SHM Error: {C}");A.shm=None
	def write(A,data):
		if not A.shm:
			A._ensure_shm()
			if not A.shm:return
		try:
			C=pickle.dumps(data,protocol=5);B=len(C)
			if B+12>A.size:print(f"SHM Overflow! Data Size: {B/1024:.2f}KB > Limit");return
			D=struct.pack('=Id',B,time.time());A.shm.buf[:12]=D;A.shm.buf[12:12+B]=C
		except Exception as E:print(f"SHM Write Failed: {E}")
	def read(A):
		if not A.shm:
			A._ensure_shm()
			if not A.shm:return
		try:
			D=A.shm.buf[:12];B,E=struct.unpack('=Id',D)
			if B==0:return
			if B+12>A.size:return
			F=bytes(A.shm.buf[12:12+B]);C=pickle.loads(F);C['shm_latency']=time.time()-E;return C
		except Exception:return
	def cleanup(A):
		if A.shm:
			A.shm.close()
			if A.is_writer:
				try:A.shm.unlink()
				except:pass