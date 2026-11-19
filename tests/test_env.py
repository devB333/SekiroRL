"""Gymnasium wrapper checks. Skipped when gymnasium / stable-baselines3 are missing."""
import importlib.util
import unittest

HAVE_GYM = all(importlib.util.find_spec(name) for name in ("gymnasium", "numpy"))
HAVE_SB3 = HAVE_GYM and importlib.util.find_spec("stable_baselines3") is not None


@unittest.skipUnless(HAVE_GYM, "gymnasium and numpy are not installed")
class EnvTests(unittest.TestCase):
    def make(self, **kwargs):
        from sekirorl.env import SekiroEnv
        from sekirorl.fake import FakeGame
        game = FakeGame(**kwargs)
        env = SekiroEnv(game, game, game, max_steps=50)
        self.addCleanup(env.close)
        return game, env

    def test_spaces_are_fixed_and_observations_fit(self):
        import numpy as np
        from sekirorl import actions, snapshot
        game, env = self.make(hits=[(0.15, 80)], unavailable=[(0.35, 0.55)])
        self.assertEqual(env.observation_space.shape, (snapshot.OBS_SIZE,))
        self.assertEqual(env.action_space.n, actions.NUM_ACTIONS)
        observation, info = env.reset(seed=0)
        self.assertEqual(observation.dtype, np.float32)
        for _ in range(10):
            observation, reward, terminated, truncated, info = env.step(env.action_space.sample())
            self.assertTrue(env.observation_space.contains(observation))
            self.assertIsInstance(reward, float)
            self.assertFalse(terminated)

    @unittest.skipUnless(HAVE_SB3, "stable-baselines3 is not installed")
    def test_sb3_check_env(self):
        from stable_baselines3.common.env_checker import check_env
        game, env = self.make()
        check_env(env, warn=True)


if __name__ == "__main__":
    unittest.main()
