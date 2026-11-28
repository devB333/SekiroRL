"""Encounter rules: unit cases, the step loop on the fake game, and a replay of
the four fights recorded from the live game on 2026-11-25. Standard library only."""
import gzip
import json
import os
import unittest

from sekirorl import actions
from sekirorl.actions import action_index as A
from sekirorl.controller import NEUTRAL
from sekirorl.encounter import Encounter
from sekirorl.fake import FakeGame
from sekirorl.loop import ResetFailed, StepLoop
from sekirorl.snapshot import Snapshot

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "fights-2026-11-25.json.gz")
FULL = 9887


def snap(token=1, **fields):
    """A snapshot of a running fight; keyword arguments override fields."""
    values = dict(player_hp=960, player_max_hp=960, player_anim_id=0, boss_npc_id=71001000,
                  boss_hp=FULL, boss_max_hp=FULL, boss_posture=4083, boss_max_posture=4083,
                  boss_anim_id=405003, frame_token=token)
    values.update(fields)
    return Snapshot(t_ns=0, **values)


def ready_after(encounter, snapshots):
    return [encounter.ready(snapshot) for snapshot in snapshots][-1]


class ReadyTests(unittest.TestCase):
    def test_running_fight_is_ready_once_the_maximum_has_been_stable(self):
        encounter = Encounter(stable_samples=3)
        results = [encounter.ready(snap(token)) for token in (1, 2, 3)]
        self.assertEqual([ok for ok, _ in results], [False, False, True])
        self.assertEqual(results[0][1], "boss maximum HP not stable yet")

    def test_each_missing_condition_blocks_with_its_reason(self):
        cases = (
            (dict(player_hp=None, player_max_hp=None, reason="Invalid/null pointer"),
             "Invalid/null pointer"),
            (dict(player_hp=0), "player is dead"),
            (dict(boss_hp=None, boss_max_hp=None, boss_reason="Boss not in the character collection"),
             "Boss not in the character collection"),
            (dict(boss_anim_id=-1), "boss not active"),
            (dict(boss_anim_id=None), "boss not active"),
            (dict(boss_hp=FULL - 340), "boss not at full HP"),
            (dict(player_anim_id=700010), "player does not have control yet"),
            (dict(player_anim_id=-1), "player does not have control yet"),
            (dict(player_anim_id=None), "player does not have control yet"),
            (dict(frame_token=None), "game frame not advancing"),
        )
        for fields, reason in cases:
            with self.subTest(reason=reason, fields=fields):
                encounter = Encounter()
                self.assertEqual(ready_after(encounter, [snap(t, **fields) for t in range(5)]),
                                 (False, reason))

    def test_frozen_frame_token_blocks(self):
        encounter = Encounter()
        self.assertEqual(ready_after(encounter, [snap(7)] * 5), (False, "game frame not advancing"))
        self.assertEqual(encounter.ready(snap(8)), (True, None))

    def test_rescale_while_the_boss_wakes_up_restarts_the_stability_count(self):
        encounter = Encounter(stable_samples=3)
        dormant = dict(boss_hp=7670, boss_max_hp=7670, boss_anim_id=-1)
        mid = dict(boss_hp=7690, boss_max_hp=7690)
        sequence = [snap(1, **dormant), snap(2, **dormant), snap(3, **mid), snap(4), snap(5)]
        self.assertEqual(ready_after(encounter, sequence),
                         (False, "boss maximum HP not stable yet"))
        self.assertEqual(encounter.ready(snap(6)), (True, None))

    def test_begin_forgets_the_previous_fight(self):
        encounter = Encounter()
        ready_after(encounter, [snap(t) for t in range(4)])
        encounter.update(snap(boss_hp=1, boss_posture=0))
        encounter.begin()
        self.assertEqual(encounter.deathblows, 0)
        self.assertFalse(encounter.ready(snap(9))[0])

    def test_arguments_are_validated(self):
        with self.assertRaises(ValueError):
            Encounter(deathblows_to_win=0)
        with self.assertRaises(ValueError):
            Encounter(stable_samples=0)


class UpdateTests(unittest.TestCase):
    def run_hp(self, encounter, sequence):
        """Feed (boss HP, boss posture) pairs; return the deathblow flag of each step."""
        return [encounter.update(snap(boss_hp=hp, boss_posture=posture))["deathblow"]
                for hp, posture in sequence]

    def test_recorded_deathblow_counts_once_and_the_refill_does_not(self):
        encounter = Encounter()
        flags = self.run_hp(encounter, [(4787, 119), (4787, 0), (1, 0), (1, 0), (FULL, 0),
                                        (FULL, 4083), (FULL - 340, 3900)])
        self.assertEqual(flags, [False, False, True, False, False, False, False])
        self.assertEqual(encounter.deathblows, 1)

    def test_refill_alone_counts_when_the_hp_1_sample_was_missed(self):
        encounter = Encounter()
        flags = self.run_hp(encounter, [(4787, 0), (FULL, 0), (FULL, 4083)])
        self.assertEqual(flags, [False, True, False])

    def test_second_deathblow_is_counted_separately(self):
        encounter = Encounter(deathblows_to_win=2)
        events = [encounter.update(snap(boss_hp=hp, boss_posture=posture))
                  for hp, posture in ((3000, 0), (1, 0), (FULL, 4083), (2000, 0), (1, 0))]
        self.assertEqual([e["deathblows"] for e in events], [0, 1, 1, 1, 2])
        self.assertEqual([e["victory"] for e in events], [False, False, False, False, True])

    def test_ordinary_hits_and_broken_posture_are_not_deathblows(self):
        encounter = Encounter()
        flags = self.run_hp(encounter, [(FULL, 4083), (FULL - 340, 3956), (FULL - 680, 0),
                                        (FULL - 680, 0), (FULL - 1020, 2000)])
        self.assertEqual(flags, [False] * 5)

    def test_hp_1_without_broken_posture_waits_for_the_refill(self):
        encounter = Encounter()
        flags = self.run_hp(encounter, [(341, 500), (1, 500), (FULL, 4083)])
        self.assertEqual(flags, [False, False, True])

    def test_rescale_with_a_new_maximum_is_never_a_deathblow(self):
        encounter = Encounter()
        flags = [encounter.update(snap(boss_hp=hp, boss_max_hp=hp))["deathblow"]
                 for hp in (1598, 7670, 7690, FULL)]
        self.assertEqual(flags, [False] * 4)

    def test_missing_boss_gives_no_event_and_keeps_the_last_value(self):
        encounter = Encounter()
        encounter.update(snap(boss_hp=4787, boss_posture=0))
        gone = encounter.update(snap(boss_hp=None, boss_max_hp=None, boss_posture=None))
        self.assertFalse(gone["deathblow"])
        self.assertTrue(encounter.update(snap(boss_hp=FULL))["deathblow"])

    def test_player_death_is_hp_zero_only(self):
        encounter = Encounter()
        self.assertFalse(encounter.update(snap(player_hp=8))["player_dead"])
        self.assertTrue(encounter.update(snap(player_hp=0))["player_dead"])


BOSS = {"boss_npc_id": 71001000, "boss_hp": 7670, "boss_max_hp": 7670, "boss_posture": 3168,
        "boss_max_posture": 3168, "boss_anim_id": -1}
WAKE = [(0.8, {"boss_hp": FULL, "boss_max_hp": FULL, "boss_posture": 4083,
               "boss_max_posture": 4083, "boss_anim_id": 405003}),
        (1.5, {"player_anim_id": 0})]


def fight(script=(), **kwargs):
    """Fake rematch: boss dormant, wakes at 0.8 s, player gets control at 1.5 s."""
    game = FakeGame(max_hp=960, boss=BOSS, script=WAKE + list(script), **kwargs)
    game.player_anim_id = 700010
    loop = StepLoop(game, game, game, encounter=Encounter(), reset_timeout_s=10.0)
    return game, loop


class LoopTests(unittest.TestCase):
    def test_reset_waits_for_the_boss_to_wake_and_the_player_to_get_control(self):
        game, loop = fight()
        observation, info = loop.reset()
        self.assertGreaterEqual(game.t, 1.5)
        self.assertLess(game.t, 1.8)
        self.assertEqual(game.state, NEUTRAL)

    def test_reset_fails_loudly_when_the_boss_never_wakes(self):
        game = FakeGame(max_hp=960, boss=BOSS)
        loop = StepLoop(game, game, game, encounter=Encounter(), reset_timeout_s=3.0)
        with self.assertRaisesRegex(ResetFailed, "Fight not running.*boss not active"):
            loop.reset()

    def test_player_death_terminates_and_releases(self):
        game, loop = fight(hits=[(2.0, 500), (2.3, 500)])
        loop.reset()
        results = []
        while not results or not (results[-1][2] or results[-1][3]):
            results.append(loop.step(A("forward+none")))
        _, reward, terminated, truncated, info = results[-1]
        self.assertTrue(terminated)
        self.assertFalse(truncated)
        self.assertEqual(info["termination_reason"], "death")
        self.assertEqual(info["player_hp"], 0)
        self.assertLess(reward, 0)
        self.assertEqual(game.state, NEUTRAL)
        self.assertIn("timing_summary", info)
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)

    def test_first_deathblow_terminates_as_victory(self):
        game, loop = fight(script=[(2.0, {"boss_hp": 4787, "boss_posture": 0}),
                                   (2.5, {"boss_hp": 1}),
                                   (5.1, {"boss_hp": FULL})])
        loop.reset()
        results = []
        while not results or not (results[-1][2] or results[-1][3]):
            results.append(loop.step(A("stay+guard_hold")))
        _, reward, terminated, truncated, info = results[-1]
        self.assertTrue(terminated)
        self.assertEqual(info["termination_reason"], "victory")
        self.assertEqual(info["encounter"]["deathblows"], 1)
        self.assertLess(game.t, 2.7)        # at the HP 1 sample, not at the refill
        self.assertEqual(game.state, NEUTRAL)

    def test_death_and_deathblow_on_the_same_step_is_a_death(self):
        game, loop = fight(script=[(2.0, {"boss_hp": 1, "boss_posture": 0, "hp": 0})])
        loop.reset()
        results = []
        while not results or not results[-1][2]:
            results.append(loop.step(actions.NOOP))
        self.assertEqual(results[-1][4]["termination_reason"], "death")

    def test_no_event_is_taken_from_an_invalid_step(self):
        game, loop = fight(script=[(2.0, {"hp": 0})], menu=[(1.9, 2.25)])
        loop.reset()
        results = []
        while not results or not results[-1][2]:
            results.append(loop.step(actions.NOOP))
        invalid = [r for r in results if not r[4]["valid"]]
        self.assertTrue(invalid)
        self.assertTrue(all("encounter" not in r[4] and not r[2] for r in invalid))
        self.assertGreaterEqual(game.t, 2.25)     # the death is reported once the menu closes

    def test_next_reset_starts_a_clean_fight(self):
        game, loop = fight(script=[(2.0, {"boss_hp": 1, "boss_posture": 0}),
                                   (3.0, {"boss_hp": FULL, "boss_posture": 4083})])
        loop.reset()
        while not loop.step(actions.NOOP)[2]:
            pass
        loop.reset()
        self.assertGreaterEqual(game.t, 3.0)
        self.assertFalse(loop.step(actions.NOOP)[2])
        self.assertEqual(loop.encounter.deathblows, 0)

    def test_without_an_encounter_nothing_terminates(self):
        game = FakeGame(max_hp=960, hits=[(0.15, 960)])
        loop = StepLoop(game, game, game, max_steps=5)
        loop.reset()
        results = [loop.step(actions.NOOP) for _ in range(5)]
        self.assertFalse(any(r[2] for r in results))
        self.assertTrue(results[-1][3])


def load_fights():
    with gzip.open(FIXTURE, "rt") as handle:
        data = json.load(handle)
    fields = data["fields"]
    for name, rows in data["fights"].items():
        snapshots = []
        for row in rows:
            values = dict(zip(fields, row))
            seconds = values.pop("t")
            snapshots.append((seconds, Snapshot(t_ns=int(seconds * 1e9), **values)))
        yield name, snapshots


def replay(snapshots, deathblows_to_win=99):
    """Run the rules over one recorded fight the way reset and step would."""
    encounter = Encounter(deathblows_to_win=deathblows_to_win)
    result = {"boss_first": None, "ready": None, "first_death": None, "deathblows": [],
              "first_hit": None}
    for seconds, snapshot in snapshots:
        if result["boss_first"] is None and snapshot.boss_hp is not None:
            result["boss_first"] = seconds
        if (result["first_hit"] is None and snapshot.valid
                and snapshot.player_hp < snapshot.player_max_hp):
            result["first_hit"] = seconds
        if result["ready"] is None:
            if encounter.ready(snapshot)[0]:
                result["ready"] = seconds
            continue
        if not snapshot.valid:
            continue
        events = encounter.update(snapshot)
        if events["deathblow"]:
            result["deathblows"].append((seconds, snapshot.boss_hp))
        if events["player_dead"] and result["first_death"] is None:
            result["first_death"] = seconds
    return result


class RecordedFightTests(unittest.TestCase):
    """Fights logged from the live game on 2026-11-25 through MemoryStateSource.

    Each fixture row is one 10 Hz sample; frame tokens are renumbered so that
    equal tokens stay equal. See MEMORY_READER.md for the sessions.
    """

    @classmethod
    def setUpClass(cls):
        cls.results = {name: replay(snapshots) for name, snapshots in load_fights()}

    def test_fixture_holds_the_four_fights(self):
        self.assertEqual(sorted(self.results),
                         ["live-03", "restart1-live", "restart2-live", "restart3-live"])

    def test_fight_start_is_after_the_boss_wakes_and_before_the_first_hit(self):
        for name, result in self.results.items():
            with self.subTest(fight=name):
                delay = result["ready"] - result["boss_first"]
                self.assertGreater(delay, 7.0)      # the fade-in and the rescale are over
                self.assertLess(delay, 10.0)
                self.assertLess(result["ready"], result["first_hit"])

    def test_only_the_fight_with_a_deathblow_reports_one_and_only_once(self):
        counts = {name: len(result["deathblows"]) for name, result in self.results.items()}
        self.assertEqual(counts, {"live-03": 0, "restart1-live": 0, "restart2-live": 1,
                                  "restart3-live": 0})
        seconds, hp = self.results["restart2-live"]["deathblows"][0]
        self.assertEqual(hp, 1)
        self.assertAlmostEqual(seconds, 232.1)

    def test_player_death_is_found_in_every_fight(self):
        deaths = {name: result["first_death"] for name, result in self.results.items()}
        self.assertEqual(deaths, {"live-03": 45.4, "restart1-live": 23.4,
                                  "restart2-live": 236.8, "restart3-live": 43.9})

    def test_first_deathblow_curriculum_ends_the_winning_fight_before_the_death(self):
        result = self.results["restart2-live"]
        self.assertLess(result["deathblows"][0][0], result["first_death"])


if __name__ == "__main__":
    unittest.main()
