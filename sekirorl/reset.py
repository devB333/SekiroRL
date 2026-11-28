"""Automatic reset: from wherever an episode ended, back into the rematch.

Pass a `RematchReset` to `StepLoop` as `reset_hook`. It only gets the player
into the loading fight; `Encounter.ready` then decides when the fight is running.

The button presses are data (`RematchScript`). Nothing here trusts timing
alone: after each part of the script the reset waits for the game state that
part should produce, read from memory, and raises `ResetFailed` naming the
stage if it does not arrive.

Every animation ID and the timings in the comments come from the sessions
logged on 2026-11-25 (four rematch entries, four final deaths, two
resurrections), all at one idol on one save.
"""
import cmath
from dataclasses import dataclass
import math
from typing import Tuple

from .controller import NEUTRAL, ControlState
from .encounter import PLAYER_INTRO_ANIMATIONS
from .loop import ResetFailed

# Player animations.
SITTING = 710320            # idol menu open; `710310` (1.1 s) is the sit-down before it
ENTERING = 710360           # rematch confirmed; 2.6 s, then the loading screen
# HP 0 plays `110000` for 3.3 s. With a resurrection left, `110020` follows and
# holds for as long as the Resurrect/Die prompt is open. `110010` (4.1 s, then
# the loading screen) is the final death; without a resurrection it follows
# `110000` directly and no prompt shows.
#
# Those IDs are not the only ones. On 2026-11-27 the boss killed a player who
# stood still with a different move, and the death ran `227600170` (2.8 s),
# `227600171` (5.5 s), `227610172` (15.0 s with no input sent), `227610171`
# (4.2 s), then the loading screen. So the reset does not wait for a known
# prompt animation: it presses the Die input at intervals for as long as the
# player's HP is 0.
DEATH_PROMPT = 110020

# States returned by `classify`.
LOADING = "loading"         # no valid player HP
DEAD = "dead"               # player HP 0
FIGHT = "fight"             # the boss is loaded and the player is alive
SAT = "sitting"             # at the idol with its menu open
BUSY = "busy"               # alive without control: respawning or entering the rematch
IDOL = "idol"               # standing with control at the respawn point
OFF_MARK = "off_mark"       # standing near the idol, but not on the respawn point
AWAY = "away"               # alive, no boss, nowhere near the idol
STATES = frozenset({LOADING, DEAD, FIGHT, SAT, BUSY, IDOL, OFF_MARK, AWAY})


@dataclass(frozen=True)
class Press:
    """Press one button, release it, then wait before the next step."""
    button: str
    hold_s: float = 0.08
    then_s: float = 0.4


@dataclass(frozen=True)
class Stick:
    """Hold the left stick (x right +, y forward +), release it, then wait."""
    direction: Tuple[float, float]
    seconds: float
    then_s: float = 0.3


@dataclass(frozen=True)
class Wait:
    seconds: float


@dataclass(frozen=True)
class WalkTo:
    """Walk to a point on the ground (x, z), steering by the position read
    from memory.

    The stick is relative to the camera and the camera is not read. So the
    walk goes in short stretches: push the stick, release it, wait until the
    player stands still, and see where that led. The first stretch is a short
    push forward; where the player went gives the camera's forward. How far a
    second of stick carries the player is measured the same way, and each
    later stretch is aimed and timed from the last one. Whether stick right
    is clockwise or anticlockwise of forward on the map is worked out from
    the first turn.

    The walk fails instead of wandering: when the player is farther than
    `max_start_m` at the start, gets `stray_m` farther away than they
    started, or has not arrived after `timeout_s`.
    """
    point: Tuple[float, float]
    within_m: float = 0.3
    timeout_s: float = 12.0
    max_start_m: float = 5.0
    stray_m: float = 1.5
    run_beyond_m: float = 1.5       # farther than this the stick is pushed all the way
    slow_stick: float = 0.6         # otherwise this far
    probe_s: float = 0.12           # length of a stretch whose speed is not known yet
    max_hold_s: float = 1.5
    aim: float = 0.9                # part of the remaining distance a stretch goes for
    settle_s: float = 0.25          # after releasing the stick, before looking
    poll_s: float = 0.05
    then_s: float = 0.2


def presses(button, times=1, **timing):
    return (Press(button, **timing),) * times


@dataclass(frozen=True)
class RematchScript:
    """The inputs for one idol and one rematch entry.

    walk: from the respawn point to where the idol can be used. Empty if it
        can be used from the respawn point. `Stick` steps only work from the
        respawn point; a walk made of `WalkTo` steps works from anywhere near.
    sit: open the idol menu.
    menu: from the idol menu to the rematch being confirmed.
    die: choose "Die" at the Resurrect/Die prompt (D9).
    abandon: leave a fight that is still running. Empty means stand still
        until the boss kills the player.
    back: with it, a reset that finds the idol menu already open backs out of
        it and sits down again, so the menu inputs always start from the first
        entry. Without it the menu is taken to be at its first entry.
    respawn: where the player stands after a death or a load.
    """
    walk: tuple
    sit: tuple
    menu: tuple
    die: tuple
    respawn: Tuple[float, float, float]
    abandon: tuple = ()
    back: str = None            # button that backs out of the idol menu, one level a press
    on_mark_m: float = 0.5      # closer than this to `respawn` counts as on it
    idol_area_m: float = 10.0   # farther than this is not at the idol at all

    @property
    def steers(self):
        """True if the walk finds the idol from wherever the player stands."""
        return bool(self.walk) and all(isinstance(step, WalkTo) for step in self.walk)


# The Genichiro rematch on the user's save.
#
# Inputs from the user's notes of 2026-11-27: walk up to the idol, X to sit;
# the cursor always starts on the first entry; 8 down and A opens Reflections
# of Strength; 2 down is "Genichiro Ashina"; A picks it and A confirms. At the
# Resurrect/Die prompt LB is Die. There is no way out of a running fight, and
# the fight goes on after the first deathblow, so `abandon` stays empty.
#
# Positions from the logs of 2026-11-25: the respawn point after every death
# and load; the player sat down at x 163.3 to 163.8, z 22.4 to 23.2, after
# running 2.1 to 2.6 m. Whether the idol can be used from the respawn point
# itself is not known, so the script always walks.
#
# The menu timings are guesses on the slow side, to be shortened after live
# runs. The menu cannot be read from memory: a lost press would pick a
# neighbouring entry.
GENICHIRO_REMATCH = RematchScript(
    walk=(WalkTo((163.5, 22.8)),),
    sit=presses("pad_x"),
    menu=((Wait(1.0),)
          + presses("dpad_down", 8, hold_s=0.1, then_s=0.3)
          + presses("pad_a", hold_s=0.1, then_s=1.0)
          + presses("dpad_down", 2, hold_s=0.1, then_s=0.3)
          + presses("pad_a", hold_s=0.1, then_s=1.0)
          + presses("pad_a", hold_s=0.1)),
    die=presses("guard"),
    respawn=(161.22, -29.01, 23.0),
    back="pad_b",               # assumed; not tried on the game
)


def classify(snapshot, script):
    """Which of the states above a snapshot shows."""
    if not snapshot.valid:
        return LOADING
    if snapshot.player_hp <= 0:
        return DEAD
    if snapshot.boss_hp is not None:
        return FIGHT
    animation = snapshot.player_anim_id
    if animation == SITTING:
        return SAT
    if animation is None or animation in PLAYER_INTRO_ANIMATIONS or animation == ENTERING:
        return BUSY
    if snapshot.player_pos is None:
        return BUSY
    distance = math.dist(snapshot.player_pos, script.respawn)
    if distance <= script.on_mark_m:
        return IDOL
    return OFF_MARK if distance <= script.idol_area_m else AWAY


class LockKeeper:
    """Keeps lock-on on the boss. Pass to `StepLoop(lock_keeper=...)`; the loop
    asks once per step whether to tap lock-on (R3) in that step.

    With lock-on the camera follows the boss and the stick directions of the
    action set are relative to him. The press is a toggle, so it is only made
    when the snapshot says the lock is not on the boss (`locked_on` is False),
    and not again for `retry_s`.

    Two guards against a reading that is wrong, where pressing would switch a
    good lock off again and again:
    - If the lock has never read as on in this fight after `max_unconfirmed`
      presses, no more presses are made in this fight.
    - If `locked_on` is None (unreadable), lock-on is pressed once per fight.
    """

    def __init__(self, retry_s=1.0, max_unconfirmed=3):
        self.retry_s = retry_s
        self.max_unconfirmed = max_unconfirmed
        self.begin()

    def begin(self):
        self.presses = 0
        self.confirmed = False      # the lock has read as on at least once in this fight
        self.gave_up = False
        self._pressed_at = None

    def __call__(self, snapshot):
        if snapshot.locked_on:
            self.confirmed = True
            return False
        now = snapshot.t_ns / 1e9
        if self._pressed_at is not None and now - self._pressed_at < self.retry_s:
            return False
        if snapshot.locked_on is None and self.presses:
            return False
        if not self.confirmed and self.presses >= self.max_unconfirmed:
            self.gave_up = True
            return False
        self.presses += 1
        self._pressed_at = now
        return True


class RematchReset:
    """Callable reset hook. `source`, `controller` and `clock` are the ones the
    step loop uses. `focus_check` is asked before every input; without focus
    the reset fails instead of pressing buttons into another window.

    `log` collects (seconds since the call started, message) for the last call.
    """

    MAX_LEAVES = 3

    def __init__(self, source, controller, clock, script, focus_check=None, poll_s=0.1,
                 death_timeout_s=180.0, respawn_timeout_s=40.0, sit_timeout_s=6.0,
                 confirm_timeout_s=6.0, load_timeout_s=30.0, prompt_retry_s=1.5,
                 steady_s=0.5):
        self.source = source
        self.controller = controller
        self.clock = clock
        self.script = script
        self.focus_check = focus_check
        self.poll_s = poll_s
        self.death_timeout_s = death_timeout_s      # a boss killing a player who stands still
        self.respawn_timeout_s = respawn_timeout_s  # HP 0 to standing at the idol
        self.sit_timeout_s = sit_timeout_s
        self.confirm_timeout_s = confirm_timeout_s  # end of the menu inputs to ENTERING
        self.load_timeout_s = load_timeout_s        # ENTERING to the boss being loaded
        self.prompt_retry_s = prompt_retry_s
        self.steady_s = steady_s                    # standing at the idol this long before input
        self.log = []
        self._start = 0.0

    # -- helpers -----------------------------------------------------------

    def _note(self, message):
        self.log.append((round(self.clock.now() - self._start, 3), message))

    def _fail(self, message):
        self._note("FAILED: " + message)
        raise ResetFailed(message)

    def _sleep(self, seconds):
        self.clock.sleep_until(self.clock.now() + seconds)

    def _look(self):
        snapshot = self.source.read()
        return classify(snapshot, self.script), snapshot

    def _play(self, steps, what):
        self._note(what)
        for step in steps:
            if self.focus_check is not None and not self.focus_check():
                self.controller.neutral()
                self._fail(f"game window not in foreground while trying to {what}")
            if isinstance(step, Press):
                self.controller.apply(ControlState(buttons=frozenset({step.button})))
                self._sleep(step.hold_s)
                self.controller.neutral()
                self._sleep(step.then_s)
            elif isinstance(step, Stick):
                self.controller.apply(ControlState(stick=step.direction))
                self._sleep(step.seconds)
                self.controller.neutral()
                self._sleep(step.then_s)
            elif isinstance(step, Wait):
                self._sleep(step.seconds)
            elif isinstance(step, WalkTo):
                self._walk_to(step, what)
            else:
                raise TypeError(f"Not a script step: {step!r}")

    def _walk_to(self, step, what):
        """Positions and directions are complex numbers, x + z*1j on the map
        and right + forward*1j on the stick. With the camera's forward at
        `forward` on the map, the stick moves the player along
        `forward * (stick_forward - 1j * hand * stick_right)`.
        """
        def where():
            state, snapshot = self._look()
            if state not in (IDOL, OFF_MARK):
                self.controller.neutral()
                self._fail(f"walking to the idol: the player is now '{state}' "
                           f"(player animation {snapshot.player_anim_id})")
            return complex(snapshot.player_pos[0], snapshot.player_pos[2])

        def stand_still():
            self.controller.neutral()
            self._sleep(step.settle_s)
            last = where()
            for _ in range(20):
                self._sleep(step.poll_s)
                now = where()
                if abs(now - last) < 0.01:
                    return now
                last = now
            return last

        def moves(stick, hand):
            return complex(stick.imag, -hand * stick.real)

        target = complex(*step.point)
        here = where()
        start_m = abs(target - here)
        if start_m > step.max_start_m:
            self._fail(f"walking to the idol: it is {start_m:.1f} m away, "
                       f"more than the {step.max_start_m:.1f} m this walk is allowed")
        give_up = self.clock.now() + step.timeout_s
        forward, hand = None, 1
        speed = {}                      # stick size -> metres per second of holding it
        probe_s = step.probe_s
        first = None                    # (stick, direction moved) of the first stretch
        stretches = 0
        while abs(target - here) > step.within_m:
            offset = target - here
            if abs(offset) > start_m + step.stray_m or self.clock.now() >= give_up:
                self._fail(f"walking to the idol: {abs(offset):.1f} m away after {stretches} "
                           f"stretches, having started {start_m:.1f} m away; stopped"
                           + ("" if speed else ". The stick never moved the player: the game "
                              "may be listening to another controller"))
            if self.focus_check is not None and not self.focus_check():
                self._fail(f"game window not in foreground while trying to {what}")
            if forward is None:
                stick, size = 1j, step.slow_stick
            else:
                wanted = offset / abs(offset) / forward
                stick = complex(-hand * wanted.imag, wanted.real)
                size = 1.0 if abs(offset) > step.run_beyond_m else step.slow_stick
            hold_s = probe_s
            if forward is not None and size in speed:
                hold_s = min(step.max_hold_s, step.aim * abs(offset) / speed[size])
            self.controller.apply(ControlState(stick=(stick.real * size, stick.imag * size)))
            self._sleep(hold_s)
            moved = stand_still() - here
            here += moved
            stretches += 1
            if abs(moved) < 0.03:
                probe_s = min(step.max_hold_s, probe_s * 2)     # too short a push to move
                continue
            speed[size] = abs(moved) / hold_s
            moved /= abs(moved)
            if first is None:
                first = (stick, moved)
            elif abs(cmath.phase(stick / first[0])) > 0.5:
                # Two different stick directions: keep the handedness under
                # which both give the same camera direction.
                def disagreement(h):
                    return abs(cmath.phase((moved / moves(stick, h))
                                           / (first[1] / moves(first[0], h))))
                if disagreement(-hand) + 0.4 < disagreement(hand):
                    hand = -hand
                    self._note("stick right is the other way round on the map; corrected")
            forward = moved / moves(stick, hand)
        self._note(f"walked {start_m:.1f} m in {stretches} stretches, "
                   f"now {abs(target - here):.2f} m from the mark")
        self._sleep(step.then_s)

    def _wait(self, wanted, timeout_s, stage, each_poll=None, steady_s=0.0):
        """Poll until the state is one of `wanted` and has been that same
        state for `steady_s`; return (state, snapshot)."""
        give_up = self.clock.now() + timeout_s
        last, since = None, 0.0
        while True:
            state, snapshot = self._look()
            if state != last:
                self._note(f"state: {state}")
                last, since = state, self.clock.now()
            if state in wanted and self.clock.now() - since >= steady_s:
                return state, snapshot
            if self.clock.now() >= give_up:
                self._fail(f"{stage}: still '{state}' after {timeout_s:.0f} s "
                           f"(player animation {snapshot.player_anim_id})")
            if each_poll is not None:
                each_poll(state, snapshot)
            self._sleep(self.poll_s)

    # -- stages ------------------------------------------------------------

    def _leave_fight(self):
        if self.script.abandon:
            self._play(self.script.abandon, "leave the fight")
            self._wait(STATES - {FIGHT, AWAY, BUSY}, self.death_timeout_s, "leaving the fight")
            return
        self._note("no way to leave the fight: standing still until the boss kills the player")
        self._wait({DEAD, LOADING}, self.death_timeout_s, "waiting to be killed")

    def _leave_menu(self, tries=6):
        """Back out of an idol menu left open at an entry that is not known."""
        for _ in range(tries):
            self._play(presses(self.script.back, then_s=0.8), "back out of the idol menu")
            state, snapshot = self._look()
            if state != SAT:
                return self._wait({IDOL, OFF_MARK}, self.sit_timeout_s, "standing up from the idol",
                                  steady_s=self.steady_s)
        self._fail(f"leaving the idol menu: still sitting after {tries} presses of "
                   f"{self.script.back}")

    def _to_idol(self):
        """Get from a death or a loading screen to the idol. While the player's
        HP is 0 the Die input is pressed every `prompt_retry_s`, prompt or not.
        Also returns if the player turns out to be alive away from the idol."""
        pressed_at = None

        def answer_prompt(state, snapshot):
            nonlocal pressed_at
            if state != DEAD:
                return
            if not self.script.die:
                if snapshot.player_anim_id == DEATH_PROMPT:
                    self._fail("the Resurrect/Die prompt is open and the script has no "
                               "inputs for it")
                return
            now = self.clock.now()
            if pressed_at is None or now - pressed_at >= self.prompt_retry_s:
                pressed_at = now
                self._play(self.script.die, "choose Die")

        # Steady for a moment: a respawn passes through animations that are not
        # all known, and one of them could look like standing with control.
        return self._wait({IDOL, OFF_MARK, SAT, FIGHT, AWAY}, self.respawn_timeout_s,
                          "waiting to respawn at the idol", answer_prompt,
                          steady_s=self.steady_s)

    def __call__(self):
        self._start = self.clock.now()
        self.log = []
        self.controller.neutral()
        try:
            state, snapshot = self._look()
            self._note(f"state: {state}")
            # A loading screen does not have to be the respawn: after the boss's
            # last deathblow it may be a cutscene with the fight going on after it.
            for _ in range(self.MAX_LEAVES):
                if state in (FIGHT, AWAY):
                    self._leave_fight()
                state, snapshot = self._to_idol()
                if state not in (FIGHT, AWAY):
                    break
            else:
                self._fail(f"still '{state}' after leaving a fight {self.MAX_LEAVES} times")
            if state == SAT and self.script.back:
                state, snapshot = self._leave_menu()
            if state == OFF_MARK and not self.script.steers:
                distance = math.dist(snapshot.player_pos, self.script.respawn)
                self._fail(f"standing {distance:.1f} m from the respawn point; the walk to the "
                           f"idol only works from there. Sit at the idol and start again")
            if state in (IDOL, OFF_MARK):
                self._play(self.script.walk + self.script.sit, "walk to the idol and sit")
                self._wait({SAT}, self.sit_timeout_s, "sitting down at the idol")
            self._play(self.script.menu, "pick the rematch in the idol menu")
            # Still sitting after the inputs means the menu did not take them as
            # written. Once the player is out of the seat, allow for the load.
            self._wait(STATES - {SAT}, self.confirm_timeout_s,
                       "confirming the rematch in the menu")
            state, _ = self._wait({FIGHT, IDOL, OFF_MARK, AWAY}, self.load_timeout_s,
                                  "loading the rematch")
            if state != FIGHT:
                self._fail(f"left the idol menu but the boss is not loaded (now '{state}'): "
                           f"the menu inputs picked something else")
            self._note("boss loaded")
        finally:
            self.controller.neutral()


class RetryingReset:
    """A reset hook that tries again when the reset fails, for unattended runs.

    The hook starts from whatever the failed try left behind. A failure
    because the game window is not in front is never retried: that is how a
    person stops a run.
    """

    def __init__(self, hook, clock, attempts=3, wait_s=5.0, out=None):
        self.hook = hook
        self.clock = clock
        self.attempts = attempts
        self.wait_s = wait_s
        self.out = out
        self.failures = []          # every failure message, for the run's log

    @property
    def log(self):
        return self.hook.log

    def __call__(self):
        for attempt in range(1, self.attempts + 1):
            try:
                return self.hook()
            except ResetFailed as error:
                self.failures.append(str(error))
                if "not in foreground" in str(error) or attempt == self.attempts:
                    raise
                if self.out is not None:
                    self.out(f"Reset failed (try {attempt} of {self.attempts}): {error}")
                self.clock.sleep_until(self.clock.now() + self.wait_s)
