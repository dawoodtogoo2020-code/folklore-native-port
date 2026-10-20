"""Read only selected owned-process globals using the exact build's ABI metadata."""
import ctypes,json,time
from ctypes import wintypes
from pathlib import Path

class ModuleEntry(ctypes.Structure):
    _fields_=[('dwSize',wintypes.DWORD),('th32ModuleID',wintypes.DWORD),
        ('th32ProcessID',wintypes.DWORD),('GlblcntUsage',wintypes.DWORD),
        ('ProccntUsage',wintypes.DWORD),('modBaseAddr',ctypes.c_void_p),
        ('modBaseSize',wintypes.DWORD),('hModule',wintypes.HMODULE),
        ('szModule',wintypes.WCHAR*256),('szExePath',wintypes.WCHAR*260)]

def module_snapshot(api,pid,last_error=ctypes.get_last_error,sleep=time.sleep):
    """Retry only documented loader-race ERROR_BAD_LENGTH, at most200ms."""
    for attempt in range(8):
        snapshot=api.CreateToolhelp32Snapshot(0x18,pid)
        if snapshot and snapshot!=ctypes.c_void_p(-1).value:return snapshot
        error=last_error()
        if error!=24 or attempt==7:
            raise OSError(error,'Read-only module snapshot failed')
        sleep(.025)

class HostThreadProbe:
    def __init__(self,pid,binary,sha256,metadata):
        self.meta=json.loads(Path(metadata).read_text())
        assert self.meta['binary_sha256']==sha256
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
        api.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        api.Module32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(ModuleEntry)]
        api.Module32NextW.argtypes=api.Module32FirstW.argtypes
        api.CloseHandle.argtypes=[wintypes.HANDLE]
        snap=module_snapshot(api,pid)
        self.base=None
        try:
            entry=ModuleEntry();entry.dwSize=ctypes.sizeof(entry)
            ok=api.Module32FirstW(snap,ctypes.byref(entry))
            while ok:
                if Path(entry.szExePath).resolve()==Path(binary).resolve():
                    assert entry.modBaseSize==self.meta['image_size']
                    self.base=entry.modBaseAddr;break
                ok=api.Module32NextW(snap,ctypes.byref(entry))
        finally:api.CloseHandle(snap)
        assert self.base is not None

    def sample(self,reader):
        m=self.meta;l=m['layout'];out={}
        def word(address,size):
            assert self.base<=address and address+size<=self.base+m['image_size']
            b=reader.read(address,size)
            return int.from_bytes(b,'little') if b is not None else None
        for tid in (1,7,8,36):
            at=self.base+m['rvas']['g_ppu_threads']+(tid-1)*l['stride']
            ctx=at+l['ctx']
            state=word(at+l['state'],4)
            hle=word(self.base+m['rvas']['g_hle_inflight']+tid*8,8)
            name=None
            if hle and self.base<=hle<self.base+m['image_size']:
                b=reader.read(hle,min(96,self.base+m['image_size']-hle))
                if b:name=b.split(b'\0',1)[0].decode('ascii',errors='replace')
            out[str(tid)]={'state':state,'syscall':word(self.base+m['rvas']['g_sc_inflight']+tid*4,4),
                'hle':name}
            # Main executes with a separate context outside this slot.
            if tid!=1 and state:
                out[str(tid)].update(last_dispatch=word(at+l['prof_pc'],4),
                    context_tid=word(ctx+l['thread_id'],8),
                    lr=word(ctx+l['lr'],8),cia=word(ctx+l['cia'],8),
                    r3=word(ctx+l['gpr']+3*8,8),r13=word(ctx+l['gpr']+13*8,8))
        return out
