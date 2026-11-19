"""Clock interface so the step loop can run in real or simulated time."""
import time


class RealClock:
    """Monotonic seconds. Sleeps most of the way, then spins for the last part.

    `time.sleep` granularity on Windows depends on the Python version and
    system timer, so the loop measures lateness instead of trusting it.
    """

    def __init__(self, spin_s=0.002):
        self.spin_s = spin_s

    def now(self):
        return time.perf_counter()

    def sleep_until(self, deadline):
        remaining = deadline - time.perf_counter()
        if remaining > self.spin_s:
            time.sleep(remaining - self.spin_s)
        while time.perf_counter() < deadline:
            pass
