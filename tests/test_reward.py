"""The fight reward: HP and posture, between two snapshots. Standard library only."""
import unittest

from sekirorl.loop import FightReward
from sekirorl.snapshot import Snapshot


def snap(**fields):
    values = dict(player_hp=960, player_max_hp=960, player_posture=360, player_max_posture=360,
                  boss_hp=9887, boss_max_hp=9887, boss_posture=4083, boss_max_posture=4083)
    values.update(fields)
    return Snapshot(t_ns=0, **values)


class FightRewardTests(unittest.TestCase):
    def test_nothing_changed_is_zero(self):
        self.assertEqual(FightReward()(snap(), snap()), 0.0)

    def test_player_damage_is_negative_and_healing_positive(self):
        reward = FightReward()
        self.assertAlmostEqual(reward(snap(), snap(player_hp=711)), -249 / 960)
        self.assertAlmostEqual(reward(snap(player_hp=480), snap(player_hp=720)), 0.25)

    def test_boss_hp_damage_is_positive(self):
        self.assertAlmostEqual(FightReward()(snap(), snap(boss_hp=9505)), 382 / 9887)

    def test_boss_posture_damage_is_positive_and_weighs_twice_his_hp(self):
        """A deflect logged on 2026-11-25 took 159 of 4083."""
        self.assertAlmostEqual(FightReward()(snap(), snap(boss_posture=3924)), 2.0 * 159 / 4083)
        self.assertAlmostEqual(FightReward(boss_posture=0.5)(snap(), snap(boss_posture=3924)),
                               0.5 * 159 / 4083)
        same_share = FightReward()(snap(), snap(boss_hp=9887 - 989, boss_posture=4083 - 408))
        self.assertAlmostEqual(same_share, 989 / 9887 + 2.0 * 408 / 4083)

    def test_posture_the_boss_recovers_is_not_taken_back(self):
        self.assertEqual(FightReward()(snap(boss_posture=3924), snap(boss_posture=4083)), 0.0)

    def test_a_deathblow_pays_what_was_left_of_both_bars(self):
        before = snap(boss_hp=4787, boss_posture=1021)
        reward = FightReward()(before, snap(boss_hp=1, boss_posture=0))
        self.assertAlmostEqual(reward, 4786 / 9887 + 2.0 * 1021 / 4083)

    def test_the_refill_after_a_deathblow_pays_nothing(self):
        self.assertEqual(FightReward()(snap(boss_hp=1, boss_posture=0), snap()), 0.0)

    def test_a_rescaled_bar_pays_nothing(self):
        """While the boss wakes up its bars go from 7670/3168 to 9887/4083."""
        before = snap(boss_hp=7670, boss_max_hp=7670, boss_posture=3168, boss_max_posture=3168)
        self.assertEqual(FightReward()(before, snap()), 0.0)
        shrunk = snap(boss_hp=7670, boss_max_hp=7670, boss_posture=3168, boss_max_posture=3168)
        self.assertEqual(FightReward()(snap(), shrunk), 0.0)

    def test_player_posture_damage_costs_a_quarter_of_what_hp_does(self):
        """A deflect costs the player 26 of 360."""
        self.assertAlmostEqual(FightReward()(snap(), snap(player_posture=334)), -0.25 * 26 / 360)
        self.assertEqual(FightReward(player_posture=0.0)(snap(), snap(player_posture=334)), 0.0)
        self.assertEqual(FightReward()(snap(player_posture=334), snap()), 0.0)     # recovering

    def test_a_deflect_is_far_better_than_taking_the_hit(self):
        """The opening attack: deflected -26 posture; not guarded -249 HP and -67 posture."""
        deflected = FightReward()(snap(), snap(player_posture=334))
        hit = FightReward()(snap(), snap(player_hp=711, player_posture=293))
        self.assertAlmostEqual(hit, -249 / 960 - 0.25 * 67 / 360)
        self.assertGreater(deflected, hit / 10)

    def test_a_boss_that_is_not_loaded_leaves_only_the_player_part(self):
        gone = dict(boss_hp=None, boss_max_hp=None, boss_posture=None, boss_max_posture=None)
        self.assertAlmostEqual(FightReward()(snap(**gone), snap(player_hp=711, **gone)), -249 / 960)
        self.assertAlmostEqual(FightReward()(snap(), snap(player_hp=711, **gone)), -249 / 960)


if __name__ == "__main__":
    unittest.main()
