"""Small Windows helpers: which window has focus. Uses user32 queries only."""
import ctypes as C
from ctypes import wintypes as W


def foreground_window():
    """Return (pid, title) of the foreground window, or (None, "") if there is none."""
    user32 = C.WinDLL("user32", use_last_error=True)
    user32.GetForegroundWindow.restype = W.HWND
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, C.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, C.c_int]
    user32.GetWindowTextW.restype = C.c_int
    window = user32.GetForegroundWindow()
    if not window:
        return None, ""
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(window, C.byref(pid))
    title = C.create_unicode_buffer(512)
    user32.GetWindowTextW(window, title, len(title))
    return pid.value, title.value


def focus_check_for(pid):
    """Return a callable that is true while `pid` owns the foreground window."""
    return lambda: foreground_window()[0] == pid
