"""One explicit, bounded input to an exact owned native Folklore notice.

Construction enables the scenario. The normal launcher must opt in; importing
this module never enumerates windows or sends input.
"""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import time


WINDOW_CLASS = "FolkloreNativeSystemDialog"
WINDOW_TITLE = "Folklore system dialog"
MESSAGE = ("Game data is about to be saved to the HDD.\n"
           "During saving the HDD access indicator will blink.\n"
           "Do not turn off the PLAYSTATION\u00ae3 system during saving.")
INSTALL_COMPLETE_MESSAGE = "Game data creation on the HDD complete."
BUTTON_ID = 204
MESSAGE_ID = 100
BM_CLICK = 0x00F5
WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002


def normalized(text):
    """Normalize only Windows line endings, leaving all other text exact."""
    return text.replace("\r\n", "\n")


class WindowsDialogBackend:
    """Small Win32 adapter; initialized only for an explicitly armed run."""

    def __init__(self):
        if os.name != "nt":
            raise OSError("native dialog backend requires Windows")
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self.user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetClassNameW.restype = ctypes.c_int
        self.user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self.user32.GetWindowTextW.restype = ctypes.c_int
        self.user32.GetDlgItem.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetDlgItem.restype = wintypes.HWND
        self.user32.GetParent.argtypes = [wintypes.HWND]
        self.user32.GetParent.restype = wintypes.HWND
        self.user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL
        self.user32.IsWindowEnabled.argtypes = [wintypes.HWND]
        self.user32.IsWindowEnabled.restype = wintypes.BOOL
        self.user32.SendMessageTimeoutW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
                                                  wintypes.LPARAM, wintypes.UINT, wintypes.UINT,
                                                  ctypes.POINTER(ctypes.c_size_t)]
        self.user32.SendMessageTimeoutW.restype = wintypes.LPARAM
        self.kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL

    def creation_identity(self, process):
        handle = getattr(process, "_handle", None)
        if not handle:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not self.kernel32.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return None
        created = times[0]
        return (created.dwHighDateTime << 32) | created.dwLowDateTime

    def windows_for_pid(self, pid):
        found = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def callback(hwnd, _):
            owner = wintypes.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                found.append(int(hwnd))
            return True

        if not self.user32.EnumWindows(callback, 0):
            raise OSError(ctypes.get_last_error(), "EnumWindows failed")
        return found

    def pid(self, hwnd):
        owner = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        return owner.value

    def class_name(self, hwnd):
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def text(self, hwnd):
        buffer = ctypes.create_unicode_buffer(4096)
        self.user32.GetWindowTextW(hwnd, buffer, len(buffer))
        return buffer.value

    def child_text(self, hwnd):
        # GetWindowTextW does not retrieve a child control's text across processes.
        buffer = ctypes.create_unicode_buffer(4096)
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        ok = self.user32.SendMessageTimeoutW(hwnd, WM_GETTEXT, len(buffer),
                                             ctypes.cast(buffer, ctypes.c_void_p).value,
                                             SMTO_ABORTIFHUNG, 100, ctypes.byref(result))
        if not ok:
            raise TimeoutError(f"WM_GETTEXT unavailable (Win32 error {ctypes.get_last_error()})")
        if result.value >= len(buffer) - 1:
            raise ValueError("child text exceeds bounded buffer")
        return buffer.value

    def item(self, hwnd, item_id):
        return int(self.user32.GetDlgItem(hwnd, item_id) or 0)

    def parent(self, hwnd):
        return int(self.user32.GetParent(hwnd) or 0)

    def generation(self, hwnd):
        # The presenter sets immutable GWLP_USERDATA when creating each window.
        return int(self.user32.GetWindowLongPtrW(hwnd, -21))

    def visible(self, hwnd):
        return bool(self.user32.IsWindowVisible(hwnd))

    def enabled(self, hwnd):
        return bool(self.user32.IsWindowEnabled(hwnd))

    def click(self, hwnd):
        result = ctypes.c_size_t()
        ctypes.set_last_error(0)
        ok = self.user32.SendMessageTimeoutW(hwnd, BM_CLICK, 0, 0,
                                             SMTO_ABORTIFHUNG, 100, ctypes.byref(result))
        if not ok:
            raise TimeoutError(f"BM_CLICK delivery unknown (Win32 error {ctypes.get_last_error()})")
        return int(result.value)


class StartupDialogScenario:
    """At most one action, tied to the owned Popen process and exact dialog."""

    def __init__(self, process, folder, backend=None, stage="startup"):
        if stage not in ("startup", "install-complete"):
            raise ValueError("unsupported explicit dialog stage")
        self.stage = stage
        self.message = MESSAGE if stage == "startup" else INSTALL_COMPLETE_MESSAGE
        self.expected_generation = 1 if stage == "startup" else 2
        self.process = process
        self.folder = Path(folder)
        self.backend = backend if backend is not None else WindowsDialogBackend()
        self.pid = process.pid
        self.identity = self.backend.creation_identity(process)
        self.status = "waiting"
        self.actions = []
        self.attempted = False
        self.path = self.folder / (stage + "-dialog-input.json")
        self._record("armed", None)

    def _record(self, result, detail):
        self.actions.append({"monotonic_seconds": time.monotonic(),
                             "expected_pid": self.pid,
                             "expected_process_creation": self.identity,
                             "expected_window_class": WINDOW_CLASS,
                             "expected_window_title": WINDOW_TITLE,
                             "expected_generation": self.expected_generation,
                             "expected_control_id": BUTTON_ID,
                             "expected_control_class": "Button",
                             "expected_control_text": "Cancel",
                             "expected_message_id": MESSAGE_ID,
                             "expected_before_text": self.message,
                             "result": result, "detail": detail})
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.summary(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def summary(self):
        return {"schema": "folklore-startup-dialog-input-v1", "stage": self.stage, "pid": self.pid,
                "process_creation": self.identity, "status": self.status,
                "attempted": self.attempted, "actions": list(self.actions)}

    def _alive_owned(self):
        if self.process.poll() is not None:
            self.status = "stopped"
            self._record("stopped", "owned child exited")
            return False
        identity = self.backend.creation_identity(self.process)
        if self.identity is None or identity is None or identity != self.identity:
            self.status = "rejected"
            self._record("rejected", "owned process creation identity unavailable or changed")
            return False
        return True

    def _target(self, hwnd):
        if self.backend.pid(hwnd) != self.pid:
            raise ValueError("window PID changed")
        if self.backend.class_name(hwnd) != WINDOW_CLASS:
            raise ValueError("dialog class changed")
        if self.backend.text(hwnd) != WINDOW_TITLE:
            raise ValueError("dialog title mismatch")
        if self.backend.generation(hwnd) != self.expected_generation:
            raise ValueError("dialog generation mismatch")
        button = self.backend.item(hwnd, BUTTON_ID)
        message = self.backend.item(hwnd, MESSAGE_ID)
        if not button or not message or button == message:
            raise ValueError("required dialog controls missing")
        for name, child in (("button", button), ("message", message)):
            if self.backend.pid(child) != self.pid or self.backend.parent(child) != hwnd:
                raise ValueError(f"{name} ownership or parent mismatch")
        if self.backend.class_name(button).lower() != "button":
            raise ValueError("Cancel control is not a BUTTON")
        if self.backend.child_text(button) != "Cancel":
            raise ValueError("Cancel control name mismatch")
        if not self.backend.visible(hwnd) or not self.backend.visible(button):
            raise ValueError("dialog or Cancel control hidden")
        if not self.backend.enabled(hwnd) or not self.backend.enabled(button):
            raise ValueError("dialog or Cancel control disabled")
        before = self.backend.child_text(message)
        if normalized(before) != self.message:
            raise ValueError("dialog message mismatch")
        return button, before

    def tick(self):
        if self.status != "waiting":
            return self.status
        if not self._alive_owned():
            return self.status
        try:
            windows = self.backend.windows_for_pid(self.pid)
            matches = []
            for hwnd in windows:
                # Never read title or controls belonging to an unrelated PID.
                if self.backend.pid(hwnd) != self.pid:
                    continue
                if self.backend.class_name(hwnd) == WINDOW_CLASS:
                    matches.append(hwnd)
            if not matches:
                return self.status
            if len(matches) != 1:
                raise ValueError(f"ambiguous dialog count: {len(matches)}")
            hwnd = matches[0]
            button, before = self._target(hwnd)
            if not self._alive_owned():
                return self.status
            # Recheck full target immediately before the single allowed send.
            second_button, second_before = self._target(hwnd)
            if second_button != button or second_before != before:
                raise ValueError("dialog changed before input")
            self.attempted = True
            self._record("send-attempt", {"window": hwnd, "button": button,
                                          "before_text": before})
            try:
                self.backend.click(button)
            except Exception as exc:
                self.status = "unknown"
                self._record("unknown", f"{type(exc).__name__}: {exc}")
                return self.status
            self.status = "sent"
            self._record("sent", {"window": hwnd, "button": button,
                                   "before_text": before})
        except Exception as exc:
            self.status = "rejected"
            self._record("rejected", f"{type(exc).__name__}: {exc}")
        return self.status
