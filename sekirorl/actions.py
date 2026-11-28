"""Discrete action set and the press/release schedule each action produces.

One flat list: every movement combined with every button behaviour. Flat so
the same space works for behaviour cloning, PPO and replay-buffer methods.
"""
from .controller import ControlState

_DIAGONAL = 0.7071      # the stick on its rim, halfway between two directions

MOVEMENTS = (
    ("stay", (0.0, 0.0)),
    ("forward", (0.0, 1.0)),      # camera-relative; toward the boss while locked on
    ("back", (0.0, -1.0)),
    ("left", (-1.0, 0.0)),
    ("right", (1.0, 0.0)),
    # Diagonals, added 2026-11-27: in the user's recorded fights 16% of all
    # steps (a fifth of the steps with the stick out) were nearer a diagonal
    # than a straight direction, mostly forward-right and back-right.
    ("forward_left", (-_DIAGONAL, _DIAGONAL)),
    ("forward_right", (_DIAGONAL, _DIAGONAL)),
    ("back_left", (-_DIAGONAL, -_DIAGONAL)),
    ("back_right", (_DIAGONAL, -_DIAGONAL)),
)

# name, button, kind. "tap": press then release inside the step. "hold": keep
# pressed with no release between consecutive steps. "repress": release briefly
# and press again, giving the fresh press edge a deflect needs, then keep held.
# A tuple of buttons is pressed and released together.
#
# The last four were added on 2026-11-27 at the user's request and have not
# been tried on the live game yet:
# - sprint: dodge held. The first step of it is a dodge step, as in the game.
# - heal: the selected quick item, which has to be the Healing Gourd.
# - thrust: attack held. The charged attack comes out when another action
#   releases it, or by itself at full charge.
# - combat_art: guard and attack together.
# A tap of a button that a hold mode already has down adds no new press: the
# button is released at the end of the tap.
BUTTON_MODES = (
    ("none", None, None),
    ("attack", "attack", "tap"),
    ("guard_hold", "guard", "hold"),
    ("deflect", "guard", "repress"),
    ("dodge", "dodge", "tap"),
    ("jump", "jump", "tap"),
    ("sprint", "dodge", "hold"),
    ("heal", "use_item", "tap"),
    ("thrust", "attack", "hold"),
    ("combat_art", ("guard", "attack"), "tap"),
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
    buttons = frozenset((button,) if isinstance(button, str) else button)
    pressed = ControlState(buttons=buttons, stick=stick)
    if kind == "tap":
        return [(0.0, pressed), (tap_s, released)]
    if kind == "hold":
        return [(0.0, pressed)]
    if button in previous.buttons:      # repress while already held
        return [(0.0, released), (repress_gap_s, pressed)]
    return [(0.0, pressed)]             # not held: pressing now is already a fresh edge


def with_tap(plan, button, tap_s=0.05):
    """The same plan with one more button pressed at the step start and
    released `tap_s` later. For a button outside the action set (lock-on)."""
    out, current, released = [], None, False
    for offset, state in plan:
        if offset < tap_s:
            current = state
            out.append((offset, ControlState(buttons=state.buttons | {button}, stick=state.stick)))
            continue
        if not released and offset > tap_s:
            out.append((tap_s, current))
        released = True
        out.append((offset, state))
    if not released:
        out.append((tap_s, current))
    return out


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
