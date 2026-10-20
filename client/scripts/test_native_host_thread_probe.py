import ctypes,unittest
from native_host_thread_probe import module_snapshot

class FakeAPI:
    def __init__(self,values):self.values=iter(values);self.calls=[]
    def CreateToolhelp32Snapshot(self,flags,pid):
        self.calls.append((flags,pid));return next(self.values)

class SnapshotRetry(unittest.TestCase):
    def test_success_without_retry(self):
        api=FakeAPI([123]);delays=[]
        self.assertEqual(module_snapshot(api,42,lambda:0,delays.append),123)
        self.assertEqual(api.calls,[(0x18,42)]);self.assertEqual(delays,[])
    def test_documented_loader_race(self):
        api=FakeAPI([ctypes.c_void_p(-1).value,456]);delays=[]
        self.assertEqual(module_snapshot(api,43,lambda:24,delays.append),456)
        self.assertEqual(len(api.calls),2);self.assertEqual(delays,[.025])
    def test_other_error_is_not_retried(self):
        api=FakeAPI([ctypes.c_void_p(-1).value]);delays=[]
        with self.assertRaises(OSError) as raised:module_snapshot(api,44,lambda:5,delays.append)
        self.assertEqual(raised.exception.errno,5)
        self.assertEqual(len(api.calls),1);self.assertEqual(delays,[])
    def test_persistent_race_is_bounded(self):
        api=FakeAPI([ctypes.c_void_p(-1).value]*8);delays=[]
        with self.assertRaises(OSError) as raised:module_snapshot(api,45,lambda:24,delays.append)
        self.assertEqual(raised.exception.errno,24)
        self.assertEqual(len(api.calls),8);self.assertEqual(len(delays),7)

if __name__=='__main__':unittest.main()
