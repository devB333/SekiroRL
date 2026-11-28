"""Scripted deflect: unit cases and a replay of every opening attack logged
from the live game on 2026-11-25. Standard library only."""
import json
import os
import unittest

from sekirorl import actions
from sekirorl.scripted import OPENING_ATTACK, AttackOutcomes, DeflectPolicy
from sekirorl.snapshot import Snapshot

FIXTURE = os.path.join(os.path.dirname(__file__), "data", "opening-attack-2026-11-25.json")
DEFLECT = actions.action_index("stay+deflect")
HOLD = actions.action_index("stay+guard_hold")


def load_runs():
    with open(FIXTURE, encoding="utf-8") as handle:
        data = json.load(handle)
    for run in data["runs"]:
        yield (f'{run["fight"]} at {run["seconds"]} s',
               [Snapshot(t_ns=0, **dict(zip(data["fields"], row))) for row in run["rows"]])


def snap(animation, elapsed, hp=960, posture=360, boss_posture=4083, player_anim=0):
    return Snapshot(t_ns=0, player_hp=hp, player_max_hp=960, player_posture=posture,
                    player_max_posture=360, boss_hp=9887, boss_max_hp=9887,
                    boss_posture=boss_posture, boss_max_posture=4083,
                    boss_anim_id=animation, boss_anim_elapsed=elapsed,
                    player_anim_id=player_anim)


class DeflectPolicyTests(unittest.TestCase):
    def test_presses_once_then_holds_until_the_attack_ends(self):
        policy = DeflectPolicy(press_from=0.58)
        elapsed = [0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85]
        chosen = [policy(snap(OPENING_ATTACK, e)) for e in elapsed]
        self.assertEqual(chosen, [actions.NOOP] * 6 + [DEFLECT, HOLD, HOLD])
        self.assertEqual(policy(snap(3015, 0.02)), actions.NOOP)
        self.assertEqual(policy.presses, [0.65])

    def test_other_animations_and_missing_boss_are_noops(self):
        policy = DeflectPolicy()
        self.assertEqual(policy(snap(3050, 0.6)), actions.NOOP)
        self.assertEqual(policy(Snapshot(t_ns=0, reason="x")), actions.NOOP)
        self.assertEqual(policy(snap(OPENING_ATTACK, None)), actions.NOOP)

    def test_does_not_press_after_the_hit_time(self):
        policy = DeflectPolicy()
        self.assertEqual(policy(snap(OPENING_ATTACK, 0.80)), actions.NOOP)

    def test_presses_again_when_the_attack_comes_again(self):
        policy = DeflectPolicy()
        chosen = [policy(snap(OPENING_ATTACK, e)) for e in (0.6, 0.7, 0.8, 0.1, 0.6)]
        self.assertEqual(chosen, [DEFLECT, HOLD, HOLD, actions.NOOP, DEFLECT])
        policy(snap(3015, 0.0))
        self.assertEqual(policy(snap(OPENING_ATTACK, 0.6)), DEFLECT)

    def test_a_small_step_back_in_the_animation_time_is_not_a_new_attack(self):
        """As read from the live game on 2026-11-27: 0.58, 0.53, 0.78, 0.73."""
        policy = DeflectPolicy()
        chosen = [policy(snap(OPENING_ATTACK, e)) for e in (0.48, 0.58, 0.53, 0.78, 0.73)]
        self.assertEqual(chosen, [actions.NOOP, DEFLECT, HOLD, HOLD, HOLD])
        self.assertEqual(policy.presses, [0.58])

    def test_rejects_a_press_time_after_the_hit(self):
        with self.assertRaises(ValueError):
            DeflectPolicy(press_from=0.9)


class AttackOutcomesTests(unittest.TestCase):
    def outcome(self, after):
        """One attack; `after` overrides the samples from the hit on."""
        outcomes = AttackOutcomes()
        rows = [snap(3003, 0.5), snap(OPENING_ATTACK, 0.65), snap(OPENING_ATTACK, 0.75),
                snap(OPENING_ATTACK, 0.85, **after), snap(3015, 0.05, **after),
                snap(3015, 0.15, **after)]
        results = [outcomes.update(row) for row in rows]
        self.assertEqual(results[:-1], [None] * 5)
        return results[-1]

    def test_outcomes(self):
        self.assertEqual(self.outcome(dict(hp=711, posture=293, player_anim=100300)), "hit")
        self.assertEqual(self.outcome(dict(posture=334, player_anim=50130201)), "deflect")
        self.assertEqual(self.outcome(dict(posture=352, boss_posture=3924, player_anim=50130101)),
                         "deflect")
        self.assertEqual(self.outcome(dict(posture=308, player_anim=50120204)), "block")
        self.assertEqual(self.outcome(dict(posture=334)), "block")     # no known reaction
        self.assertEqual(self.outcome({}), "nothing")

    def test_a_reaction_left_over_from_the_attack_before_does_not_count(self):
        outcomes = AttackOutcomes()
        rows = [snap(3023, 2.8, player_anim=50130700), snap(OPENING_ATTACK, 0.07, player_anim=50130700),
                snap(OPENING_ATTACK, 0.67, player_anim=0), snap(OPENING_ATTACK, 0.87, hp=711),
                snap(3015, 0.03, hp=711), snap(3015, 0.13, hp=711)]
        self.assertEqual([outcomes.update(row) for row in rows][-1], "hit")
        outcomes = AttackOutcomes()
        rows = [snap(3023, 2.8, player_anim=50130700), snap(OPENING_ATTACK, 0.07, player_anim=50130700),
                snap(OPENING_ATTACK, 0.87, player_anim=50130700), snap(3015, 0.03, player_anim=50130700),
                snap(3015, 0.13, player_anim=50130700)]
        self.assertEqual([outcomes.update(row) for row in rows][-1], "nothing")

    def test_a_block_and_a_deflect_in_one_attack_is_a_block(self):
        outcomes = AttackOutcomes()
        rows = [snap(OPENING_ATTACK, 0.7), snap(OPENING_ATTACK, 0.8, posture=334, player_anim=50130201),
                snap(OPENING_ATTACK, 0.9, posture=282, player_anim=50120204),
                snap(3015, 0.0, posture=282, player_anim=50120204),
                snap(3015, 0.1, posture=282, player_anim=50120204)]
        self.assertEqual([outcomes.update(row) for row in rows][-1], "block")

    def test_posture_recovering_is_not_damage(self):
        outcomes = AttackOutcomes()
        rows = [snap(OPENING_ATTACK, 0.7, posture=300), snap(OPENING_ATTACK, 0.8, posture=309),
                snap(3015, 0.0, posture=318), snap(3015, 0.1, posture=327)]
        self.assertEqual([outcomes.update(row) for row in rows][-1], "nothing")


class RecordedOpeningAttackTests(unittest.TestCase):
    """The ten occurrences of boss animation 3014 in the logs of 2026-11-25."""

    def test_the_policy_presses_once_40_to_200_ms_before_every_logged_hit(self):
        runs = list(load_runs())
        self.assertEqual(len(runs), 10)
        for name, snapshots in runs:
            with self.subTest(run=name):
                policy = DeflectPolicy()
                chosen = [policy(snapshot) for snapshot in snapshots]
                self.assertEqual(chosen.count(DEFLECT), 1)
                press = snapshots[chosen.index(DEFLECT)]
                self.assertEqual(press.boss_anim_id, OPENING_ATTACK)
                self.assertGreaterEqual(press.boss_anim_elapsed, 0.58)
                self.assertLess(press.boss_anim_elapsed, 0.68)
                for snapshot, action in zip(snapshots, chosen):
                    if snapshot.boss_anim_id != OPENING_ATTACK:
                        self.assertEqual(action, actions.NOOP)

    def test_the_hit_always_lands_between_the_same_two_samples(self):
        """The first sample showing damage is at 0.77 to 0.88 s of animation time."""
        for name, snapshots in load_runs():
            with self.subTest(run=name):
                for before, after in zip(snapshots, snapshots[1:]):
                    hurt = (after.player_hp < before.player_hp
                            or after.player_posture < before.player_posture - 5)
                    if hurt and after.boss_anim_id == OPENING_ATTACK:
                        self.assertGreater(after.boss_anim_elapsed, 0.76)
                        self.assertLess(after.boss_anim_elapsed, 0.89)
                        self.assertLess(before.boss_anim_elapsed, 0.79)
                        break
                else:
                    self.fail("no damage during the attack")

    def test_logged_outcomes_are_six_hits_three_deflects_and_one_block(self):
        """By hand on 2026-11-25. In the three deflects guard went up 0.05 s or
        less before the hit; in the block it had been up for 0.5 s."""
        outcomes = []
        for _, snapshots in load_runs():
            scorer = AttackOutcomes()
            outcomes += [result for result in map(scorer.update, snapshots) if result]
        self.assertEqual(sorted(outcomes), ["block"] + ["deflect"] * 3 + ["hit"] * 6)


if __name__ == "__main__":
    unittest.main()
