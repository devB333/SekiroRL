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


ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_DISPLAY_REQUIRED = 0x80000000, 0x00000001, 0x00000002


def keep_awake(on=True):
    """Ask Windows not to sleep or turn the display off while this thread
    lives (`on=False` withdraws the request). Controller input does not count
    as activity for Windows, so a long run would otherwise end at the idle
    timeout: with the display off or the PC locked the game is no longer the
    foreground window. Returns False if the call failed.
    """
    kernel32 = C.WinDLL("kernel32", use_last_error=True)
    kernel32.SetThreadExecutionState.argtypes = [C.c_uint]
    kernel32.SetThreadExecutionState.restype = C.c_uint
    wanted = ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED if on else 0
    return kernel32.SetThreadExecutionState(ES_CONTINUOUS | wanted) != 0

