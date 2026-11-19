"""Gymnasium wrapper around `StepLoop`. The only module needing gymnasium/numpy."""
import gymnasium as gym
from gymnasium import spaces
import numpy as np

from . import actions, snapshot
from .clock import RealClock
from .loop import StepLoop


class SekiroEnv(gym.Env):
    """Fixed spaces: Box(OBS_SIZE,) of values plus validity bits, Discrete actions.

    `source` provides `read() -> Snapshot`; `controller` provides
    `apply(ControlState)`, `neutral()` and `close()`. Extra keyword arguments
    go to `StepLoop` (step_interval, max_steps, reset_hook, watchdog_s, ...).
    """

    metadata = {"render_modes": []}

    def __init__(self, source, controller, clock=None, **loop_kwargs):
        super().__init__()
        self.loop = StepLoop(source, controller, clock or RealClock(), **loop_kwargs)
        self.observation_space = spaces.Box(snapshot.OBS_LOW, snapshot.OBS_HIGH,
                                            shape=(snapshot.OBS_SIZE,), dtype=np.float32)
        self.action_space = spaces.Discrete(actions.NUM_ACTIONS)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        observation, info = self.loop.reset()
        return np.asarray(observation, dtype=np.float32), info

    def step(self, action):
        observation, reward, terminated, truncated, info = self.loop.step(action)
        return np.asarray(observation, dtype=np.float32), reward, terminated, truncated, info

    def close(self):
        self.loop.close()
