"""Monitor list, DPI awareness and window fitting for the launcher (Windows; safe elsewhere)."""
from dataclasses import dataclass
import os

if os.name == 'nt':
    import ctypes
    from ctypes import wintypes

BASE_DPI = 96


@dataclass(frozen=True)
class Monitor:
    index: int          # SDL display index: primary first, then Windows adapter order
    device: str
    primary: bool
    bounds: tuple       # (left, top, right, bottom) in physical pixels
    work: tuple         # usable area without the taskbar
    dpi: int

    @property
    def size(self):
        return self.bounds[2] - self.bounds[0], self.bounds[3] - self.bounds[1]

    @property
    def scale_percent(self):
        return round(self.dpi * 100 / BASE_DPI)

    def label(self):
        w, h = self.size
        return (f'Display {self.index + 1} · {w} × {h} · {self.scale_percent}% scaling'
                + (' (primary)' if self.primary else ''))


def enable_dpi_awareness():
    """Per-monitor DPI awareness before any window exists. Returns the mode obtained."""
    if os.name != 'nt':
        return 'not-windows'
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return 'per-monitor-v2'
    except (AttributeError, OSError):
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return 'per-monitor'
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return 'system'
    except (AttributeError, OSError):
        return 'unaware'


def order_like_sdl(adapters):
    """adapters: [(device, attached, primary)] in EnumDisplayDevices order.
    SDL lists the primary display first, then the other attached ones in order."""
    attached = [a for a in adapters if a[1]]
    return [a[0] for a in attached if a[2]] + [a[0] for a in attached if not a[2]]


if os.name == 'nt':
    class _MonitorInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('rcMonitor', wintypes.RECT), ('rcWork', wintypes.RECT),
                    ('dwFlags', wintypes.DWORD), ('szDevice', wintypes.WCHAR * 32)]

    class _DisplayDevice(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('DeviceName', wintypes.WCHAR * 32),
                    ('DeviceString', wintypes.WCHAR * 128), ('StateFlags', wintypes.DWORD),
                    ('DeviceID', wintypes.WCHAR * 128), ('DeviceKey', wintypes.WCHAR * 128)]

    _ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                    ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    # Handles are pointer-sized; the ctypes default int return would truncate them.
    ctypes.windll.user32.MonitorFromWindow.restype = wintypes.HMONITOR
    ctypes.windll.user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
    ctypes.windll.user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.c_void_p)
    try:
        ctypes.windll.shcore.GetDpiForMonitor.argtypes = (wintypes.HMONITOR, ctypes.c_int,
                                                          ctypes.c_void_p, ctypes.c_void_p)
    except (AttributeError, OSError):
        pass

    def _monitor_info(handle):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(_MonitorInfo)
        if not ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info)):
            return None
        dpi = BASE_DPI
        try:
            x, y = wintypes.UINT(), wintypes.UINT()
            if ctypes.windll.shcore.GetDpiForMonitor(handle, 0, ctypes.byref(x), ctypes.byref(y)) == 0:
                dpi = x.value
        except (AttributeError, OSError):
            pass
        r, w = info.rcMonitor, info.rcWork
        return (info.szDevice, bool(info.dwFlags & 1), (r.left, r.top, r.right, r.bottom),
                (w.left, w.top, w.right, w.bottom), dpi)


def list_monitors():
    """Active monitors in SDL display-index order; [] if unavailable."""
    if os.name != 'nt':
        return []
    try:
        found = {}

        def callback(handle, _dc, _rect, _param):
            info = _monitor_info(handle)
            if info:
                found[info[0]] = info
            return True
        ctypes.windll.user32.EnumDisplayMonitors(None, None, _ENUM_PROC(callback), 0)
        adapters, i = [], 0
        while True:
            dev = _DisplayDevice()
            dev.cb = ctypes.sizeof(_DisplayDevice)
            if not ctypes.windll.user32.EnumDisplayDevicesW(None, i, ctypes.byref(dev), 0):
                break
            adapters.append((dev.DeviceName, bool(dev.StateFlags & 1), bool(dev.StateFlags & 4)))
            i += 1
        names = [n for n in order_like_sdl(adapters) if n in found]
        names += [n for n in found if n not in names]
        return [Monitor(i, n, found[n][1], found[n][2], found[n][3], found[n][4]) for i, n in enumerate(names)]
    except (AttributeError, OSError, ValueError):
        return []


def monitor_of_window(hwnd, monitors):
    """The Monitor a window is mostly on (Windows), else None."""
    if os.name != 'nt' or not monitors:
        return None
    try:
        handle = ctypes.windll.user32.MonitorFromWindow(wintypes.HWND(hwnd), 2)
        info = _monitor_info(handle)
    except (AttributeError, OSError):
        return None
    return next((m for m in monitors if info and m.device == info[0]), None)


def fit_geometry(work, dpi, desired, minimum=(560, 420), margin=24):
    """Place a window of `desired` logical (96-DPI) size on one monitor's work area.

    Returns (x, y, width, height, min_width, min_height) in physical pixels; the window
    is scaled for the monitor's DPI and shrunk, never past the work area, to fit.
    """
    scale = max(dpi, BASE_DPI) / BASE_DPI
    left, top, right, bottom = work
    avail_w, avail_h = max(1, right - left - 2 * margin), max(1, bottom - top - 2 * margin)
    min_w, min_h = min(round(minimum[0] * scale), avail_w), min(round(minimum[1] * scale), avail_h)
    w = max(min_w, min(round(desired[0] * scale), avail_w))
    h = max(min_h, min(round(desired[1] * scale), avail_h))
    x = left + (right - left - w) // 2
    y = top + (bottom - top - h) // 2
    return x, y, w, h, min_w, min_h
