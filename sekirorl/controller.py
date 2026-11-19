"""Controller output. `ControlState` is the complete pad state at one instant.

Sending a state releases everything not named in it, so there is no hidden
"still pressed" state outside the last `ControlState` applied.
"""
import atexit
from dataclasses import dataclass
import threading
from typing import FrozenSet, Tuple

# Logical button -> Xbox 360 pad button (default Sekiro bindings; confirm in game).
BUTTONS = {
    "attack": "XUSB_GAMEPAD_RIGHT_SHOULDER",   # RB
    "guard": "XUSB_GAMEPAD_LEFT_SHOULDER",     # LB: hold to block, fresh press to deflect
    "dodge": "XUSB_GAMEPAD_B",                 # B
    "jump": "XUSB_GAMEPAD_A",                  # A
    "lock_on": "XUSB_GAMEPAD_RIGHT_THUMB",     # R3, not part of the agent's action set
}


@dataclass(frozen=True)
class ControlState:
    buttons: FrozenSet[str] = frozenset()
    stick: Tuple[float, float] = (0.0, 0.0)    # left stick x (right +), y (forward +)

    def __post_init__(self):
        unknown = set(self.buttons) - set(BUTTONS)
        if unknown:
            raise ValueError(f"Unknown buttons: {sorted(unknown)}")


NEUTRAL = ControlState()


class RecordingController:
    """Controller that only records what it was told. For tests and dry runs."""

    def __init__(self, time_fn=None):
        self.state = NEUTRAL
        self.log = []           # (time or None, ControlState)
        self.closed = False
        self._time_fn = time_fn

    def apply(self, state):
        self.state = state
        self.log.append((self._time_fn() if self._time_fn else None, state))

    def neutral(self):
        self.apply(NEUTRAL)

    def close(self):
        self.neutral()
        self.closed = True


class VigemController:
    """Virtual Xbox 360 pad through `vgamepad` (needs the ViGEmBus driver).

    `vgamepad` is imported here, not at module import, because importing it
    loads a Windows DLL. Calls are locked so the watchdog thread can release
    inputs safely.
    """

    def __init__(self):
        import vgamepad
        self._vg = vgamepad
        self._pad = vgamepad.VX360Gamepad()
        self._lock = threading.Lock()
        self.state = NEUTRAL
        atexit.register(self._atexit)

    def apply(self, state):
        with self._lock:
            if self._pad is None:
                return
            self._pad.reset()
            for name in state.buttons:
                self._pad.press_button(button=getattr(self._vg.XUSB_BUTTON, BUTTONS[name]))
            self._pad.left_joystick_float(x_value_float=state.stick[0],
                                          y_value_float=state.stick[1])
            self._pad.update()
            self.state = state

    def neutral(self):
        self.apply(NEUTRAL)

    def close(self):
        self.neutral()
        with self._lock:
            self._pad = None    # dropping the pad unplugs the virtual device

    def _atexit(self):
        try:
            self.neutral()
        except Exception:
            pass
