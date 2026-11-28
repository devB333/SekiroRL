"""Fixed-rate step loop: action out, wait, state in. No Gymnasium or numpy here.

Order inside one step: apply the action at the step start, wait until the
deadline, then read. The returned observation therefore shows the consequence
of the action, and the delay from observation to next action is inference time.

Without an `encounter` the loop never sets `terminated`: episodes end by
truncation only (step limit, repeated invalid reads, or a long gap). With one
(see encounter.py), reset waits until the fight is really running and the
episode terminates when the player's HP reaches 0 or the boss has taken the
configured number of deathblows.
"""
import threading
import time

from . import actions
from .controller import NEUTRAL
from .snapshot import encode


class ResetFailed(RuntimeError):
    pass


def hp_reward(previous, current):
    """Normalised HP change between two consecutive valid snapshots.

    Player damage is negative. Boss damage is positive when boss HP is
    available in both; a boss HP increase (refill after a deathblow) is ignored.
    """
    reward = (current.player_hp - previous.player_hp) / current.player_max_hp
    if None not in (previous.boss_hp, current.boss_hp, current.boss_max_hp):
        dealt = previous.boss_hp - current.boss_hp
        if dealt > 0 and current.boss_max_hp > 0:
            reward += dealt / current.boss_max_hp
    return reward


def _percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(fraction * len(ordered)))]


class FightReward:
    """Reward between two consecutive valid snapshots, from HP and posture.

    Each part is a fraction of its bar times a weight:
    - player HP lost: negative (`player_hp`); HP gained (healing) is positive.
    - boss HP lost: positive (`boss_hp`).
    - boss posture damage: positive (`boss_posture`). Raw posture counts down,
      so a fall is damage. Posture the boss recovers is taken back at
      `boss_posture_recovery` times that rate.
    - player posture damage: negative (`player_posture`). Recovered posture is not
      given back.

    A deathblow shows as the boss's HP dropping to 1 (with his posture at or
    near 0), so it pays whatever was left of the HP bar. The refill after it
    pays nothing: HP going up never counts, and posture coming back by more
    than `refill` of the bar in one step is the refill, not recovery (in the
    user's recorded fights of 2026-11-27 the most he recovered in one step was
    1.1% of the bar; the refill is the whole bar, one sample after the HP).
    A change of a maximum pays nothing either (the boss's bars are rescaled
    while it wakes up).

    The user's calls on 2026-11-27: the boss's posture weighs twice his HP,
    because posture is what wins this fight most of the time; the player's
    posture counts, less than the player's HP (a quarter is the agent's
    number); and recovered boss posture is taken back at half, "because it's
    very important that you keep the posture bar up". Before that last change
    nothing was taken back, and letting him recover cost nothing.
    """

    def __init__(self, player_hp=1.0, boss_hp=1.0, boss_posture=2.0, player_posture=0.25,
                 boss_posture_recovery=0.5, refill=0.1):
        self.player_hp = player_hp
        self.boss_hp = boss_hp
        self.boss_posture = boss_posture
        self.player_posture = player_posture
        self.boss_posture_recovery = boss_posture_recovery
        self.refill = refill

    @staticmethod
    def _lost(previous, current, name, maximum_name):
        """Fraction of the bar lost, or 0.0 if unavailable or the maximum changed."""
        before, after = getattr(previous, name), getattr(current, name)
        maximum = getattr(current, maximum_name)
        if None in (before, after, maximum) or maximum <= 0:
            return 0.0
        if getattr(previous, maximum_name) != maximum:
            return 0.0
        return (before - after) / maximum

    def __call__(self, previous, current):
        reward = -self.player_hp * self._lost(previous, current, "player_hp", "player_max_hp")
        reward += self.boss_hp * max(0.0, self._lost(previous, current, "boss_hp", "boss_max_hp"))
        posture = self._lost(previous, current, "boss_posture", "boss_max_posture")
        if posture >= 0.0:
            reward += self.boss_posture * posture
        elif -posture <= self.refill:
            reward += self.boss_posture * self.boss_posture_recovery * posture
        reward -= self.player_posture * max(0.0, self._lost(previous, current, "player_posture",
                                                           "player_max_posture"))
        return reward


class Liveness:
    """Whether a snapshot is usable as a step and the game moved on since the
    last one. `begin(snapshot)` at the start of an episode, then
    `check(snapshot)` once per step: (valid, reason).

    Missing HP or a game that is not advancing is invalid. The frame token
    stands still in the pause menu; in-game time stands still in loading
    screens. Either one not moving means no step happened.
    """

    def begin(self, snapshot):
        self._igt = snapshot.igt_ms
        self._token = snapshot.frame_token

    def check(self, snapshot):
        if not snapshot.valid:
            return False, snapshot.reason or "player HP unavailable"
        reason = None
        last = self._igt
        if snapshot.igt_ms is not None:
            self._igt = snapshot.igt_ms
            if last is not None and snapshot.igt_ms <= last:
                reason = "in-game time not advancing"
        last = self._token
        if snapshot.frame_token is not None:
            self._token = snapshot.frame_token
            if reason is None and last is not None and snapshot.frame_token == last:
                reason = "game frame not advancing (paused)"
        return reason is None, reason


class _Watchdog:
    """Releases all inputs if no step arrives for `timeout_s` of wall-clock time.

    Covers the pause while the trainer does a gradient update, during which the
    game keeps running with whatever was last held.
    """

    def __init__(self, controller, timeout_s):
        self.controller = controller
        self.timeout_s = timeout_s
        self.fired = 0
        self._armed = False
        self._last = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="input-watchdog", daemon=True)
        self._thread.start()

    def pet(self):
        self._last = time.monotonic()
        self._armed = True

    def disarm(self):
        self._armed = False

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=1.0)

    def _run(self):
        while not self._stop.wait(self.timeout_s / 4):
            if self._armed and time.monotonic() - self._last > self.timeout_s:
                self._armed = False
                self.fired += 1
                try:
                    self.controller.neutral()
                except Exception:
                    pass


class StepLoop:
    def __init__(self, source, controller, clock, step_interval=0.1, tap_s=0.05,
                 repress_gap_s=0.034, max_steps=3000, max_invalid_steps=5, gap_factor=2.0,
                 reward_fn=hp_reward, focus_check=None, reset_hook=None, lock_keeper=None,
                 reset_timeout_s=30.0, reset_poll_s=0.1, watchdog_s=None, encounter=None):
        actions.validate_timing(step_interval, tap_s, repress_gap_s)
        self.source = source
        self.controller = controller
        self.clock = clock
        self.dt = step_interval
        self.tap_s = tap_s
        self.repress_gap_s = repress_gap_s
        self.max_steps = max_steps
        self.max_invalid_steps = max_invalid_steps
        self.gap_factor = gap_factor
        self.reward_fn = reward_fn
        # lock_keeper.begin() at every reset; lock_keeper(snapshot) before every
        # step says whether to tap lock-on in that step (see reset.LockKeeper).
        self.lock_keeper = lock_keeper
        self.focus_check = focus_check
        self.reset_hook = reset_hook
        self.reset_timeout_s = reset_timeout_s
        self.reset_poll_s = reset_poll_s
        self.encounter = encounter
        self._watchdog = _Watchdog(controller, watchdog_s) if watchdog_s else None
        self._live = Liveness()
        self._needs_reset = True
        self._held = NEUTRAL
        self._closed = False

    # -- helpers -----------------------------------------------------------

    def _apply(self, state):
        self.controller.apply(state)
        self._held = state

    def _release_all(self):
        """Best-effort neutral that never raises; used on every exit path."""
        if self._watchdog:
            self._watchdog.disarm()
        try:
            self.controller.neutral()
        except Exception:
            pass
        self._held = NEUTRAL

    def _summary(self):
        elapsed = self.clock.now() - self._episode_start
        return {
            "steps": self._steps,
            "invalid_steps": self._invalid_total,
            "overruns": self._overruns,
            "steps_per_second": self._steps / elapsed if elapsed > 0 else None,
            "wake_late_s": {"p50": _percentile(self._wake_late, 0.5),
                            "p99": _percentile(self._wake_late, 0.99),
                            "max": max(self._wake_late, default=None)},
            "apply_late_s": {"p50": _percentile(self._apply_late, 0.5),
                             "p99": _percentile(self._apply_late, 0.99),
                             "max": max(self._apply_late, default=None)},
            "watchdog_fired": self._watchdog.fired if self._watchdog else 0,
        }

    # -- API ---------------------------------------------------------------

    def reset(self):
        if self._closed:
            raise RuntimeError("Loop is closed")
        self._release_all()
        if self.reset_hook is not None:
            self.reset_hook()
        if self.encounter is not None:
            self.encounter.begin()
        give_up = self.clock.now() + self.reset_timeout_s
        while True:
            snapshot = self.source.read()
            if self.encounter is None:
                ready, reason = snapshot.valid, snapshot.reason
            else:
                ready, reason = self.encounter.ready(snapshot)
            if ready:
                break
            if self.clock.now() >= give_up:
                what = "No valid player HP" if self.encounter is None else "Fight not running"
                raise ResetFailed(f"{what} within {self.reset_timeout_s} s: {reason}")
            self.clock.sleep_until(self.clock.now() + self.reset_poll_s)
        if self.lock_keeper is not None:
            self.lock_keeper.begin()
        self._previous = snapshot
        self._live.begin(snapshot)
        self._deadline = None
        self._steps = 0
        self._invalid_run = 0
        self._invalid_total = 0
        self._overruns = 0
        self._wake_late = []
        self._apply_late = []
        self._episode_start = self.clock.now()
        self._needs_reset = False
        return encode(snapshot), {"valid": True, "reason": None,
                                  "player_hp": snapshot.player_hp,
                                  "player_max_hp": snapshot.player_max_hp}

    def step(self, action):
        if self._needs_reset:
            raise RuntimeError("Call reset() before step()")
        try:
            return self._step(int(action))
        except BaseException:
            self._release_all()
            self._needs_reset = True
            raise

    def _step(self, action):
        if not 0 <= action < actions.NUM_ACTIONS:
            raise ValueError(f"Action {action} outside 0..{actions.NUM_ACTIONS - 1}")
        now = self.clock.now()
        start, apply_late, overrun, gap = now, 0.0, False, False
        if self._deadline is not None:
            late = now - self._deadline
            if late > self.gap_factor * self.dt:
                gap = True                      # caller was away; the game was not
            elif late >= self.dt:
                overrun = True                  # resynchronise, never catch up
                self._overruns += 1
            else:
                start, apply_late = self._deadline, max(0.0, late)
        if self._watchdog:
            self._watchdog.pet()

        focused = self.focus_check() if self.focus_check is not None else True
        applied = []
        lock_pressed = False
        if gap or not focused:
            self._release_all()
            if self._watchdog and not gap:
                self._watchdog.pet()
        else:
            plan = actions.schedule(action, self._held, self.tap_s, self.repress_gap_s)
            if (self.lock_keeper is not None and self._previous is not None
                    and self.lock_keeper(self._previous)):
                plan = actions.with_tap(plan, "lock_on", self.tap_s)
                lock_pressed = True
            for offset, state in plan:
                self.clock.sleep_until(start + offset)
                self._apply(state)
                applied.append((self.clock.now() - start, state))

        deadline = start + self.dt
        self.clock.sleep_until(deadline)
        wake_late = self.clock.now() - deadline
        snapshot = self.source.read()
        read_s = self.clock.now() - deadline - wake_late
        self._deadline = deadline
        self._steps += 1
        self._wake_late.append(wake_late)
        self._apply_late.append(apply_late)

        valid, reason = self._live.check(snapshot)
        if valid and not focused:
            valid, reason = False, "game window not in foreground"
        if valid and gap:
            valid, reason = False, "gap between steps; inputs were released"

        reward = 0.0
        if valid and self._previous is not None:
            reward = float(self.reward_fn(self._previous, snapshot))
        # A delta is only ever taken between two consecutive valid samples.
        self._previous = snapshot if valid else None
        if valid:
            self._invalid_run = 0
        else:
            self._invalid_run += 1
            self._invalid_total += 1

        terminated, outcome, events = False, None, None
        if valid and self.encounter is not None:
            events = self.encounter.update(snapshot)
            if events["player_dead"]:       # checked first: a trade is a loss (D9)
                terminated, outcome = True, "death"
            elif events["victory"]:
                terminated, outcome = True, "victory"

        truncated, why = False, None
        if terminated:
            pass
        elif gap:
            truncated, why = True, "gap"
        elif self._invalid_run >= self.max_invalid_steps:
            truncated, why = True, "invalid_reads"
        elif self._steps >= self.max_steps:
            truncated, why = True, "step_limit"

        info = {
            "valid": valid,
            "reason": reason,
            "action": actions.ACTION_NAMES[action],
            "player_hp": snapshot.player_hp,
            "player_max_hp": snapshot.player_max_hp,
            "boss_hp": snapshot.boss_hp,
            "boss_max_hp": snapshot.boss_max_hp,
            "locked_on": snapshot.locked_on,
            "timing": {"apply_late_s": apply_late, "wake_late_s": wake_late,
                       "read_s": read_s, "overrun": overrun, "gap": gap},
            "applied": applied,
            "lock_pressed": lock_pressed,
        }
        if events is not None:
            info["encounter"] = events
        if terminated or truncated:
            self._release_all()
            self._needs_reset = True
            info["termination_reason" if terminated else "truncation_reason"] = outcome or why
            info["timing_summary"] = self._summary()
        return encode(snapshot), reward, terminated, truncated, info

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._needs_reset = True
        self._release_all()
        if self._watchdog:
            self._watchdog.stop()
        try:
            self.controller.close()
        except Exception:
            pass
