"""Discrete action set and the press/release schedule each action produces.

One flat list: every movement combined with every button behaviour. Flat so
the same space works for behaviour cloning, PPO and replay-buffer methods.
"""
from .controller import ControlState

MOVEMENTS = (
    ("stay", (0.0, 0.0)),
    ("forward", (0.0, 1.0)),      # camera-relative; toward the boss while locked on
    ("back", (0.0, -1.0)),
    ("left", (-1.0, 0.0)),
    ("right", (1.0, 0.0)),
)

# name, button, kind. "tap": press then release inside the step. "hold": keep
# pressed with no release between consecutive steps. "repress": release briefly
# and press again, giving the fresh press edge a deflect needs, then keep held.
BUTTON_MODES = (
    ("none", None, None),
    ("attack", "attack", "tap"),
    ("guard_hold", "guard", "hold"),
    ("deflect", "guard", "repress"),
    ("dodge", "dodge", "tap"),
    ("jump", "jump", "tap"),
)

ACTIONS = tuple((move, mode) for mode in range(len(BUTTON_MODES))
                for move in range(len(MOVEMENTS)))
ACTION_NAMES = tuple(f"{MOVEMENTS[move][0]}+{BUTTON_MODES[mode][0]}" for move, mode in ACTIONS)
NUM_ACTIONS = len(ACTIONS)
NOOP = ACTION_NAMES.index("stay+none")


def action_index(name):
    return ACTION_NAMES.index(name)


def schedule(action, previous, tap_s=0.05, repress_gap_s=0.034):
    """Return [(seconds after step start, ControlState), ...] for one step.

    `previous` is the ControlState held when the step starts. The last entry
    is what stays held until the next step.
    """
    move, mode = ACTIONS[action]
    stick = MOVEMENTS[move][1]
    _, button, kind = BUTTON_MODES[mode]
    released = ControlState(stick=stick)
    if kind is None:
        return [(0.0, released)]
    pressed = ControlState(buttons=frozenset({button}), stick=stick)
    if kind == "tap":
        return [(0.0, pressed), (tap_s, released)]
    if kind == "hold":
        return [(0.0, pressed)]
    if button in previous.buttons:      # repress while already held
        return [(0.0, released), (repress_gap_s, pressed)]
    return [(0.0, pressed)]             # not held: pressing now is already a fresh edge


def validate_timing(step_interval, tap_s, repress_gap_s, min_gap_s=0.03):
    """A tap must end early enough that the next step's press is a new edge."""
    if step_interval <= 0:
        raise ValueError("step_interval must be positive")
    if tap_s <= 0 or repress_gap_s <= 0:
        raise ValueError("tap_s and repress_gap_s must be positive")
    if tap_s + min_gap_s > step_interval:
        raise ValueError(f"tap_s ({tap_s}) + {min_gap_s} s release gap exceeds the "
                         f"step interval ({step_interval})")
    if repress_gap_s >= step_interval:
        raise ValueError("repress_gap_s must be shorter than the step interval")
