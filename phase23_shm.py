import multiprocessing.shared_memory as sm
import numpy as np
import pickle
import time
import struct

# Constants
SHM_NAME = "eth_bot_shm_v1"
SHM_SIZE = 20 * 1024 * 1024  # 20 MB (Plenty for 512x214x4 bytes + Metadata)
LOCK_NAME = "eth_bot_shm_lock"

# Signals SHM Constants
SIGNALS_SHM_NAME = "eth_bot_signals_v1"
SIGNALS_SHM_SIZE = 2 * 1024 * 1024  # 2 MB (Plenty for JSON dicts)

class SharedMemoryManager:
    """
    Manages a Shared Memory block for high-speed transfer between Scribe (Writer) and Trader (Reader).
    Protocol:
    - 4 bytes: Data Length (int32)
    - 8 bytes: Timestamp (double)
    - N bytes: Pickled Data (Dictionary)
    """
    def __init__(self, is_writer=False, name=SHM_NAME, size=SHM_SIZE):
        self.is_writer = is_writer
        self.name = name
        self.size = size
        self.shm = None
        self._ensure_shm()

    def _ensure_shm(self):
        try:
            if self.is_writer:
                # API: Create or Open
                # Try to unlink old if exists (cleanup)
                try:
                    old = sm.SharedMemory(name=self.name)
                    old.close()
                    old.unlink()
                except FileNotFoundError:
                    pass
                
                try:
                    self.shm = sm.SharedMemory(name=self.name, create=True, size=self.size)
                    # Zero out header
                    self.shm.buf[:12] = b'\x00' * 12
                except FileExistsError:
                    # Fallback: Open existing if we couldn't unlink/create (e.g. rapid restart)
                    print("SHM exists, attaching to existing...")
                    self.shm = sm.SharedMemory(name=self.name)
            else:
                # Reader: Open existing
                self.shm = sm.SharedMemory(name=self.name)
                
        except FileNotFoundError:
            if not self.is_writer:
                # print("Shared Memory not found. Writer not started?")
                self.shm = None
        except Exception as e:
            print(f"SHM Error: {e}")
            self.shm = None

    def write(self, data: dict):
        if not self.shm:
            self._ensure_shm()
            if not self.shm: return

        try:
            # Serialize
            serialized = pickle.dumps(data, protocol=5)
            data_len = len(serialized)
            
            if data_len + 12 > self.size:
                print(f"SHM Overflow! Data Size: {data_len/1024:.2f}KB > Limit")
                return

            # Header: Length (4 bytes) + Timestamp (8 bytes)
            # Use '=' for standard alignment (no padding) -> strict 12 bytes
            header = struct.pack("=Id", data_len, time.time())
            
            # Write Header
            self.shm.buf[:12] = header
            
            # Write Data
            self.shm.buf[12:12+data_len] = serialized
            
        except Exception as e:
            print(f"SHM Write Failed: {e}")

    def read(self):
        if not self.shm:
            self._ensure_shm()
            if not self.shm: return None

        try:
            # Read Header
            header = self.shm.buf[:12]
            # Use '=' for standard alignment
            data_len, ts = struct.unpack("=Id", header)
            
            if data_len == 0:
                return None # Empty
                
            if data_len + 12 > self.size:
                return None # Corrupt header
                
            # Read Data
            raw_data = bytes(self.shm.buf[12:12+data_len])
            data = pickle.loads(raw_data)
            
            # Attach latency meta
            data["shm_latency"] = time.time() - ts
            return data
            
        except Exception:
            return None

    def cleanup(self):
        if self.shm:
            self.shm.close()
            if self.is_writer:
                try:
                    self.shm.unlink()
                except: pass
