"""Simulated game for automated tests: state source, controller and clock in one.

Time only moves when the loop sleeps (or a test calls `advance`), so tests are
deterministic and instant.
"""
from .controller import NEUTRAL
from .snapshot import Snapshot


class FakeGame:
    def __init__(self, max_hp=320, hits=(), unavailable=(), paused=(), with_igt=True):
        """
        hits: (time_s, damage) pairs; a hit does nothing while guard is held.
        unavailable: (start_s, end_s) windows where reads fail, as in a loading screen.
        paused: (start_s, end_s) windows where in-game time stands still.
        """
        self.max_hp = max_hp
        self.hp = max_hp
        self.hits = sorted(hits)
        self.unavailable = tuple(unavailable)
        self.paused = tuple(paused)
        self.with_igt = with_igt
        self.t = 0.0
        self.state = NEUTRAL
        self.log = []           # (time_s, ControlState)
        self.closed = False
        self.reads = 0
        self._hit_index = 0
        self._igt = 0.0

    # -- clock -------------------------------------------------------------

    def now(self):
        return self.t

    def sleep_until(self, deadline):
        if deadline <= self.t:
            return
        while self._hit_index < len(self.hits) and self.hits[self._hit_index][0] <= deadline:
            _, damage = self.hits[self._hit_index]
            self._hit_index += 1
            if "guard" not in self.state.buttons:
                self.hp = max(0, self.hp - damage)
        if not self._in(self.paused, self.t):
            self._igt += deadline - self.t
        self.t = deadline

    def advance(self, seconds):
        """Simulate the caller being busy (for example a policy update)."""
        self.sleep_until(self.t + seconds)

    # -- controller --------------------------------------------------------

    def apply(self, state):
        self.state = state
        self.log.append((self.t, state))

    def neutral(self):
        self.apply(NEUTRAL)

    def close(self):
        self.neutral()
        self.closed = True

    def press_times(self, button):
        """Times of rising edges for one button."""
        times, held = [], False
        for t, state in self.log:
            down = button in state.buttons
            if down and not held:
                times.append(t)
            held = down
        return times

    # -- state source ------------------------------------------------------

    @staticmethod
    def _in(windows, t):
        return any(start <= t < end for start, end in windows)

    def read(self):
        self.reads += 1
        t_ns = int(self.t * 1e9)
        if self._in(self.unavailable, self.t):
            return Snapshot(t_ns=t_ns, reason="Invalid/null pointer (simulated)")
        return Snapshot(t_ns=t_ns, player_hp=self.hp, player_max_hp=self.max_hp,
                        igt_ms=int(self._igt * 1000) if self.with_igt else None)

    def restart(self):
        """Stand-in for a supervised reset: back to full health."""
        self.hp = self.max_hp
