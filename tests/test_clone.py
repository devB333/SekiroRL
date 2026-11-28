"""Behaviour cloning on a simulated recording. Needs Stable-Baselines3."""
import os
import tempfile
import unittest

try:
    import numpy as np
    from sekirorl import clone
    from sekirorl.train import make_model
except ImportError as error:        # a skip is not a pass
    clone = None
    MISSING = str(error)

from sekirorl import actions, demos, runner
from sekirorl.actions import action_index as A
from sekirorl.controller import ControlState
from sekirorl.demos import DemoFight, DemoStep, PadTrack, Recording
from sekirorl.pad import PadState
from sekirorl.snapshot import OBS_SIZE, Snapshot, encode

FIGHT_S, GAP_S, ATTACK_EVERY_S = 12.0, 4.0, 1.5
GUARD = PadState.from_control(ControlState(buttons=frozenset({"guard"})))


def simulated_recording(fights=10, hz=60.0):
    """Fights in which the boss attacks every 1.5 s (animation 3014) and the
    user presses guard 0.2 s into each attack and holds it for 0.3 s. Every
    fight ends with the player's death; a loading screen separates them."""
    snapshots, changes = [], [(0.0, PadState())]
    period = FIGHT_S + GAP_S
    for index in range(int(fights * period * hz)):
        t = index / hz
        local = t % period
        if local < 1.0:
            snapshots.append((t, Snapshot(t_ns=0, reason="loading")))
            continue
        fields = dict(player_hp=960, player_max_hp=960, player_posture=360,
                      player_max_posture=360, player_anim_id=0, boss_hp=9887, boss_max_hp=9887,
                      boss_posture=4083, boss_max_posture=4083, boss_anim_id=405010,
                      boss_anim_elapsed=0.0, igt_ms=int(t * 1000), frame_token=int(t * 60))
        if local < 2.0:
            fields.update(player_anim_id=700010)
        elif local >= FIGHT_S:
            fields.update(player_hp=0)
        else:
            into = (local - 2.0) % ATTACK_EVERY_S
            if into < 0.6:
                fields.update(boss_anim_id=3014, boss_anim_elapsed=into)
            else:
                fields.update(boss_anim_elapsed=into - 0.6)
        snapshots.append((t, Snapshot(t_ns=0, **fields)))
    for fight in range(fights):
        start = fight * period + 2.0
        attack = start + ATTACK_EVERY_S
        while attack + 0.5 < fight * period + FIGHT_S:
            changes.append((attack + 0.2, GUARD))
            changes.append((attack + 0.5, PadState()))
            attack += ATTACK_EVERY_S
    return Recording({}, {0: PadTrack(changes)}, snapshots)


@unittest.skipIf(clone is None, "Stable-Baselines3 or numpy is missing")
class CloneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.recording = simulated_recording()
        cls.train, cls.held = clone.dataset([cls.recording])

    def test_every_fifth_fight_is_held_out_at_every_shift(self):
        self.assertEqual((self.train["fights"], self.held["fights"]), (8, 2))
        self.assertEqual(self.train["obs"].shape[1], OBS_SIZE)
        self.assertEqual(len(self.train["obs"]), len(self.train["action"]))
        self.assertEqual(len(self.train["value"]), len(self.train["action"]))
        self.assertAlmostEqual(len(self.train["action"]) / len(self.held["action"]), 4.0, delta=0.1)
        steps = sum(len(fight.steps) for fight in demos.fights(self.recording))
        self.assertAlmostEqual(len(self.train["action"]) + len(self.held["action"]),
                               steps * len(clone.PHASES), delta=40)

    def test_no_holdout(self):
        train, held = clone.dataset([self.recording], phases=(0.0,), holdout_every=0)
        self.assertEqual((train["fights"], held["fights"]), (10, 0))
        self.assertEqual(len(held["action"]), 0)
        self.assertEqual(held["obs"].shape, (0, OBS_SIZE))

    def test_returns_are_discounted_to_the_end_of_the_fight(self):
        fight = DemoFight(start_t=0.0)
        for reward in (0.0, 1.0, -2.0):
            fight.steps.append(DemoStep(0.0, Snapshot(t_ns=0), 0, frozenset(), reward, True, True))
        self.assertEqual(clone.returns(fight, 0.5), [0.0, 0.0, -2.0])
        self.assertEqual(clone.returns(fight, 1.0), [-1.0, -1.0, -2.0])
        # The player's death at the end of every simulated fight costs a full bar.
        self.assertLess(self.train["value"].max(), 0.0)
        self.assertAlmostEqual(self.train["value"].min(), -1.0, places=5)

    def test_rare_actions_weigh_more_within_limits(self):
        labels = np.array([0] * 900 + [3] * 90 + [7] * 10)
        weights = clone.class_weights(labels, power=0.5)
        self.assertLess(weights[0], weights[3])
        self.assertLess(weights[3], weights[7])
        self.assertEqual(weights[1], 1.0)                   # never seen: left alone
        flat = clone.class_weights(labels, power=0.0)
        self.assertTrue((flat == 1.0).all())
        extreme = clone.class_weights(np.array([0] * 100000 + [1, 2, 3, 4]), power=1.0)
        self.assertEqual((extreme[0], extreme[1]), (0.25, 8.0))

    def test_the_copy_presses_guard_where_the_recording_does_and_can_be_resumed(self):
        model = make_model(clone.SpacesOnly(), seed=0)
        model.verbose = 0
        before = clone.match(model.policy, self.held)
        lines = []
        history = clone.clone(model, self.train, self.held, epochs=40, learning_rate=3e-3,
                              out=lines.append)
        self.assertEqual(len(history), 40)
        self.assertEqual(len(lines), 40)
        self.assertLess(min(row["check_loss"] for row in history), history[0]["check_loss"])
        after = clone.match(model.policy, self.held)
        self.assertGreater(after["same_action"], before["same_action"])
        # Not 100%: at the edge of a step the shifted grids label the same view differently.
        self.assertGreater(after["same_buttons"], 0.85)
        self.assertEqual(after["same_movement"], 1.0)
        self.assertGreater(after["buttons"]["deflect"]["found"], 0.8)
        self.assertGreater(after["buttons"]["guard_hold"]["found"], 0.7)
        self.assertEqual(set(self.held["action"]),
                         {actions.NOOP, A("stay+deflect"), A("stay+guard_hold")})
        shown = []
        clone.print_match("held", after, out=shown.append)
        self.assertIn("same action", shown[0])
        self.assertTrue(any("deflect" in line for line in shown))

        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "last")
            model.save(path)
            again = make_model(clone.SpacesOnly(), resume=path + ".zip")
            self.assertEqual(again.action_space.n, actions.NUM_ACTIONS)
            self.assertTrue((clone.predict(again.policy, self.held["obs"])
                             == clone.predict(model.policy, self.held["obs"])).all())
            # The runner's policy from the same file picks the same actions.
            policy = runner.model_policy(path + ".zip")
            fight = demos.fights(self.recording)[4]
            chosen = [policy(step.snapshot) for step in fight.steps]
            self.assertEqual(chosen, list(clone.predict(model.policy, np.asarray(
                [encode(step.snapshot) for step in fight.steps], dtype=np.float32))))
            self.assertIn(A("stay+deflect"), chosen)
            sampled = runner.model_policy(path + ".zip", sample=True)
            self.assertIn(sampled(fight.steps[0].snapshot), range(actions.NUM_ACTIONS))

    def test_match_of_nothing(self):
        model = make_model(clone.SpacesOnly(), seed=0)
        empty = {"obs": np.zeros((0, OBS_SIZE), dtype=np.float32),
                 "action": np.zeros(0, dtype=np.int64)}
        self.assertEqual(clone.match(model.policy, empty), {"steps": 0})

    def test_the_spaces_only_environment_cannot_be_played(self):
        with self.assertRaises(RuntimeError):
            clone.SpacesOnly().reset()


if __name__ == "__main__":
    unittest.main()
