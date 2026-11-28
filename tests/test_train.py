"""The training script's pieces on the simulated rematch. Needs Stable-Baselines3;
skipped without it, and a skip is not a pass."""
import os
import tempfile
import unittest

from sekirorl.encounter import Encounter
from sekirorl.fake import FakeRematch
from sekirorl.loop import FightReward
from sekirorl.reset import GENICHIRO_REMATCH, LockKeeper, RematchReset, RetryingReset

try:
    from sekirorl import train
    from sekirorl.env import SekiroEnv
except ImportError:                                 # gymnasium or stable-baselines3 missing
    train = None

MENU = ("dpad_down",) * 8 + ("pad_a",) + ("dpad_down",) * 2 + ("pad_a", "pad_a")


@unittest.skipIf(train is None, "stable-baselines3 is not installed")
class TrainTests(unittest.TestCase):
    def setUp(self):
        self.game = FakeRematch(menu=MENU, die="guard", camera_deg=40, bars=2)
        self.rows, self.lines = [], []
        hook = RetryingReset(RematchReset(self.game, self.game, self.game, GENICHIRO_REMATCH),
                             self.game)
        self.env = train.EpisodeLog(SekiroEnv(
            self.game, self.game, self.game, max_steps=300, reset_timeout_s=60.0,
            reset_hook=hook, lock_keeper=LockKeeper(), reward_fn=FightReward(),
            encounter=Encounter(deathblows_to_win=2)), self.rows.append, out=self.lines.append)

    def tearDown(self):
        self.env.close()

    def test_a_short_run_trains_saves_and_logs_every_fight(self):
        model = train.make_model(self.env, n_steps=64, batch_size=32, n_epochs=2)
        model.verbose = 0
        with tempfile.TemporaryDirectory() as folder:
            why = train.train(model, folder, total_steps=256, keep_every=2, out=self.lines.append)
            self.assertEqual(why, "finished")
            self.assertEqual(model.num_timesteps, 256)
            self.assertTrue(os.path.exists(os.path.join(folder, "last.zip")))
            self.assertTrue(os.path.exists(os.path.join(folder, "step_128.zip")))
            self.assertGreaterEqual(len(self.rows), 1)
            for row in self.rows:
                self.assertIn(row["ending"], ("death", "victory", "step_limit"))
                self.assertEqual(row["invalid"], 0)
                self.assertEqual(row["lock_presses"], 1)
            self.assertEqual(self.game.fights_started, len(self.rows) + 1)

            again = train.make_model(self.env, resume=os.path.join(folder, "last.zip"))
            again.verbose = 0
            train.train(again, folder, total_steps=64, out=self.lines.append)
            self.assertEqual(again.num_timesteps, 256 + 64)

    def test_a_saved_model_can_be_resumed_with_gentler_settings(self):
        model = train.make_model(self.env, n_steps=64, batch_size=32, n_epochs=1)
        model.verbose = 0
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "last")
            model.save(path)
            same = train.make_model(self.env, resume=path + ".zip")
            self.assertEqual((same.learning_rate, same.ent_coef, same.clip_range(1.0)),
                             (3e-4, 0.01, 0.2))
            gentle = train.make_model(self.env, resume=path + ".zip", **train.FINE_TUNE)
            gentle.verbose = 0
            self.assertEqual((gentle.learning_rate, gentle.ent_coef), (1e-4, 0.002))
            self.assertEqual((gentle.lr_schedule(1.0), gentle.lr_schedule(0.1)), (1e-4, 1e-4))
            self.assertEqual(gentle.clip_range(0.5), 0.1)
            self.assertEqual(gentle.n_steps, 64)
            self.assertEqual(train.train(gentle, folder, total_steps=64, out=self.lines.append),
                             "finished")
            self.assertEqual(gentle.policy.optimizer.param_groups[0]["lr"], 1e-4)
            with self.assertRaises(ValueError):
                train.make_model(self.env, resume=path + ".zip", n_steps=128)

    def test_the_run_stops_when_the_time_is_up_and_still_saves(self):
        model = train.make_model(self.env, n_steps=64, batch_size=32, n_epochs=1)
        model.verbose = 0
        with tempfile.TemporaryDirectory() as folder:
            why = train.train(model, folder, total_steps=10_000, deadline=0.0,
                              out=self.lines.append)
            self.assertEqual(why, "time is up")
            self.assertLess(model.num_timesteps, 10_000)
            self.assertTrue(os.path.exists(os.path.join(folder, "last.zip")))

    def test_a_reset_that_keeps_failing_ends_the_run_with_the_model_saved(self):
        model = train.make_model(self.env, n_steps=64, batch_size=32, n_epochs=1)
        model.verbose = 0
        with tempfile.TemporaryDirectory() as folder:
            train.train(model, folder, total_steps=64, out=self.lines.append)
            self.game.menu = ("pad_a",)                 # the menu inputs no longer work
            why = train.train(model, folder, total_steps=5_000, out=self.lines.append)
            self.assertTrue(why.startswith("reset failed"), why)
            self.assertTrue(os.path.exists(os.path.join(folder, "last.zip")))
            self.assertEqual(self.game.state.buttons, frozenset())


if __name__ == "__main__":
    unittest.main()
