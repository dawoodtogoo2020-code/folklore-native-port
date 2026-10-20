"""Mark QPC-bounded settled-scene windows; never drives or writes guest state."""
import argparse,ctypes,datetime,hashlib,json,time
from ctypes import wintypes
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('run',type=Path)
p.add_argument('--warmup',type=int,default=15);p.add_argument('--windows',type=int,default=3)
p.add_argument('--seconds',type=int,default=20);a=p.parse_args()
assert 5<=a.warmup<=60 and 1<=a.windows<=3 and 10<=a.seconds<=30
r=json.loads((a.run/'receipt.json').read_text());assert 'exit' not in r
assert hashlib.sha256(Path(r['command'][0]).read_bytes()).hexdigest()==r['binary_sha256']
k=ctypes.WinDLL('kernel32',use_last_error=True)
k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];k.OpenProcess.restype=wintypes.HANDLE
k.CloseHandle.argtypes=[wintypes.HANDLE]
k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
handle=k.OpenProcess(0x1010,False,r['pid']);assert handle
name=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(name))
assert k.QueryFullProcessImageNameW(handle,0,name,ctypes.byref(size))
assert Path(name.value).resolve()==Path(r['command'][0]).resolve()
class Memory(ctypes.Structure):
    _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(n,ctypes.c_size_t) for n in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage','PrivateUsage']]
ps=ctypes.WinDLL('psapi',use_last_error=True)
ps.GetProcessMemoryInfo.argtypes=[wintypes.HANDLE,ctypes.POINTER(Memory),wintypes.DWORD]
def memory():
    m=Memory();m.cb=ctypes.sizeof(m)
    if not ps.GetProcessMemoryInfo(handle,ctypes.byref(m),m.cb):return {'error':ctypes.get_last_error()}
    return {n:getattr(m,n) for n in ['WorkingSetSize','PeakWorkingSetSize','PrivateUsage','PageFaultCount']}
def qpc():
    v=ctypes.c_longlong();assert k.QueryPerformanceCounter(ctypes.byref(v));return v.value
freq=ctypes.c_longlong();assert k.QueryPerformanceFrequency(ctypes.byref(freq))
start=time.perf_counter();qstart=qpc();time.sleep(.25)
qdelta=qpc()-qstart;pdelta=time.perf_counter()-start
calibration={'qpc_frequency':freq.value,'wall_seconds':pdelta,'qpc_seconds':qdelta/freq.value,'relative_difference':abs(qdelta/freq.value-pdelta)/pdelta}
assert calibration['relative_difference']<.002
output={'binary_sha256':r['binary_sha256'],'run':str(a.run.resolve()),'clock_calibration':calibration,'warmup_seconds':a.warmup,'windows':[],'scope':'Host successful-present timestamps only; no GPU execution/scanout measurement'}
try:
    print(json.dumps({'phase':'warmup','seconds':a.warmup}),flush=True);time.sleep(a.warmup)
    for i in range(a.windows):
        row={'index':i+1,'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'memory_before':memory(),'start_qpc':qpc()}
        time.sleep(a.seconds);row['end_qpc']=qpc();row['memory_after']=memory()
        row['qpc_seconds']=(row['end_qpc']-row['start_qpc'])/freq.value
        output['windows'].append(row)
        (a.run/'timing-windows.json').write_text(json.dumps(output,indent=2))
        print(json.dumps({'completed_window':i+1,'seconds':row['qpc_seconds']}),flush=True)
finally:k.CloseHandle(handle)
