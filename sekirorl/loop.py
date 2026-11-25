"""Fixed-rate step loop: action out, wait, state in. No Gymnasium or numpy here.

Order inside one step: apply the action at the step start, wait until the
deadline, then read. The returned observation therefore shows the consequence
of the action, and the delay from observation to next action is inference time.

This milestone never sets `terminated`: player death has not yet been observed
as HP 0 in the live game and there is no verified victory signal, so episodes
end by truncation only (step limit, repeated invalid reads, or a long gap).
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
                 reward_fn=hp_reward, focus_check=None, reset_hook=None,
                 reset_timeout_s=30.0, reset_poll_s=0.1, watchdog_s=None):
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
        self.focus_check = focus_check
        self.reset_hook = reset_hook
        self.reset_timeout_s = reset_timeout_s
        self.reset_poll_s = reset_poll_s
        self._watchdog = _Watchdog(controller, watchdog_s) if watchdog_s else None
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

    def _check(self, snapshot):
        """Return (valid, reason). Missing HP or a game that is not advancing is invalid.

        The frame token stands still in the pause menu; in-game time stands
        still in loading screens. Either one not moving means no step happened.
        """
        if not snapshot.valid:
            return False, snapshot.reason or "player HP unavailable"
        reason = None
        last = self._last_igt
        if snapshot.igt_ms is not None:
            self._last_igt = snapshot.igt_ms
            if last is not None and snapshot.igt_ms <= last:
                reason = "in-game time not advancing"
        last = self._last_token
        if snapshot.frame_token is not None:
            self._last_token = snapshot.frame_token
            if reason is None and last is not None and snapshot.frame_token == last:
                reason = "game frame not advancing (paused)"
        return reason is None, reason

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
        give_up = self.clock.now() + self.reset_timeout_s
        while True:
            snapshot = self.source.read()
            if snapshot.valid:
                break
            if self.clock.now() >= give_up:
                raise ResetFailed(f"No valid player HP within {self.reset_timeout_s} s: "
                                  f"{snapshot.reason}")
            self.clock.sleep_until(self.clock.now() + self.reset_poll_s)
        self._previous = snapshot
        self._last_igt = snapshot.igt_ms
        self._last_token = snapshot.frame_token
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
        if gap or not focused:
            self._release_all()
            if self._watchdog and not gap:
                self._watchdog.pet()
        else:
            plan = actions.schedule(action, self._held, self.tap_s, self.repress_gap_s)
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

        valid, reason = self._check(snapshot)
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

        truncated, why = False, None
        if gap:
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
            "timing": {"apply_late_s": apply_late, "wake_late_s": wake_late,
                       "read_s": read_s, "overrun": overrun, "gap": gap},
            "applied": applied,
        }
        if truncated:
            self._release_all()
            self._needs_reset = True
            info["truncation_reason"] = why
            info["timing_summary"] = self._summary()
        return encode(snapshot), reward, False, truncated, info

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
