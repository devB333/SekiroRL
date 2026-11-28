"""Record the user's own play, and turn a recording into training pairs.

    python -m sekirorl.demos pads
        Shows which controllers are seen and what is pressed: XInput slots
        0 to 3, and a DualSense read over HID as slot 4.
    python -m sekirorl.demos record --pid <sekiro pid> --minutes 30
        Records the pad and the game state until the time is up, Ctrl+C, or
        the game exits. Nothing is sent to the game; memory access is
        query/read only. Start Sekiro with the controller ON for this.
    python -m sekirorl.demos report demos/<file>.jsonl ...
        Cuts a recording into fights and steps, labels every step with the
        nearest action of the agent, and says how much of the play the action
        set could not express.

A recording keeps raw values (pad state on every change, a `Snapshot` about
60 times a second), not observations or actions, so it can be re-read after
the observation layout, the action set or the step rate changes. Recordings
go under `demos/` (ignored by git).

Offline, `fights()` walks a recording the way `loop.StepLoop` walks the live
game: the same start rule (`Encounter.ready`), the same validity check, the
same endings, one step every `dt`. The label of a step is what the user did
with the pad from that step's start to the next one, so the observation always
comes before the input it is paired with.
"""
import argparse
import bisect
import dataclasses
from dataclasses import dataclass, field
import json
import math
import os
import sys
import time

from . import actions
from .encounter import Encounter
from .loop import FightReward, Liveness
from .pad import MASKS, SLOTS, TRIGGERS, PadState, mask
from .snapshot import SCHEMA_VERSION, Snapshot, encode

PAD_FIELDS = tuple(f.name for f in dataclasses.fields(PadState))
SNAPSHOT_FIELDS = frozenset(f.name for f in dataclasses.fields(Snapshot))
VECTOR_FIELDS = ("player_pos", "boss_pos")


# -- recording ---------------------------------------------------------------

class Recorder:
    """Polls the pads `pad_hz` times a second and writes a row whenever a
    pad's state changed; reads a snapshot `snapshot_hz` times a second.

    `pads.read(slot)` returns (packet, PadState) or None. Empty slots are
    polled only once a second: asking XInput about a slot with no pad is slow.
    Row times are seconds since the recording started, on `clock`. A snapshot
    row is stamped after its read, so what it shows is never newer than its time.
    """

    def __init__(self, source, pads, clock, write, pad_hz=125.0, snapshot_hz=62.5,
                 slots=range(SLOTS), empty_poll_s=1.0):
        self.source = source
        self.pads = pads
        self.clock = clock
        self.write = write
        self.pad_hz = pad_hz
        self.snapshot_hz = snapshot_hz
        self.slots = tuple(slots)
        self.empty_poll_s = empty_poll_s
        self.ticks = 0
        self.late_ticks = 0         # pad polls that were skipped because the loop fell behind
        self.snapshots = 0
        self.missed_packets = 0     # pad states that came and went between two polls

    def _poll(self, slot, t):
        seen = self.pads.read(slot)
        last = self._last[slot]
        if seen is None:
            self._retry[slot] = t + self.empty_poll_s
            if last is not None:
                self._last[slot] = None
                self.write({"kind": "pad", "t": round(t, 5), "slot": slot, "connected": False})
            return
        packet, state = seen
        if last is not None:
            if last[1] == state:
                return
            self.missed_packets += max(0, ((packet - last[0]) & 0xFFFFFFFF) - 1)
        self._last[slot] = seen
        row = {"kind": "pad", "t": round(t, 5), "slot": slot, "packet": packet}
        row.update(dataclasses.asdict(state))
        self.write(row)

    def run(self, stop):
        """Record until `stop(seconds since the start)` returns a reason; returns it."""
        self._last = {slot: None for slot in self.slots}
        self._retry = {slot: 0.0 for slot in self.slots}
        start = self.clock.now()
        next_snapshot = 0.0
        tick = 0
        while True:
            self.clock.sleep_until(start + tick / self.pad_hz)
            t = self.clock.now() - start
            why = stop(t)
            if why:
                break
            for slot in self.slots:
                if self._last[slot] is not None or t >= self._retry[slot]:
                    self._poll(slot, t)
            if t >= next_snapshot:
                snapshot = self.source.read()
                self.write(snapshot_row(snapshot, self.clock.now() - start))
                self.snapshots += 1
                next_snapshot += 1.0 / self.snapshot_hz
                if next_snapshot < t:               # fell behind: do not catch up
                    next_snapshot = t + 1.0 / self.snapshot_hz
            self.ticks += 1
            tick += 1
            behind = int((self.clock.now() - start) * self.pad_hz) - tick
            if behind > 0:
                self.late_ticks += behind
                tick += behind
        self.write({"kind": "end", "t": round(t, 5), "why": why, "ticks": self.ticks,
                    "late_ticks": self.late_ticks, "snapshots": self.snapshots,
                    "missed_packets": self.missed_packets})
        return why


def snapshot_row(snapshot, t):
    """One log row for a snapshot. Unavailable fields are left out."""
    row = {"kind": "snapshot", "t": round(t, 5)}
    for name, value in dataclasses.asdict(snapshot).items():
        if value is not None:
            row[name] = value
    return row


def snapshot_from_row(row):
    values = {name: value for name, value in row.items() if name in SNAPSHOT_FIELDS}
    for name in VECTOR_FIELDS:
        if values.get(name) is not None:
            values[name] = tuple(values[name])
    values.setdefault("t_ns", 0)
    return Snapshot(**values)


# -- reading a recording -------------------------------------------------------

class PadTrack:
    """One pad over time, from its change rows: [(t, PadState or None), ...]
    in time order. None means no pad was connected from then on."""

    def __init__(self, changes):
        self.times = [t for t, _ in changes]
        self.states = [state for _, state in changes]
        self._presses = {}
        self._releases = {}

    def at(self, t):
        """The state in force at `t`; None before the first row or while disconnected."""
        index = bisect.bisect_right(self.times, t) - 1
        return self.states[index] if index >= 0 else None

    def _edges(self, name):
        if name not in self._presses:
            presses, releases, down = [], [], False
            for t, state in zip(self.times, self.states):
                now = state is not None and state.pressed(name)
                if now != down:
                    (presses if now else releases).append(t)
                down = now
            self._presses[name], self._releases[name] = presses, releases
        return self._presses[name], self._releases[name]

    def presses(self, name, t0, t1):
        """Times in [t0, t1) at which `name` went down."""
        times = self._edges(name)[0]
        return times[bisect.bisect_left(times, t0):bisect.bisect_left(times, t1)]

    def hold(self, name, t):
        """(pressed at, released at) of the press of `name` that is down at
        `t`, or None if it is up. A press still down at the end of the track
        has released at = infinity."""
        presses, releases = self._edges(name)
        index = bisect.bisect_right(presses, t) - 1
        if index < 0:
            return None
        after = bisect.bisect_right(releases, presses[index])
        released = releases[after] if after < len(releases) else math.inf
        return (presses[index], released) if released > t else None

    def held_s(self, name, t0, t1):
        """Seconds within [t0, t1) that `name` was down."""
        presses, releases = self._edges(name)
        total = 0.0
        index = max(0, bisect.bisect_right(presses, t0) - 1)
        while index < len(presses) and presses[index] < t1:
            after = bisect.bisect_right(releases, presses[index])
            released = releases[after] if after < len(releases) else math.inf
            total += max(0.0, min(released, t1) - max(presses[index], t0))
            index += 1
        return total

    def mean_stick(self, t0, t1):
        """Time-weighted mean of the left stick over [t0, t1), as (x, y) in -1..1."""
        x = y = 0.0
        index = max(0, bisect.bisect_right(self.times, t0) - 1)
        while index < len(self.times) and self.times[index] < t1:
            end = self.times[index + 1] if index + 1 < len(self.times) else math.inf
            seconds = min(end, t1) - max(self.times[index], t0)
            state = self.states[index]
            if seconds > 0 and state is not None:
                sx, sy = state.left_stick()
                x += sx * seconds
                y += sy * seconds
            index += 1
        return x / (t1 - t0), y / (t1 - t0)

    def press_count(self):
        """All button presses on the track; the busiest pad is the user's."""
        return sum(len(self._edges(name)[0]) for name in MASKS)


@dataclass
class Recording:
    meta: dict
    pads: dict              # slot -> PadTrack
    snapshots: list         # [(t, Snapshot), ...] in time order
    end: dict = None        # the recorder's last row; None if it was cut off

    def track(self, slot=None):
        """The pad to label from: `slot`, or the one with the most presses."""
        if slot is not None:
            return self.pads[slot]
        if not self.pads:
            raise ValueError("The recording has no controller rows")
        return max(self.pads.values(), key=PadTrack.press_count)


def load(path):
    """Read a recording. A last line cut short (the recorder was killed) is dropped."""
    meta, end, changes, snapshots = {}, None, {}, []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            kind = row.get("kind")
            if kind == "pad":
                state = None
                if row.get("connected", True):
                    state = PadState(**{name: row[name] for name in PAD_FIELDS})
                changes.setdefault(row["slot"], []).append((row["t"], state))
            elif kind == "snapshot":
                snapshots.append((row["t"], snapshot_from_row(row)))
            elif kind == "meta":
                meta = row
            elif kind == "end":
                end = row
    return Recording(meta, {slot: PadTrack(rows) for slot, rows in changes.items()},
                     snapshots, end)


# -- labelling -------------------------------------------------------------------

class Labeller:
    """The agent's action nearest to what the pad did in one step.

    `label(track, t0, t1)` returns (action index, flags). The flags name what
    the action leaves out; an empty set means the step is expressed in full.

    Buttons, from the action set's own table (`actions.BUTTON_MODES`):
    - A press inside the step decides the step; the earliest one if there are
      several (flag `lost_press`). A press of a button with a "repress" mode
      (guard) is that mode: a fresh press is what a deflect is. A button with
      both a tap and a hold mode is the hold if it stays down for
      `long_hold_s` or more, else the tap.
    - With no press, a button with a hold mode that is down for half the step
      or more is that mode. Guard counts however long it has been down; a
      button that also has a tap mode only once it is a long hold, so the tail
      of a tap is not read as a hold.
    - A long hold of a button with no hold mode is flagged `held_<button>`
      (for example a charged attack).
    - A mode with several buttons (combat art: guard and attack) is the label
      when a press inside the step leaves all of its buttons down, whichever
      of them went down first and however long ago.
    - `attack_with_guard_held`: only for an action set without such a mode:
      attack pressed while guard is down, which the game may read as a combat
      art and the agent could then not do.
    - `outside`: a press of anything the action set has no use for, except
      lock-on, Start and Back.

    Movement: the mean left stick over the step. Inside `deadzone` is "stay";
    otherwise the nearest of the four directions, flagged `diagonal` when the
    stick is more than `diagonal_deg` off it.
    """

    IGNORED = ("lock_on", "pad_start", "pad_back")

    def __init__(self, modes=actions.BUTTON_MODES, movements=actions.MOVEMENTS,
                 deadzone=0.35, diagonal_deg=22.5, long_hold_s=0.3):
        self.modes = tuple(modes)
        self.movements = tuple(movements)
        self.deadzone = deadzone
        self.diagonal_deg = diagonal_deg
        self.long_hold_s = long_hold_s
        self.none = next(index for index, (_, button, _) in enumerate(self.modes)
                         if button is None)
        self.by_kind = {}           # button -> {kind: mode index}
        self.combos = []            # (buttons, mode index) of the modes with several buttons
        for index, (_, button, kind) in enumerate(self.modes):
            if isinstance(button, str):
                self.by_kind.setdefault(button, {})[kind] = index
            elif button is not None:
                self.combos.append((tuple(button), index))
        self.guarded_attack = any(set(buttons) == {"guard", "attack"}
                                  for buttons, _ in self.combos)
        used = {mask(button) for button in self.by_kind} | {mask(name) for name in self.IGNORED}
        self.outside = tuple(name for name, bit in MASKS.items() if bit not in used) + TRIGGERS
        self.stay = next(index for index, (_, stick) in enumerate(self.movements)
                         if stick == (0.0, 0.0))

    def movement(self, track, t0, t1):
        x, y = track.mean_stick(t0, t1)
        if math.hypot(x, y) < self.deadzone:
            return self.stay, set()
        best, off = None, None
        for index, (_, (mx, my)) in enumerate(self.movements):
            if (mx, my) == (0.0, 0.0):
                continue
            angle = abs(math.degrees(math.atan2(x * my - y * mx, x * mx + y * my)))
            if off is None or angle < off:
                best, off = index, angle
        return best, ({"diagonal"} if off > self.diagonal_deg else set())

    def _long(self, track, button, t):
        hold = track.hold(button, t)
        return hold is not None and hold[1] - hold[0] >= self.long_hold_s

    def buttons(self, track, t0, t1):
        flags = set()
        if any(track.presses(name, t0, t1) for name in self.outside):
            flags.add("outside")
        presses = sorted((t, button) for button in self.by_kind
                         for t in track.presses(button, t0, t1))
        for buttons, mode in self.combos:
            for t, button in presses:
                state = track.at(t)
                if button in buttons and all(state.pressed(name) for name in buttons):
                    if any(other not in buttons for _, other in presses):
                        flags.add("lost_press")
                    return mode, flags
        if presses:
            t, button = presses[0]
            kinds = self.by_kind[button]
            if len(presses) > 1:
                flags.add("lost_press")
            if "repress" in kinds:
                mode = kinds["repress"]
            elif "tap" in kinds and "hold" in kinds:
                mode = kinds["hold" if self._long(track, button, t) else "tap"]
            elif "tap" in kinds:
                mode = kinds["tap"]
                if self._long(track, button, t):
                    flags.add(f"held_{button}")
            else:
                mode = kinds["hold"]
            state = track.at(t)
            if (button == "attack" and not self.guarded_attack and "guard" in self.by_kind
                    and state.pressed("guard")):
                flags.add("attack_with_guard_held")
            return mode, flags

        mode = None
        for button, kinds in self.by_kind.items():
            if track.held_s(button, t0, t1) < (t1 - t0) / 2:
                continue
            long = self._long(track, button, t0)    # no press in the step: down since before it
            if "hold" in kinds and (long or "tap" not in kinds):
                if mode is None:
                    mode = kinds["hold"]
                else:
                    flags.add("two_held")
            elif "hold" not in kinds and long:
                flags.add(f"held_{button}")
        return (self.none if mode is None else mode), flags

    def label(self, track, t0, t1):
        move, flags = self.movement(track, t0, t1)
        mode, more = self.buttons(track, t0, t1)
        # Same order as actions.ACTIONS: every movement for each button mode in turn.
        return mode * len(self.movements) + move, frozenset(flags | more)


# -- fights and steps ------------------------------------------------------------

@dataclass
class DemoStep:
    t: float                # step start, seconds into the recording
    snapshot: Snapshot      # what the agent would have seen at the step start
    action: int             # label of what the user did during the step
    flags: frozenset
    reward: float           # of the step, by the same rule as live
    valid: bool             # the read at the end of the step was valid
    usable: bool            # `snapshot` was a valid read, so (snapshot, action) can be trained on


@dataclass
class DemoFight:
    start_t: float
    steps: list = field(default_factory=list)
    ending: str = None      # death, victory, invalid_reads, or recording_ended
    deathblows: int = 0


def fights(recording, labeller=None, slot=None, dt=0.1, phase=0.0, deathblows_to_win=2,
           reward_fn=None, max_invalid_steps=5, max_stale_s=0.05):
    """Cut a recording into fights of steps, as the live loop would have.

    `phase` shifts every step start, in seconds; steps of another phase are
    another view of the same play. A step whose start has no recorded sample
    within `max_stale_s` before it is treated as an unavailable read.

    After a fight ends, the next one can only start once the player or the
    boss has been unreadable (the loading screen back into the rematch).
    Without that, the boss's refilled bar after a deathblow that ended a
    fight would look like a new fight.
    """
    labeller = labeller or Labeller()
    reward_fn = reward_fn or FightReward()
    track = recording.track(slot)
    times = [t for t, _ in recording.snapshots]
    if not times:
        return []

    def read(t):
        index = bisect.bisect_right(times, t + 1e-9) - 1
        if index < 0 or t - times[index] > max_stale_s:
            return Snapshot(t_ns=0, reason="no recorded sample")
        return recording.snapshots[index][1]

    encounter = Encounter(deathblows_to_win=deathblows_to_win)
    live = Liveness()
    out, fight = [], None
    reloaded = True
    tick = 0
    while True:
        t = times[0] + phase + tick * dt
        tick += 1
        if t > times[-1]:
            break
        if fight is None:
            snapshot = read(t)
            if not snapshot.valid or snapshot.boss_hp is None:
                reloaded = True
            if not (encounter.ready(snapshot)[0] and reloaded):
                continue
            fight = DemoFight(start_t=t)
            live.begin(snapshot)
            current, previous, invalid_run = snapshot, snapshot, 0
            continue
        # `t` is now the END of a step that started at t - dt.
        action, flags = labeller.label(track, t - dt, t)
        snapshot = read(t)
        valid, _ = live.check(snapshot)
        reward = float(reward_fn(previous, snapshot)) if valid and previous is not None else 0.0
        fight.steps.append(DemoStep(t - dt, current, action, flags, reward, valid,
                                    usable=current.valid))
        current, previous = snapshot, (snapshot if valid else None)
        invalid_run = 0 if valid else invalid_run + 1
        if valid:
            events = encounter.update(snapshot)
            fight.deathblows = events["deathblows"]
            if events["player_dead"]:
                fight.ending = "death"
            elif events["victory"]:
                fight.ending = "victory"
        if fight.ending is None and invalid_run >= max_invalid_steps:
            fight.ending = "invalid_reads"
        if fight.ending is not None:
            out.append(fight)
            fight = None
            reloaded = False
            encounter.begin()
    if fight is not None:
        fight.ending = "recording_ended"
        out.append(fight)
    return out


def pairs(demo_fights):
    """(observations, actions) of every usable step, for supervised training."""
    observations, labels = [], []
    for fight in demo_fights:
        for step in fight.steps:
            if step.usable:
                observations.append(encode(step.snapshot))
                labels.append(step.action)
    return observations, labels


def summarise(recording, demo_fights, labeller=None, slot=None):
    """Counts for the report: fights, labels, and what the labels left out."""
    labeller = labeller or Labeller()
    track = recording.track(slot)
    steps = [step for fight in demo_fights for step in fight.steps]
    flags, modes, moves = {}, {}, {}
    for step in steps:
        for flag in step.flags:
            flags[flag] = flags.get(flag, 0) + 1
        mode, move = divmod(step.action, len(labeller.movements))
        modes[labeller.modes[mode][0]] = modes.get(labeller.modes[mode][0], 0) + 1
        moves[labeller.movements[move][0]] = moves.get(labeller.movements[move][0], 0) + 1
    unlocked = sum(1 for step in steps if step.snapshot.locked_on is False)
    rows = []
    for fight in demo_fights:
        last = next((step.snapshot for step in reversed(fight.steps)
                     if step.snapshot.boss_hp is not None and step.snapshot.boss_max_hp), None)
        rows.append({"start_t": round(fight.start_t, 1), "steps": len(fight.steps),
                     "ending": fight.ending, "deathblows": fight.deathblows,
                     "reward": round(sum(step.reward for step in fight.steps), 3),
                     "boss_hp_left": (round(last.boss_hp / last.boss_max_hp, 3)
                                      if last is not None else None)})
    return {"fights": rows, "steps": len(steps),
            "usable": sum(1 for step in steps if step.usable),
            "flagged": sum(1 for step in steps if step.flags), "flags": flags,
            "modes": modes, "moves": moves, "unlocked": unlocked,
            "pad_presses": track.press_count()}


def print_report(path, out=print):
    recording = load(path)
    out(f"{path}")
    if not recording.pads:
        out("  No controller rows: no pad was seen through XInput while recording.")
        return None
    seconds = recording.snapshots[-1][0] if recording.snapshots else 0.0
    end = recording.end
    out(f"  {seconds / 60:.1f} minutes, {len(recording.snapshots)} snapshots, pads in slots "
        f"{sorted(recording.pads)}; " + ("ended: " + end["why"] + f", late polls {end['late_ticks']}"
                                         f", pad states missed {end['missed_packets']}"
                                         if end else "cut off before its end row"))
    demo_fights = fights(recording)
    summary = summarise(recording, demo_fights)
    for number, row in enumerate(summary["fights"], 1):
        out(f"  fight {number}: {row['steps'] / 10:.1f} s, {row['ending']}, deathblows "
            f"{row['deathblows']}, boss HP left {row['boss_hp_left']}, reward {row['reward']:+.3f}")
    steps = summary["steps"]
    if not steps:
        out("  No fight found in it.")
        return summary

    def share(count):
        return f"{count} ({100.0 * count / steps:.1f}%)"

    out(f"  {steps} steps, {share(summary['usable'])} usable for training, "
        f"{share(summary['unlocked'])} not locked on the boss")
    out("  buttons: " + ", ".join(f"{name} {share(count)}" for name, count
                                  in sorted(summary["modes"].items(), key=lambda item: -item[1])))
    out("  movement: " + ", ".join(f"{name} {share(count)}" for name, count
                                   in sorted(summary["moves"].items(), key=lambda item: -item[1])))
    out(f"  steps the action set does not fully express: {share(summary['flagged'])}")
    for name, count in sorted(summary["flags"].items(), key=lambda item: -item[1]):
        out(f"    {name}: {share(count)}")
    return summary


# -- command line ----------------------------------------------------------------

def show_pads(pads, seconds, out=print, sleep=time.sleep, poll_s=0.02, slots=range(SLOTS)):
    """Print which slots have a pad, then every change of its buttons or of
    the direction of its left stick for `seconds`."""
    last = {}
    waited = 0.0
    first = True
    while waited <= seconds:
        for slot in slots:
            seen = pads.read(slot)
            shown = None
            if seen is not None:
                down = [name for name in MASKS if seen[1].pressed(name)]
                down += [name for name in TRIGGERS if seen[1].pressed(name)]
                x, y = seen[1].left_stick()
                shown = (tuple(down), round(x * 2) / 2, round(y * 2) / 2)
            if first or shown != last.get(slot):
                if shown is None:
                    if first or last.get(slot) is not None:
                        out(f"slot {slot}: no controller")
                else:
                    out(f"slot {slot}: buttons {list(shown[0]) or 'none'}, "
                        f"left stick ({shown[1]:+.1f}, {shown[2]:+.1f})")
            last[slot] = shown
        first = False
        sleep(poll_s)
        waited += poll_s
    return [slot for slot, state in last.items() if state is not None]


def default_path():
    return os.path.join("demos", time.strftime("demo-%Y%m%d-%H%M%S.jsonl"))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    pads_parser = commands.add_parser("pads", help="show the connected controllers")
    pads_parser.add_argument("--seconds", type=float, default=10.0)
    record = commands.add_parser("record", help="record the pad and the game state")
    record.add_argument("--pid", required=True, type=int)
    record.add_argument("--minutes", type=float, default=30.0)
    record.add_argument("--out", default=None, help="file to write (default under demos/)")
    report = commands.add_parser("report", help="fights and labels found in recordings")
    report.add_argument("paths", nargs="+")
    args = parser.parse_args()

    if args.command == "report":
        for path in args.paths:
            print_report(path)
        return
    from .pad import DUALSENSE_SLOT, Pads
    pads = Pads()
    time.sleep(0.5)             # the DualSense reader needs a moment to find the pad
    if args.command == "pads":
        print(f"Press buttons on your controller; watching for {args.seconds:.0f} s. "
              f"Slot {DUALSENSE_SLOT} is a DualSense read over HID.")
        found = show_pads(pads, args.seconds, slots=pads.slots)
        print(f"Controllers seen in slots: {found or 'none'}")
        return

    from . import memory
    from .clock import RealClock
    from .runner import describe
    try:
        reader = memory.Reader(args.pid)
    except OSError as error:
        parser.exit(1, f"Cannot open process {args.pid}: {error}\n")
    path = args.out or default_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    log = open(path, "w", encoding="utf-8")
    source = memory.MemoryStateSource(reader, reader.module_base(), boss=memory.GENICHIRO)
    seen = {"text": None, "checked": 0.0, "written": 0}

    def write(row):
        log.write(json.dumps(row) + "\n")
        seen["written"] += 1
        if seen["written"] % 100 == 0:
            log.flush()
        if row["kind"] == "snapshot":
            text = describe(snapshot_from_row(row))
            if text != seen["text"]:
                seen["text"] = text
                print(f"[{row['t']:7.1f} s] {text}", flush=True)

    def stop(t):
        if t >= args.minutes * 60:
            return "time is up"
        if t - seen["checked"] >= 1.0:
            seen["checked"] = t
            if not reader.running():
                return "the game exited"
        return None

    write({"kind": "meta", "time": time.time(), "pid": args.pid, "schema": SCHEMA_VERSION,
           "python": sys.version.split()[0], "args": vars(args)})
    recorder = Recorder(source, pads, RealClock(), write, slots=pads.slots)
    connected = [slot for slot in pads.slots if pads.read(slot) is not None]
    print(f"Recording to {path} for up to {args.minutes:.0f} minutes. Nothing is sent to the game.")
    print(f"Controllers seen in slots: {connected or 'NONE'}", flush=True)
    why = "interrupted"
    try:
        why = recorder.run(stop)
    except KeyboardInterrupt:
        pass
    finally:
        log.close()
        reader.close()
    print(f"Stopped: {why}. {recorder.snapshots} snapshots, {recorder.late_ticks} late polls.")
    print_report(path)


if __name__ == "__main__":
    main()
