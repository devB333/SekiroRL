"""Read a physical Xbox-style controller through XInput. Query only.

Used to record the user's own play (demos.py). `XInputGetState` returns the
state of a pad; nothing is sent to the pad or to the game.

A pad that Windows does not present as XInput (for example a PlayStation pad
that only Steam translates, inside the game process) does not show up here.
"""
from dataclasses import dataclass

from .controller import BUTTONS

SLOTS = 4
# XINPUT_GAMEPAD button bits, by the ViGEm names that controller.BUTTONS uses.
MASKS = {
    "XUSB_GAMEPAD_DPAD_UP": 0x0001,
    "XUSB_GAMEPAD_DPAD_DOWN": 0x0002,
    "XUSB_GAMEPAD_DPAD_LEFT": 0x0004,
    "XUSB_GAMEPAD_DPAD_RIGHT": 0x0008,
    "XUSB_GAMEPAD_START": 0x0010,
    "XUSB_GAMEPAD_BACK": 0x0020,
    "XUSB_GAMEPAD_LEFT_THUMB": 0x0040,
    "XUSB_GAMEPAD_RIGHT_THUMB": 0x0080,
    "XUSB_GAMEPAD_LEFT_SHOULDER": 0x0100,
    "XUSB_GAMEPAD_RIGHT_SHOULDER": 0x0200,
    "XUSB_GAMEPAD_A": 0x1000,
    "XUSB_GAMEPAD_B": 0x2000,
    "XUSB_GAMEPAD_X": 0x4000,
    "XUSB_GAMEPAD_Y": 0x8000,
}
TRIGGERS = ("lt", "rt")
TRIGGER_PRESSED = 30        # XINPUT_GAMEPAD_TRIGGER_THRESHOLD, of 255
STICK_MAX = 32767.0


def mask(name):
    """Button bit for a logical name (controller.BUTTONS) or a ViGEm name."""
    return MASKS[BUTTONS.get(name, name)]


@dataclass(frozen=True)
class PadState:
    """Raw XINPUT_GAMEPAD values at one instant."""
    buttons: int = 0
    lt: int = 0             # triggers: 0..255
    rt: int = 0
    lx: int = 0             # sticks: -32768..32767, right and up positive
    ly: int = 0
    rx: int = 0
    ry: int = 0

    def pressed(self, name):
        """`name` is a logical button, a ViGEm button name, "lt" or "rt"."""
        if name in TRIGGERS:
            return getattr(self, name) > TRIGGER_PRESSED
        return bool(self.buttons & mask(name))

    def left_stick(self):
        """(x, y) in -1..1, like ControlState.stick."""
        return (max(-1.0, self.lx / STICK_MAX), max(-1.0, self.ly / STICK_MAX))

    @classmethod
    def from_control(cls, control):
        """The pad state a `ControlState` stands for (for tests and fakes)."""
        buttons = 0
        for name in control.buttons:
            buttons |= mask(name)
        return cls(buttons=buttons, lx=round(control.stick[0] * STICK_MAX),
                   ly=round(control.stick[1] * STICK_MAX))


class XInputPads:
    """The four XInput slots. `read(slot)` returns (packet number, PadState),
    or None when nothing is connected in that slot.

    The packet number changes whenever the pad's state does, so a jump of
    more than one between two reads means a state in between was not seen.
    """

    def __init__(self):
        import ctypes as C

        class Gamepad(C.Structure):
            _fields_ = [("buttons", C.c_ushort), ("lt", C.c_ubyte), ("rt", C.c_ubyte),
                        ("lx", C.c_short), ("ly", C.c_short),
                        ("rx", C.c_short), ("ry", C.c_short)]

        class State(C.Structure):
            _fields_ = [("packet", C.c_uint), ("pad", Gamepad)]

        try:
            xinput = C.WinDLL("xinput1_4")
        except OSError:
            xinput = C.WinDLL("xinput9_1_0")
        xinput.XInputGetState.argtypes = [C.c_uint, C.POINTER(State)]
        xinput.XInputGetState.restype = C.c_uint
        self._get_state = xinput.XInputGetState
        self._state = State
        self._byref = C.byref

    def read(self, slot):
        state = self._state()
        if self._get_state(slot, self._byref(state)) != 0:      # ERROR_SUCCESS is 0
            return None
        pad = state.pad
        return state.packet, PadState(pad.buttons, pad.lt, pad.rt, pad.lx, pad.ly, pad.rx, pad.ry)


# -- DualSense over HID ---------------------------------------------------------

SONY, DUALSENSE_IDS = 0x054C, (0x0CE6, 0x0DF2)     # DualSense, DualSense Edge
DUALSENSE_SLOT = 4      # the slot number the recorder gives a DualSense read over HID
# D-pad hat value -> XInput D-pad bits (up 1, down 2, left 4, right 8); 8 is released.
_HAT = (0x1, 0x1 | 0x8, 0x8, 0x2 | 0x8, 0x2, 0x2 | 0x4, 0x4, 0x1 | 0x4, 0x0)
# (byte of the button block, bit) -> the Xbox button in the same place on the pad,
# which is what Steam's default layout presents to the game.
_DUALSENSE_BUTTONS = (
    (0, 0x10, "XUSB_GAMEPAD_X"),                # square
    (0, 0x20, "XUSB_GAMEPAD_A"),                # cross
    (0, 0x40, "XUSB_GAMEPAD_B"),                # circle
    (0, 0x80, "XUSB_GAMEPAD_Y"),                # triangle
    (1, 0x01, "XUSB_GAMEPAD_LEFT_SHOULDER"),    # L1
    (1, 0x02, "XUSB_GAMEPAD_RIGHT_SHOULDER"),   # R1
    (1, 0x10, "XUSB_GAMEPAD_BACK"),             # create
    (1, 0x20, "XUSB_GAMEPAD_START"),            # options
    (1, 0x40, "XUSB_GAMEPAD_LEFT_THUMB"),       # L3
    (1, 0x80, "XUSB_GAMEPAD_RIGHT_THUMB"),      # R3
)


def _axis(value, invert=False):
    """Stick byte (0..255, centre 128) to the XInput range."""
    scaled = max(-32767, min(32767, round((value - 128) * 32767 / 127)))
    return -scaled if invert else scaled


def dualsense_state(report):
    """`PadState` from one DualSense input report, laid out as an Xbox pad.

    Three report shapes: USB (64 bytes, ID 0x01), Bluetooth full (ID 0x31, the
    same fields one byte later) and Bluetooth simple (ID 0x01, shorter, with
    the triggers after the buttons). Only the USB shape has been seen from a
    real controller. Returns None for any other report.
    """
    if len(report) == 64 and report[0] == 0x01:
        sticks, triggers, buttons = report[1:5], report[5:7], report[8:10]
    elif len(report) >= 12 and report[0] == 0x31:
        sticks, triggers, buttons = report[2:6], report[6:8], report[9:11]
    elif len(report) >= 10 and report[0] == 0x01:
        sticks, triggers, buttons = report[1:5], report[8:10], report[5:7]
    else:
        return None
    bits = _HAT[min(buttons[0] & 0x0F, 8)]
    for byte, bit, name in _DUALSENSE_BUTTONS:
        if buttons[byte] & bit:
            bits |= MASKS[name]
    # The stick's Y byte grows downward; XInput's Y grows upward.
    return PadState(buttons=bits, lt=triggers[0], rt=triggers[1],
                    lx=_axis(sticks[0]), ly=_axis(sticks[1], invert=True),
                    rx=_axis(sticks[2]), ry=_axis(sticks[3], invert=True))


def find_hid_paths(vendor, products):
    """Device paths of the connected HID devices with these IDs. Query only."""
    import ctypes as C
    from ctypes import wintypes as W

    class Guid(C.Structure):
        _fields_ = [("a", C.c_ulong), ("b", C.c_ushort), ("c", C.c_ushort), ("d", C.c_ubyte * 8)]

    class InterfaceData(C.Structure):
        _fields_ = [("cbSize", W.DWORD), ("guid", Guid), ("flags", W.DWORD),
                    ("reserved", C.c_void_p)]

    hid = C.WinDLL("hid")
    setup = C.WinDLL("setupapi", use_last_error=True)
    hid.HidD_GetHidGuid.argtypes = [C.POINTER(Guid)]
    setup.SetupDiGetClassDevsW.argtypes = [C.POINTER(Guid), W.LPCWSTR, W.HWND, W.DWORD]
    setup.SetupDiGetClassDevsW.restype = C.c_void_p
    setup.SetupDiEnumDeviceInterfaces.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(Guid),
                                                  W.DWORD, C.POINTER(InterfaceData)]
    setup.SetupDiEnumDeviceInterfaces.restype = W.BOOL
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes = [C.c_void_p, C.POINTER(InterfaceData),
                                                       C.c_void_p, W.DWORD,
                                                       C.POINTER(W.DWORD), C.c_void_p]
    setup.SetupDiGetDeviceInterfaceDetailW.restype = W.BOOL
    setup.SetupDiDestroyDeviceInfoList.argtypes = [C.c_void_p]

    guid = Guid()
    hid.HidD_GetHidGuid(C.byref(guid))
    devices = setup.SetupDiGetClassDevsW(C.byref(guid), None, None, 0x02 | 0x10)  # present, interfaces
    if not devices or devices == C.c_void_p(-1).value:
        return []
    wanted = [f"vid_{vendor:04x}&pid_{product:04x}" for product in products]
    paths = []
    try:
        index = 0
        while True:
            data = InterfaceData()
            data.cbSize = C.sizeof(InterfaceData)
            if not setup.SetupDiEnumDeviceInterfaces(devices, None, C.byref(guid), index,
                                                     C.byref(data)):
                break
            index += 1
            needed = W.DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(data), None, 0,
                                                   C.byref(needed), None)
            detail = C.create_string_buffer(needed.value)
            C.cast(detail, C.POINTER(W.DWORD))[0] = 8       # cbSize of the detail struct, x64
            if setup.SetupDiGetDeviceInterfaceDetailW(devices, C.byref(data), detail,
                                                      needed.value, None, None):
                path = C.wstring_at(C.addressof(detail) + 4)
                if any(text in path.lower() for text in wanted):
                    paths.append(path)
    finally:
        setup.SetupDiDestroyDeviceInfoList(devices)
    return paths


class DualSensePad:
    """A DualSense read straight from its HID input reports, for a pad that
    XInput does not show (Steam translates it only inside the game).

    The device is opened for reading, shared with everything else that has it
    open; nothing is written to it. A background thread keeps the latest
    state. `read()` returns (change count, PadState), or None while no
    controller is connected. The change count plays the part of XInput's
    packet number.
    """

    def __init__(self, retry_s=1.0):
        import ctypes as C
        from ctypes import wintypes as W
        import threading
        import time
        self._C, self._time = C, time
        self._k = C.WinDLL("kernel32", use_last_error=True)
        self._k.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD,
                                        W.DWORD, W.HANDLE]
        self._k.CreateFileW.restype = W.HANDLE
        self._k.ReadFile.argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD),
                                     C.c_void_p]
        self._k.ReadFile.restype = W.BOOL
        self._k.CloseHandle.argtypes = [W.HANDLE]
        self.retry_s = retry_s
        self.path = None
        self._latest = None         # (change count, PadState) or None
        self._changes = 0
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="dualsense-reader", daemon=True)
        self._thread.start()

    def _open(self):
        for path in find_hid_paths(SONY, DUALSENSE_IDS):
            # GENERIC_READ; share read and write; OPEN_EXISTING.
            handle = self._k.CreateFileW(path, 0x80000000, 0x1 | 0x2, None, 3, 0, None)
            if handle and handle != self._C.c_void_p(-1).value:
                self.path = path
                return handle
        return None

    def _run(self):
        C = self._C
        buffer = C.create_string_buffer(256)
        got = C.c_ulong()
        while not self._stop:
            handle = self._open()
            if handle is None:
                self._latest = None
                self._time.sleep(self.retry_s)
                continue
            while not self._stop:
                if not self._k.ReadFile(handle, buffer, len(buffer), C.byref(got), None):
                    break                   # unplugged
                state = dualsense_state(buffer.raw[:got.value])
                if state is not None and (self._latest is None or state != self._latest[1]):
                    self._changes += 1
                    self._latest = (self._changes, state)
            self._k.CloseHandle(handle)
            self._latest = None

    def read(self):
        return self._latest

    def close(self):
        self._stop = True


class Pads:
    """XInput slots 0 to 3, and a DualSense over HID as slot `DUALSENSE_SLOT`."""

    slots = tuple(range(SLOTS)) + (DUALSENSE_SLOT,)

    def __init__(self):
        self.xinput = XInputPads()
        self.dualsense = DualSensePad()

    def read(self, slot):
        if slot == DUALSENSE_SLOT:
            return self.dualsense.read()
        return self.xinput.read(slot)
