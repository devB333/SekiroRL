"""Train PPO (Stable-Baselines3) on the live rematch.

    python -m sekirorl.train --pid <sekiro pid> --hours 8
    python -m sekirorl.train --pid <sekiro pid> --hours 8 --resume models/<run>/last.zip

Start Sekiro with every other controller switched off, load the save and stand
near the idol. The game has to stay the foreground window for the whole run:
leave the screen on (turning it off stopped a run on 2026-11-27) and do not
lock the PC. The script asks Windows not to sleep or blank the display. Input starts 3 s after the game window is in front. Alt-tab
stops the run: the model is saved and the inputs are released.

What it does: the agent sees `snapshot.encode` (bars, distance, both
animations), picks one of the actions (`actions.py`) ten times a second, and is scored by
`loop.FightReward`. An episode ends at the player's death or at the boss's
second deathblow; `reset.RematchReset` then goes back in through the idol.

PPO stops acting while it learns from a batch of steps. Measured on the
user's PC on 2026-11-27: 0.08 s for a batch of 512 and 0.30 s for 2048, plus
the save. The loop is told to take a pause of up to `PAUSE_S` as one late step
(the inputs stay as they were) instead of as a gap that ends the episode. A
longer pause still ends the episode as truncated with the inputs released.

Files: `models/<run>/last.zip` after every batch, `step_<n>.zip` every
`--keep-every` batches, and `episodes.jsonl` with one row per fight.
"""
import argparse
import json
import os
import sys
import time

import gymnasium as gym
from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import BaseCallback

from . import memory, reset
from .clock import RealClock
from .controller import NEUTRAL, VigemController
from .encounter import Encounter
from .env import SekiroEnv
from .loop import FightReward, ResetFailed
from .runner import wait_for_focus
from .snapshot import SCHEMA_VERSION
from .winutil import focus_check_for, keep_awake

DEFAULTS = dict(n_steps=2048, batch_size=256, n_epochs=10, gamma=0.995, gae_lambda=0.95,
                learning_rate=3e-4, ent_coef=0.01, clip_range=0.2)
NET_ARCH = [128, 128]
PAUSE_S = 1.0       # longest pause between two steps that does not end the episode


class EpisodeLog(gym.Wrapper):
    """Adds up each episode and hands one row per episode to `write`."""

    def __init__(self, env, write, out=print):
        super().__init__(env)
        self.write = write
        self.out = out
        self.episodes = 0
        self._begin()

    def _begin(self):
        self._row = dict(steps=0, reward=0.0, invalid=0, lock_presses=0, unlocked=0,
                         deathblows=0, boss_hp_left=None)
        self._asked = time.time()

    def reset(self, **kwargs):
        self._begin()
        result = self.env.reset(**kwargs)
        self._row["reset_s"] = round(time.time() - self._asked, 1)
        return result

    def step(self, action):
        observation, reward, terminated, truncated, info = self.env.step(action)
        row = self._row
        row["steps"] += 1
        row["reward"] += reward
        row["invalid"] += not info["valid"]
        row["lock_presses"] += bool(info.get("lock_pressed"))
        row["unlocked"] += info["valid"] and info.get("locked_on") is False
        if info.get("encounter"):
            row["deathblows"] = info["encounter"]["deathblows"]
        if info["valid"] and info.get("boss_hp") is not None and info.get("boss_max_hp"):
            row["boss_hp_left"] = round(info["boss_hp"] / info["boss_max_hp"], 4)
        if terminated or truncated:
            self.episodes += 1
            row.update(kind="episode", episode=self.episodes, time=time.time(),
                       reward=round(row["reward"], 4),
                       ending=info.get("termination_reason") or info.get("truncation_reason"))
            self.write(row)
            self.out(f"fight {self.episodes}: {row['steps']} steps, {row['ending']}, reward "
                     f"{row['reward']:+.3f}, boss HP left {row['boss_hp_left']}, deathblows "
                     f"{row['deathblows']}, invalid {row['invalid']}, reset {row.get('reset_s')} s")
        return observation, reward, terminated, truncated, info


# The parts of the SB3 policy that choose the action; the value head is the rest.
ACTION_PARTS = ("mlp_extractor.policy_net.", "action_net.")


def freeze_actions(model, frozen):
    """Stop (or resume) learning in the part of the network that picks the
    action, leaving the value head free to learn."""
    for name, parameter in model.policy.named_parameters():
        if name.startswith(ACTION_PARTS):
            parameter.requires_grad_(not frozen)


class RunControl(BaseCallback):
    """Saves after every batch and stops the run when the time is up.

    For the first `value_warmup` batches only the value head learns. A cloned
    policy comes with a value head fitted to the user's fights, which score
    far above its own; on 2026-11-28 PPO started from one got worse within
    two batches (fights 1-5 averaged -0.59, fights 6-12 -1.14).
    """

    def __init__(self, folder, deadline=None, keep_every=5, now=time.time, value_warmup=0):
        super().__init__()
        self.folder = folder
        self.deadline = deadline
        self.keep_every = keep_every
        self.now = now
        self.value_warmup = value_warmup
        self.batches = 0

    def _on_training_start(self):
        freeze_actions(self.model, self.value_warmup > 0)

    def _on_step(self):
        return self.deadline is None or self.now() < self.deadline

    def _on_rollout_end(self):
        self.batches += 1               # the update for this batch comes right after
        if self.value_warmup and self.batches == self.value_warmup + 1:
            freeze_actions(self.model, False)
        self.model.save(os.path.join(self.folder, "last"))
        if self.keep_every and self.batches % self.keep_every == 0:
            self.model.save(os.path.join(self.folder, f"step_{self.model.num_timesteps}"))


# Gentler settings for going on from a cloned policy, so the first batches do
# not undo it: smaller steps, a tighter clip, and almost no push toward random
# actions. The agent's numbers of 2026-11-27, not tuned.
FINE_TUNE = dict(learning_rate=1e-4, ent_coef=0.002, clip_range=0.1)
RESUMABLE = ("learning_rate", "ent_coef", "clip_range")


def make_model(env, resume=None, seed=0, **overrides):
    """A new model, or a saved one. With `resume`, only the settings in
    `RESUMABLE` can be changed; the rest comes from the saved model."""
    if resume:
        unknown = set(overrides) - set(RESUMABLE)
        if unknown:
            raise ValueError(f"Cannot change {sorted(unknown)} of a saved model")
        custom = {}
        if "learning_rate" in overrides:
            rate = overrides["learning_rate"]
            custom.update(learning_rate=rate, lr_schedule=lambda _: rate)
        if "clip_range" in overrides:
            clip = overrides["clip_range"]
            custom.update(clip_range=lambda _: clip)
        model = PPO.load(resume, env=env, device="cpu", custom_objects=custom)
        if "ent_coef" in overrides:
            model.ent_coef = overrides["ent_coef"]
        return model
    settings = dict(DEFAULTS, **overrides)
    return PPO("MlpPolicy", env, policy_kwargs=dict(net_arch=NET_ARCH), device="cpu",
               seed=seed, verbose=1, **settings)


def train(model, folder, total_steps, deadline=None, keep_every=5, out=print, value_warmup=0):
    """Run `model.learn` and always leave `last.zip` behind. Returns why it stopped."""
    os.makedirs(folder, exist_ok=True)
    control = RunControl(folder, deadline=deadline, keep_every=keep_every,
                         value_warmup=value_warmup)
    why = "finished"
    try:
        model.learn(total_timesteps=total_steps, callback=control, reset_num_timesteps=False)
        if deadline is not None and time.time() >= deadline:
            why = "time is up"
    except ResetFailed as error:
        why = f"reset failed: {error}"
    except KeyboardInterrupt:
        why = "interrupted"
    freeze_actions(model, False)
    model.save(os.path.join(folder, "last"))
    out(f"Stopped: {why}. {model.num_timesteps} steps in total; model saved in {folder}")
    return why


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--hours", type=float, default=1.0, help="stop after this long")
    parser.add_argument("--steps", type=int, default=10_000_000, help="or after this many steps")
    parser.add_argument("--resume", default=None, help="a saved model to go on from")
    parser.add_argument("--fine-tune", action="store_true",
                        help="with --resume: use the gentler settings for going on from a "
                             f"cloned policy ({FINE_TUNE})")
    parser.add_argument("--name", default=None, help="run folder under models/")
    parser.add_argument("--n-steps", type=int, default=DEFAULTS["n_steps"],
                        help="steps per batch (default 2048, about 3.4 minutes of fighting)")
    parser.add_argument("--deathblows", type=int, default=2)
    parser.add_argument("--max-steps", type=int, default=3000, help="step limit of one fight")
    parser.add_argument("--keep-every", type=int, default=5, help="keep a copy every N batches")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--value-warmup", type=int, default=0, metavar="BATCHES",
                        help="for this many batches only the value head learns (use when "
                             "starting from a cloned policy; 0 when resuming a PPO run)")
    args = parser.parse_args()
    if args.fine_tune and not args.resume:
        parser.error("--fine-tune needs --resume")

    try:
        reader = memory.Reader(args.pid)
    except OSError as error:
        parser.exit(1, f"Cannot open process {args.pid}: {error}\n")
    folder = os.path.join("models", args.name or time.strftime("ppo-%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    log = open(os.path.join(folder, "episodes.jsonl"), "a", encoding="utf-8")

    def write(row):
        log.write(json.dumps(row) + "\n")
        log.flush()

    controller = env = None
    try:
        source = memory.MemoryStateSource(reader, reader.module_base(), boss=memory.GENICHIRO)
        controller = VigemController()
        clock = RealClock()
        focus_check = focus_check_for(args.pid)
        hook = reset.RetryingReset(
            reset.RematchReset(source, controller, clock, reset.GENICHIRO_REMATCH,
                               focus_check=focus_check), clock, out=print)
        env = EpisodeLog(SekiroEnv(
            source, controller, clock, max_steps=args.max_steps, watchdog_s=PAUSE_S,
            gap_factor=PAUSE_S / 0.1,
            reset_timeout_s=60.0, focus_check=focus_check, reset_hook=hook,
            lock_keeper=reset.LockKeeper(), reward_fn=FightReward(),
            encounter=Encounter(deathblows_to_win=args.deathblows)), write)
        if args.resume:
            model = make_model(env, resume=args.resume, **(FINE_TUNE if args.fine_tune else {}))
        else:
            model = make_model(env, seed=args.seed, n_steps=args.n_steps)
        settings = {name: getattr(model, name) for name in DEFAULTS
                    if isinstance(getattr(model, name), (int, float))}
        settings["clip_range"] = model.clip_range(1.0)
        write({"kind": "meta", "time": time.time(), "pid": args.pid, "args": vars(args),
               "schema": SCHEMA_VERSION, "settings": settings,
               "net_arch": NET_ARCH, "python": sys.version.split()[0]})
        print(f"Run folder: {folder}")
        print("Windows asked not to sleep or turn the display off during the run: "
              + ("yes" if keep_awake() else "THE REQUEST FAILED"))
        if not wait_for_focus(focus_check):
            raise ResetFailed("the game window was not in front; nothing was sent")
        why = train(model, folder, args.steps, deadline=time.time() + args.hours * 3600,
                    keep_every=args.keep_every, value_warmup=args.value_warmup)
        write({"kind": "end", "time": time.time(), "why": why, "steps": model.num_timesteps,
               "reset_failures": hook.failures})
    except ResetFailed as error:
        print(f"Not started: {error}")
    finally:
        if env is not None:
            env.close()
        elif controller is not None:
            controller.close()
        if controller is not None:
            print("Inputs neutral at exit: " + ("yes" if controller.state == NEUTRAL else "NO"))
        reader.close()
        log.close()


if __name__ == "__main__":
    main()
