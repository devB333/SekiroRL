"""Recording the user's play and labelling it: pad tracks, the labeller, the
recorder and the offline cut into fights. Standard library only; the pad and
the game are simulated."""
import json
import os
import random
import tempfile
import unittest

from sekirorl import actions, demos
from sekirorl.actions import action_index as A
from sekirorl.controller import NEUTRAL, ControlState
from sekirorl.demos import Labeller, PadTrack, Recorder, Recording
from sekirorl.fake import FakeGame
from sekirorl.loop import StepLoop
from sekirorl.pad import PadState, dualsense_state, mask
from sekirorl.snapshot import OBS_SIZE, Snapshot

# The action set before sprint, heal, thrust and combat art were added.
OLD_MODES = actions.BUTTON_MODES[:6]


def pad(*buttons, stick=(0.0, 0.0), **raw):
    state = PadState.from_control(ControlState(buttons=frozenset(buttons), stick=stick))
    return PadState(**dict(state.__dict__, **raw))


def track(*changes):
    """PadTrack from (t, PadState) pairs, starting with nothing pressed at 0."""
    return PadTrack([(0.0, PadState())] + list(changes))


class PadTrackTests(unittest.TestCase):
    def setUp(self):
        self.track = track((1.0, pad("guard")), (1.25, pad("guard", "attack")),
                           (1.3, pad("guard")), (2.0, pad()), (3.0, pad("guard")))

    def test_state_in_force(self):
        self.assertIsNone(PadTrack([(1.0, pad())]).at(0.5))
        self.assertFalse(self.track.at(0.99).pressed("guard"))
        self.assertTrue(self.track.at(1.0).pressed("guard"))
        self.assertTrue(self.track.at(1.27).pressed("attack"))

    def test_presses_are_half_open(self):
        self.assertEqual(self.track.presses("guard", 0.0, 1.0), [])
        self.assertEqual(self.track.presses("guard", 1.0, 1.1), [1.0])
        self.assertEqual(self.track.presses("guard", 0.0, 5.0), [1.0, 3.0])
        self.assertEqual(self.track.presses("attack", 1.2, 1.3), [1.25])

    def test_hold_and_held_time(self):
        self.assertEqual(self.track.hold("guard", 1.5), (1.0, 2.0))
        self.assertIsNone(self.track.hold("guard", 2.0))
        self.assertEqual(self.track.hold("guard", 3.5), (3.0, float("inf")))
        self.assertAlmostEqual(self.track.held_s("guard", 0.5, 2.5), 1.0)
        self.assertAlmostEqual(self.track.held_s("guard", 1.9, 3.1), 0.2)
        self.assertEqual(self.track.held_s("attack", 0.0, 1.0), 0.0)

    def test_mean_stick_is_weighted_by_time(self):
        moving = track((1.0, pad(stick=(0.0, 1.0))), (1.05, pad(stick=(1.0, 0.0))))
        x, y = moving.mean_stick(1.0, 1.1)
        self.assertAlmostEqual(x, 0.5, places=3)
        self.assertAlmostEqual(y, 0.5, places=3)
        self.assertEqual(moving.mean_stick(0.0, 1.0), (0.0, 0.0))

    def test_a_disconnected_pad_presses_nothing(self):
        gone = PadTrack([(0.0, pad("guard")), (1.0, None), (2.0, pad("guard"))])
        self.assertIsNone(gone.at(1.5))
        self.assertEqual(gone.hold("guard", 0.5), (0.0, 1.0))
        self.assertEqual(gone.presses("guard", 0.0, 3.0), [0.0, 2.0])


class LabellerTests(unittest.TestCase):
    def setUp(self):
        self.labeller = Labeller()
        self.old = Labeller(modes=OLD_MODES)

    def label(self, pad_track, t0, labeller=None):
        return (labeller or self.labeller).label(pad_track, t0, t0 + 0.1)

    def test_index_matches_the_action_table(self):
        moving = track((1.0, pad("attack", stick=(0.0, 1.0))), (1.08, pad(stick=(0.0, 1.0))))
        self.assertEqual(self.label(moving, 1.0), (A("forward+attack"), frozenset()))
        for index, (move, mode) in enumerate(actions.ACTIONS):
            self.assertEqual(mode * len(actions.MOVEMENTS) + move, index)

    def test_nothing_pressed_is_the_noop(self):
        self.assertEqual(self.label(track(), 1.0), (actions.NOOP, frozenset()))

    def test_a_guard_press_is_a_deflect_and_then_a_hold(self):
        guarding = track((1.02, pad("guard")), (1.36, pad()))
        self.assertEqual(self.label(guarding, 1.0)[0], A("stay+deflect"))
        self.assertEqual(self.label(guarding, 1.1)[0], A("stay+guard_hold"))
        self.assertEqual(self.label(guarding, 1.2)[0], A("stay+guard_hold"))
        self.assertEqual(self.label(guarding, 1.3)[0], A("stay+guard_hold"))     # down for over half of it
        self.assertEqual(self.label(guarding, 1.4)[0], actions.NOOP)

    def test_the_tail_of_a_short_guard_tap_is_not_a_hold(self):
        tapping = track((1.06, pad("guard")), (1.14, pad()))
        self.assertEqual(self.label(tapping, 1.0)[0], A("stay+deflect"))
        self.assertEqual(self.label(tapping, 1.1), (actions.NOOP, frozenset()))

    def test_the_tail_of_a_tap_is_neither_a_hold_nor_a_flag(self):
        tapping = track((1.03, pad("attack")), (1.19, pad()))
        self.assertEqual(self.label(tapping, 1.0), (A("stay+attack"), frozenset()))
        self.assertEqual(self.label(tapping, 1.1), (actions.NOOP, frozenset()))

    def test_movement_directions_and_dead_zone(self):
        for stick, name in (((0.0, 1.0), "forward"), ((0.0, -1.0), "back"),
                            ((-1.0, 0.0), "left"), ((1.0, 0.0), "right"),
                            ((0.2, 0.2), "stay"), ((0.3, 0.95), "forward")):
            with self.subTest(stick=stick):
                self.assertEqual(self.label(track((0.5, pad(stick=stick))), 1.0),
                                 (A(f"{name}+none"), frozenset()))

    def test_diagonals(self):
        for stick, name in (((0.6, 0.8), "forward_right"), ((-0.7, 0.7), "forward_left"),
                            ((0.7, -0.6), "back_right"), ((-0.5, -0.5), "back_left"),
                            ((0.3, 0.9), "forward"), ((0.9, -0.3), "right")):
            with self.subTest(stick=stick):
                self.assertEqual(self.label(track((0.5, pad(stick=stick))), 1.0),
                                 (A(f"{name}+none"), frozenset()))

    def test_without_diagonal_actions_a_diagonal_is_the_nearest_direction_with_a_flag(self):
        straight = Labeller(movements=actions.MOVEMENTS[:5])
        action, flags = self.label(track((0.5, pad(stick=(0.6, 0.8)))), 1.0, straight)
        self.assertEqual((action, flags), (1, {"diagonal"}))       # forward, no button

    def test_two_presses_in_one_step_keep_the_first(self):
        both = track((1.02, pad("dodge")), (1.05, pad()), (1.07, pad("attack")), (1.2, pad()))
        self.assertEqual(self.label(both, 1.0), (A("stay+dodge"), {"lost_press"}))

    def test_attack_while_guard_is_down_is_a_combat_art(self):
        held = track((0.5, pad("guard")), (1.03, pad("guard", "attack")), (1.1, pad("guard")))
        self.assertEqual(self.label(held, 1.0), (A("stay+combat_art"), frozenset()))
        self.assertEqual(self.label(held, 1.1), (A("stay+guard_hold"), frozenset()))
        together = track((1.03, pad("guard", "attack")), (1.2, pad()))
        self.assertEqual(self.label(together, 1.0), (A("stay+combat_art"), frozenset()))
        rolled = track((1.03, pad("guard")), (1.05, pad("guard", "attack")), (1.2, pad()))
        self.assertEqual(self.label(rolled, 1.0), (A("stay+combat_art"), frozenset()))
        other_way = track((0.2, pad("attack")), (1.05, pad("guard", "attack")), (1.2, pad()))
        self.assertEqual(self.label(other_way, 1.0), (A("stay+combat_art"), frozenset()))
        with_more = track((1.01, pad("jump")), (1.03, pad("guard", "attack")), (1.2, pad()))
        self.assertEqual(self.label(with_more, 1.0), (A("stay+combat_art"), {"lost_press"}))

    def test_without_a_combat_art_action_attack_under_guard_is_flagged(self):
        art = track((0.5, pad("guard")), (1.03, pad("guard", "attack")), (1.1, pad("guard")))
        self.assertEqual(self.label(art, 1.0, self.old),
                         (A("stay+attack"), {"attack_with_guard_held"}))
        together = track((1.03, pad("guard", "attack")), (1.2, pad()))
        self.assertEqual(self.label(together, 1.0, self.old),
                         (A("stay+attack"), {"attack_with_guard_held", "lost_press"}))

    def test_attack_right_after_guard_is_released_is_not_flagged(self):
        clean = track((0.5, pad("guard")), (1.03, pad("attack")), (1.1, pad()))
        self.assertEqual(self.label(clean, 1.0), (A("stay+attack"), frozenset()))

    def test_a_charged_attack_is_a_thrust(self):
        charged = track((1.03, pad("attack")), (1.8, pad()))
        self.assertEqual(self.label(charged, 1.0), (A("stay+thrust"), frozenset()))
        self.assertEqual(self.label(charged, 1.4), (A("stay+thrust"), frozenset()))
        self.assertEqual(self.label(charged, 1.8), (actions.NOOP, frozenset()))

    def test_without_a_thrust_action_a_charged_attack_is_flagged(self):
        charged = track((1.03, pad("attack")), (1.8, pad()))
        self.assertEqual(self.label(charged, 1.0, self.old), (A("stay+attack"), {"held_attack"}))
        self.assertEqual(self.label(charged, 1.4, self.old), (actions.NOOP, {"held_attack"}))
        self.assertEqual(self.label(charged, 1.8, self.old), (actions.NOOP, frozenset()))

    def test_the_item_button_is_a_heal(self):
        drinking = track((1.02, pad("dpad_up")), (1.12, pad()))
        self.assertEqual(self.label(drinking, 1.0), (A("stay+heal"), frozenset()))
        self.assertEqual(self.label(drinking, 1.1), (actions.NOOP, frozenset()))

    def test_buttons_outside_the_action_set(self):
        self.assertEqual(self.label(track((1.02, pad("pad_x"))), 1.0), (actions.NOOP, {"outside"}))
        self.assertEqual(self.label(track((1.02, pad("dpad_left"))), 1.0),
                         (actions.NOOP, {"outside"}))
        self.assertEqual(self.label(track((1.02, pad("dpad_up")), (1.08, pad())), 1.0, self.old),
                         (actions.NOOP, {"outside"}))
        self.assertEqual(self.label(track((1.02, pad(rt=255))), 1.0), (actions.NOOP, {"outside"}))
        self.assertEqual(self.label(track((1.02, pad("lock_on"))), 1.0), (actions.NOOP, frozenset()))
        self.assertEqual(self.label(track((1.02, pad("pad_start"))), 1.0),
                         (actions.NOOP, frozenset()))

    def test_sprint_without_a_sprint_action_is_flagged(self):
        sprinting = track((1.02, pad("dodge", stick=(0.0, 1.0))), (1.9, pad()))
        self.assertEqual(self.label(sprinting, 1.0, self.old),
                         (A("forward+dodge"), {"held_dodge"}))
        self.assertEqual(self.label(sprinting, 1.5, self.old),
                         (A("forward+none"), {"held_dodge"}))

    def test_a_long_dodge_hold_is_a_sprint_and_a_short_one_a_dodge(self):
        sprinting = track((1.02, pad("dodge", stick=(0.0, 1.0))), (1.9, pad()))
        self.assertEqual(self.label(sprinting, 1.0), (A("forward+sprint"), frozenset()))
        self.assertEqual(self.label(sprinting, 1.5), (A("forward+sprint"), frozenset()))
        tap = track((1.02, pad("dodge")), (1.17, pad()))
        self.assertEqual(self.label(tap, 1.0), (A("stay+dodge"), frozenset()))
        self.assertEqual(self.label(tap, 1.1), (actions.NOOP, frozenset()))

    def test_a_new_button_in_the_action_set_is_no_longer_outside(self):
        modes = actions.BUTTON_MODES + (("tool", "pad_y", "tap"),)
        labeller = Labeller(modes=modes)
        action, flags = self.label(track((1.02, pad("pad_y")), (1.08, pad())), 1.0, labeller)
        self.assertEqual((action, flags), ((len(modes) - 1) * len(actions.MOVEMENTS), frozenset()))


class RoundTripTests(unittest.TestCase):
    """What the agent presses, read back through the labeller, is the same input."""

    def sequence(self, rng, length):
        """Random actions the labeller can tell apart. A hold of a button that
        also has a tap (sprint, thrust) is only a hold once it is long, so it
        comes in runs of four steps or more; and a tap of a button that is
        already held down is no press at all, so it never follows such a run.
        """
        kinds = {}
        for _, button, kind in actions.BUTTON_MODES:
            if isinstance(button, str):
                kinds.setdefault(button, set()).add(kind)
        out, held = [], None
        while len(out) < length:
            move, mode = actions.ACTIONS[rng.randrange(actions.NUM_ACTIONS)]
            _, button, kind = actions.BUTTON_MODES[mode]
            if kind == "tap" and button == held:
                continue
            run = 1
            held = None
            if kind == "hold" and "tap" in kinds[button]:
                run, held = rng.randrange(4, 8), button
            for _ in range(run):
                out.append(mode * len(actions.MOVEMENTS) + rng.randrange(len(actions.MOVEMENTS)))
        return out

    def test_labels_of_the_agents_own_input_reproduce_it(self):
        rng = random.Random(7)
        game = FakeGame()
        loop = StepLoop(game, game, game)
        loop.reset()
        chosen, windows = [], []
        for action in self.sequence(rng, 1500):
            start = game.t
            loop.step(action)
            chosen.append(action)
            windows.append((start, game.t))
        self.assertEqual({mode for _, mode in map(actions.ACTIONS.__getitem__, chosen)},
                         set(range(len(actions.BUTTON_MODES))))
        pad_track = PadTrack([(t, PadState.from_control(state)) for t, state in game.log])
        labeller = Labeller()
        held = NEUTRAL
        for action, (t0, t1) in zip(chosen, windows):
            label, flags = labeller.label(pad_track, t0, t1)
            plan = actions.schedule(action, held)
            self.assertEqual(actions.schedule(label, held), plan,
                             f"{actions.ACTION_NAMES[action]} read back as "
                             f"{actions.ACTION_NAMES[label]}")
            self.assertEqual(flags, frozenset())
            held = plan[-1][1]


class FakePads:
    """slot -> function of time returning a PadState or None; counts reads."""

    def __init__(self, clock, slots):
        self.clock = clock
        self.slots = slots
        self.reads = {slot: 0 for slot in range(4)}
        self.packet = {}

    def read(self, slot):
        self.reads[slot] += 1
        state = self.slots[slot](self.clock.now()) if slot in self.slots else None
        if state is None:
            return None
        last = self.packet.get(slot)
        number = 1 if last is None else last[0] + (state != last[1])
        self.packet[slot] = (number, state)
        return number, state


class RecorderTests(unittest.TestCase):
    def record(self, slots, seconds, game=None, **kwargs):
        game = game or FakeGame()
        rows = []
        recorder = Recorder(game, FakePads(game, slots), game, rows.append, **kwargs)
        why = recorder.run(lambda t: "time is up" if t >= seconds else None)
        return recorder, rows, why

    def test_pad_rows_only_on_change_and_snapshots_at_their_rate(self):
        def user(t):
            return pad("guard") if 0.5 <= t < 0.7 else pad()
        recorder, rows, why = self.record({1: user}, 2.0)
        self.assertEqual(why, "time is up")
        pad_rows = [row for row in rows if row["kind"] == "pad"]
        self.assertEqual([row["slot"] for row in pad_rows], [1, 1, 1])
        self.assertEqual([row["buttons"] for row in pad_rows], [0, mask("guard"), 0])
        self.assertAlmostEqual(pad_rows[1]["t"], 0.5, delta=0.009)
        self.assertAlmostEqual(pad_rows[2]["t"], 0.7, delta=0.009)
        snapshots = [row for row in rows if row["kind"] == "snapshot"]
        self.assertAlmostEqual(len(snapshots), 125, delta=2)
        self.assertEqual(rows[-1]["kind"], "end")
        self.assertEqual(rows[-1]["late_ticks"], 0)
        self.assertEqual(rows[-1]["missed_packets"], 0)

    def test_empty_slots_are_polled_rarely(self):
        recorder, _, _ = self.record({0: lambda t: pad()}, 3.0)
        self.assertGreater(recorder.pads.reads[0], 300)
        for slot in (1, 2, 3):
            self.assertLessEqual(recorder.pads.reads[slot], 4)

    def test_a_pad_that_disconnects_and_comes_back(self):
        def user(t):
            return None if 1.0 <= t < 2.5 else pad("attack")
        _, rows, _ = self.record({0: user}, 4.0)
        pad_rows = [row for row in rows if row["kind"] == "pad"]
        self.assertEqual([row.get("connected", True) for row in pad_rows], [True, False, True])
        self.assertLess(pad_rows[2]["t"], 3.6)

    def test_unavailable_fields_are_left_out_of_a_snapshot_row(self):
        row = demos.snapshot_row(Snapshot(t_ns=5, reason="loading"), 1.23456789)
        self.assertEqual(row, {"kind": "snapshot", "t": 1.23457, "t_ns": 5, "reason": "loading"})

    def test_a_slow_read_makes_late_polls_and_no_catching_up(self):
        class SlowGame(FakeGame):
            def read(self):
                self.advance(0.05)
                return super().read()
        recorder, rows, _ = self.record({0: lambda t: pad()}, 1.0, game=SlowGame())
        self.assertGreater(recorder.late_ticks, 50)
        self.assertLessEqual(recorder.snapshots, 21)


def write_rows(rows, cut=None):
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8")
    with handle:
        text = "".join(json.dumps(row) + "\n" for row in rows)
        handle.write(text if cut is None else text[:-cut])
    return handle.name


class LoadTests(unittest.TestCase):
    def test_round_trip_through_a_file(self):
        game = FakeGame()
        rows = [{"kind": "meta", "pid": 1}]
        Recorder(game, FakePads(game, {2: lambda t: pad("jump") if t > 0.3 else pad()}), game,
                 rows.append).run(lambda t: "time is up" if t >= 1.0 else None)
        rows.insert(1, demos.snapshot_row(
            Snapshot(t_ns=0, player_hp=5, player_max_hp=9, player_pos=(1.0, 2.0, 3.0)), 0.0))
        path = write_rows(rows)
        self.addCleanup(os.remove, path)
        recording = demos.load(path)
        self.assertEqual(recording.meta["pid"], 1)
        self.assertEqual(recording.end["why"], "time is up")
        self.assertEqual(sorted(recording.pads), [2])
        self.assertEqual(len(recording.track().presses("jump", 0.0, 1.0)), 1)
        first = recording.snapshots[0][1]
        self.assertEqual(first.player_pos, (1.0, 2.0, 3.0))
        self.assertIsNone(first.boss_hp)
        self.assertEqual(len(recording.snapshots), len([r for r in rows if r["kind"] == "snapshot"]))

    def test_a_cut_off_last_line_is_dropped(self):
        rows = [{"kind": "meta"}, demos.snapshot_row(Snapshot(t_ns=0, player_hp=1, player_max_hp=2), 0.0),
                demos.snapshot_row(Snapshot(t_ns=0, player_hp=1, player_max_hp=2), 0.1)]
        path = write_rows(rows, cut=20)
        self.addCleanup(os.remove, path)
        recording = demos.load(path)
        self.assertEqual(len(recording.snapshots), 1)
        self.assertIsNone(recording.end)

    def test_the_busiest_pad_is_the_users(self):
        idle = PadTrack([(0.0, PadState())])
        busy = track((1.0, pad("attack")), (1.1, pad()), (2.0, pad("guard")))
        recording = Recording({}, {0: idle, 3: busy}, [])
        self.assertIs(recording.track(), busy)
        self.assertIs(recording.track(0), idle)
        with self.assertRaises(ValueError):
            Recording({}, {}, []).track()


BOSS = dict(boss_hp=9887, boss_max_hp=9887, boss_posture=4083, boss_max_posture=4083)


def game_snapshot(t, **fields):
    values = dict(player_hp=960, player_max_hp=960, player_posture=360, player_max_posture=360,
                  player_anim_id=0, boss_anim_id=405010, locked_on=True,
                  igt_ms=int(t * 1000), frame_token=int(t * 60))
    values.update(BOSS)
    values.update(fields)
    return Snapshot(t_ns=0, **values)


def recording(script, seconds, pad_changes=(), hz=60.0):
    """`script(t)` returns a Snapshot, or keyword fields for `game_snapshot`."""
    snapshots = []
    for index in range(int(seconds * hz) + 1):
        t = index / hz
        made = script(t)
        snapshots.append((t, made if isinstance(made, Snapshot) else game_snapshot(t, **made)))
    return Recording({}, {0: track(*pad_changes)}, snapshots)


LOADING = Snapshot(t_ns=0, reason="loading")


def two_fights(t):
    """Loading, walk-in, a fight lost at 6 s, loading again, a second fight."""
    if t < 1.0 or 8.0 <= t < 9.0:
        return LOADING
    if t < 2.0 or 9.0 <= t < 10.0:
        return dict(boss_anim_id=-1, player_anim_id=-1)
    if t < 3.0 or 10.0 <= t < 11.0:
        return dict(player_anim_id=700010)
    if 6.0 <= t < 8.0:
        return dict(player_hp=0)
    return {}


class FightsTests(unittest.TestCase):
    def test_fights_are_cut_like_the_live_loop(self):
        found = demos.fights(recording(two_fights, 13.0))
        self.assertEqual([fight.ending for fight in found], ["death", "recording_ended"])
        first = found[0]
        self.assertAlmostEqual(first.start_t, 3.0, delta=0.11)
        self.assertAlmostEqual(first.steps[-1].t, 5.9, delta=0.001)
        self.assertEqual(len(first.steps), round((6.0 - first.start_t) / 0.1))
        self.assertTrue(all(step.valid and step.usable for step in first.steps))
        self.assertAlmostEqual(sum(step.reward for step in first.steps), -1.0)
        self.assertAlmostEqual(found[1].start_t, 11.0, delta=0.11)

    def test_the_observation_comes_before_the_input_it_is_paired_with(self):
        presses = ((4.05, pad("guard")), (4.3, pad()))
        found = demos.fights(recording(two_fights, 7.0, presses))
        by_time = {round(step.t, 3): step for step in found[0].steps}
        self.assertEqual(by_time[4.0].action, A("stay+deflect"))
        self.assertLessEqual(by_time[4.0].snapshot.igt_ms, 4000)
        self.assertEqual(by_time[4.1].action, A("stay+guard_hold"))
        self.assertEqual(by_time[3.9].action, actions.NOOP)

    def test_phase_shifts_the_steps(self):
        presses = ((4.05, pad("guard")), (4.3, pad()))
        found = demos.fights(recording(two_fights, 7.0, presses), phase=0.06)
        by_time = {round(step.t, 3): step for step in found[0].steps}
        self.assertEqual(by_time[3.96].action, A("stay+deflect"))
        self.assertEqual(by_time[4.06].action, A("stay+guard_hold"))

    def test_pairs_are_encoded_observations_and_labels(self):
        found = demos.fights(recording(two_fights, 7.0))
        observations, labels = demos.pairs(found)
        self.assertEqual(len(observations), len(found[0].steps))
        self.assertEqual(len(observations[0]), OBS_SIZE)
        self.assertEqual(set(labels), {actions.NOOP})

    def test_a_short_pause_makes_invalid_steps_and_a_long_one_ends_the_fight(self):
        def paused(start, end):
            def script(t):
                made = two_fights(t)
                if made == {} and start <= t < end:
                    return dict(frame_token=int(start * 60))
                return made
            return script
        short = demos.fights(recording(paused(4.0, 4.3), 7.0))[0]
        self.assertEqual(short.ending, "death")
        # The first sample of the pause still shows a new frame.
        self.assertEqual(sum(1 for step in short.steps if not step.valid), 2)
        long = demos.fights(recording(paused(4.0, 5.5), 7.0))[0]
        self.assertEqual(long.ending, "invalid_reads")

    def test_a_gap_in_the_recording_is_an_unavailable_read(self):
        full = recording(two_fights, 7.0)
        full.snapshots = [(t, s) for t, s in full.snapshots if not 4.0 < t < 4.2]
        fight = demos.fights(full)[0]
        self.assertEqual(sum(1 for step in fight.steps if not step.valid), 1)
        self.assertEqual(sum(1 for step in fight.steps if not step.usable), 1)
        self.assertEqual(len(demos.pairs([fight])[0]), len(fight.steps) - 1)

    def test_reward_follows_the_live_rule(self):
        def script(t):
            made = two_fights(t)
            if made == {} and t >= 4.0:
                return dict(boss_posture=4083 - 2000)
            return made
        fight = demos.fights(recording(script, 5.0))[0]
        self.assertAlmostEqual(sum(step.reward for step in fight.steps), 2.0 * 2000 / 4083)

    def deathblows(self, t):
        made = two_fights(t)
        if made != {} or t < 3.0:
            return made
        if 4.0 <= t < 4.5 or 5.0 <= t < 5.5:
            return dict(boss_hp=1, boss_posture=0)
        return {}

    def test_two_deathblows_are_a_victory(self):
        found = demos.fights(recording(self.deathblows, 5.9))
        self.assertEqual([(fight.ending, fight.deathblows) for fight in found], [("victory", 2)])

    def test_the_refill_after_a_winning_deathblow_is_not_a_new_fight(self):
        found = demos.fights(recording(self.deathblows, 5.9), deathblows_to_win=1)
        self.assertEqual([(fight.ending, fight.deathblows) for fight in found], [("victory", 1)])

    def test_no_snapshots_no_fights(self):
        self.assertEqual(demos.fights(Recording({}, {0: track()}, [])), [])


class ReportTests(unittest.TestCase):
    def test_summary_counts(self):
        presses = ((4.05, pad("guard", stick=(0.6, 0.8))), (4.36, pad()),
                   (5.02, pad("pad_x")), (5.1, pad()))
        made = recording(two_fights, 7.0, presses)
        made.snapshots = [(t, s if not 5.0 <= t < 5.5 else game_snapshot(t, locked_on=False))
                          for t, s in made.snapshots]
        found = demos.fights(made)
        summary = demos.summarise(made, found)
        self.assertEqual(summary["steps"], len(found[0].steps))
        self.assertEqual(summary["modes"]["deflect"], 1)
        self.assertEqual(summary["modes"]["guard_hold"], 3)
        self.assertEqual(summary["moves"]["forward_right"], 4)
        self.assertEqual(summary["flags"], {"outside": 1})
        self.assertEqual(summary["flagged"], 1)
        self.assertEqual(summary["unlocked"], 5)
        self.assertEqual(summary["fights"][0]["ending"], "death")
        self.assertEqual(summary["fights"][0]["boss_hp_left"], 1.0)

    def test_report_of_a_file(self):
        game = FakeGame(boss=dict(BOSS, boss_anim_id=405010), hits=((2.0, 400),))
        game.player_anim_id = 0
        rows = [{"kind": "meta"}]
        Recorder(game, FakePads(game, {0: lambda t: pad("attack") if 1.0 <= t < 1.06 else pad()}),
                 game, rows.append).run(lambda t: "time is up" if t >= 3.0 else None)
        path = write_rows(rows)
        self.addCleanup(os.remove, path)
        lines = []
        summary = demos.print_report(path, out=lines.append)
        self.assertEqual(summary["fights"][0]["ending"], "death")
        self.assertEqual(summary["modes"]["attack"], 1)
        text = "\n".join(lines)
        self.assertIn("fight 1:", text)
        self.assertIn("ended: time is up", text)

    def test_report_of_a_recording_without_a_pad(self):
        path = write_rows([{"kind": "meta"},
                           demos.snapshot_row(Snapshot(t_ns=0, reason="loading"), 0.0)])
        self.addCleanup(os.remove, path)
        lines = []
        self.assertIsNone(demos.print_report(path, out=lines.append))
        self.assertIn("No controller rows", lines[1])


class ShowPadsTests(unittest.TestCase):
    def test_lists_connected_slots_and_changes(self):
        game = FakeGame()
        pads = FakePads(game, {1: lambda t: pad("attack") if t >= 0.5 else pad()})
        lines = []
        found = demos.show_pads(pads, 1.0, out=lines.append, sleep=game.advance)
        self.assertEqual(found, [1])
        self.assertEqual(sum("no controller" in line for line in lines), 3)
        self.assertEqual(sum("XUSB_GAMEPAD_RIGHT_SHOULDER" in line for line in lines), 1)

    def test_stick_noise_is_not_printed(self):
        game = FakeGame()
        pads = FakePads(game, {0: lambda t: pad(lx=int(t * 5000) % 300)})
        lines = []
        demos.show_pads(pads, 1.0, out=lines.append, sleep=game.advance)
        self.assertEqual(sum(line.startswith("slot 0") for line in lines), 1)


def dualsense_report(lx=128, ly=128, rx=128, ry=128, l2=0, r2=0, hat=8, face=0, other=0):
    report = bytearray(64)
    report[0:7] = bytes([0x01, lx, ly, rx, ry, l2, r2])
    report[8], report[9] = hat | face, other
    return bytes(report)


class DualSenseTests(unittest.TestCase):
    """The USB input report, as documented by the community drivers; the idle
    report was also seen from the user's controller on 2026-11-27."""

    def test_idle(self):
        state = dualsense_state(dualsense_report())
        self.assertEqual(state, PadState())

    def test_buttons_map_to_the_xbox_button_in_the_same_place(self):
        for face, other, name in ((0x20, 0, "jump"), (0x40, 0, "dodge"), (0, 0x01, "guard"),
                                  (0, 0x02, "attack"), (0, 0x80, "lock_on"),
                                  (0x10, 0, "pad_x"), (0x80, 0, "pad_y"),
                                  (0, 0x20, "pad_start"), (0, 0x10, "pad_back")):
            with self.subTest(name=name):
                state = dualsense_state(dualsense_report(face=face, other=other))
                self.assertEqual(state.buttons, mask(name))

    def test_dpad_hat(self):
        self.assertTrue(dualsense_state(dualsense_report(hat=0)).pressed("use_item"))
        self.assertEqual(dualsense_state(dualsense_report(hat=4)).buttons, mask("dpad_down"))
        self.assertEqual(dualsense_state(dualsense_report(hat=1)).buttons,
                         mask("dpad_up") | mask("dpad_right"))
        self.assertEqual(dualsense_state(dualsense_report(hat=6)).buttons, mask("dpad_left"))

    def test_sticks_and_triggers(self):
        state = dualsense_state(dualsense_report(lx=255, ly=0, rx=0, ry=255, l2=200, r2=10))
        self.assertEqual(state.left_stick(), (1.0, 1.0))        # the report's Y grows downward
        self.assertEqual((state.rx, state.ry), (-32767, -32767))
        self.assertTrue(state.pressed("lt"))
        self.assertFalse(state.pressed("rt"))

    def test_bluetooth_shapes_and_unknown_reports(self):
        usb = dualsense_report(lx=255, face=0x20, other=0x01, l2=99)
        full = bytes([0x31, 0x00]) + usb[1:] + bytes(13)
        self.assertEqual(dualsense_state(full), dualsense_state(usb))
        simple = bytes([0x01, 255, 128, 128, 128, 0x28, 0x01, 0x00, 99, 0]) + bytes(68)
        self.assertEqual(dualsense_state(simple), dualsense_state(usb))
        self.assertIsNone(dualsense_state(bytes([0x05]) + bytes(63)))
        self.assertIsNone(dualsense_state(b""))


if __name__ == "__main__":
    unittest.main()
