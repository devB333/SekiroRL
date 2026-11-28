"""Behaviour cloning: teach the PPO policy to copy recorded play.

    python -m sekirorl.clone demos/<file>.jsonl [more files] --name clone-1

Reads recordings made by `sekirorl.demos record`, cuts them into fights and
labelled steps (`demos.fights`), and trains the same network `train.py` uses
to pick the user's action in each step. The result is saved as
`models/<name>/last.zip`, which `train.py --resume` continues with PPO.

What is fitted:
- the action head, by cross-entropy against the labels. Rare actions weigh
  more (`class_power`), because most steps are "do nothing";
- the value head, toward the discounted reward that followed each step in the
  recording, so PPO does not start with a value estimate of noise.

Every `holdout_every`-th fight is kept out of training and used to pick the
best epoch and to report how well the copy matches. Each recording is cut
several times with the step grid shifted (`phases`); a fight is in the same
group at every shift.

A high match here does not mean good play: the copy sees only the recorded
situations, and one early mistake live takes it to ones it never saw.
"""
import argparse
import json
import os
import time

import gymnasium as gym
from gymnasium import spaces
import numpy as np
import torch

from . import actions, demos, snapshot
from .train import DEFAULTS, make_model

PHASES = (0.0, 0.025, 0.05, 0.075)


class SpacesOnly(gym.Env):
    """An environment that only states the spaces, to build a model without the game."""

    def __init__(self):
        self.observation_space = spaces.Box(snapshot.OBS_LOW, snapshot.OBS_HIGH,
                                            shape=(snapshot.OBS_SIZE,), dtype=np.float32)
        self.action_space = spaces.Discrete(actions.NUM_ACTIONS)

    def reset(self, *, seed=None, options=None):
        raise RuntimeError("SpacesOnly cannot be played")

    def step(self, action):
        raise RuntimeError("SpacesOnly cannot be played")


def returns(fight, gamma):
    """Discounted reward from each step of a fight to its end."""
    out, total = [], 0.0
    for step in reversed(fight.steps):
        total = step.reward + gamma * total
        out.append(total)
    return out[::-1]


def dataset(recordings, phases=PHASES, holdout_every=5, gamma=DEFAULTS["gamma"], **cut):
    """Training and held-out arrays from loaded recordings.

    Returns (train, held), each a dict of numpy arrays: `obs`, `action`,
    `value`. Fights that ended with the recording are used like the others.
    """
    groups = {"train": ([], [], []), "held": ([], [], [])}
    counts = {"train": 0, "held": 0}
    number = 0
    for recording in recordings:
        first = demos.fights(recording, phase=phases[0], **cut)
        held_starts = []
        for fight in first:
            number += 1
            held = holdout_every and number % holdout_every == 0
            counts["held" if held else "train"] += 1
            if held:
                held_starts.append(fight.start_t)
        for phase in phases:
            found = first if phase == phases[0] else demos.fights(recording, phase=phase, **cut)
            for fight in found:
                held = any(abs(fight.start_t - start) < 0.5 for start in held_starts)
                obs, labels, values = groups["held" if held else "train"]
                for step, value in zip(fight.steps, returns(fight, gamma)):
                    if step.usable:
                        obs.append(snapshot.encode(step.snapshot))
                        labels.append(step.action)
                        values.append(value)

    def arrays(group):
        obs, labels, values = group
        return {"obs": np.asarray(obs, dtype=np.float32).reshape(-1, snapshot.OBS_SIZE),
                "action": np.asarray(labels, dtype=np.int64),
                "value": np.asarray(values, dtype=np.float32)}

    train, held = arrays(groups["train"]), arrays(groups["held"])
    train["fights"], held["fights"] = counts["train"], counts["held"]
    return train, held


def class_weights(labels, power=0.5, low=0.25, high=8.0):
    """One weight per action: (mean count / its count) ** power, clipped."""
    counts = np.bincount(labels, minlength=actions.NUM_ACTIONS).astype(np.float64)
    seen = counts > 0
    weights = np.ones(actions.NUM_ACTIONS)
    weights[seen] = np.clip((counts[seen].mean() / counts[seen]) ** power, low, high)
    return weights.astype(np.float32)


def _losses(policy, obs, labels, values, weights):
    predicted, log_prob, _ = policy.evaluate_actions(obs, labels)
    weight = weights[labels]
    action_loss = -(log_prob * weight).sum() / weight.sum()
    value_loss = torch.nn.functional.mse_loss(predicted.flatten(), values)
    return action_loss, value_loss


def predict(policy, obs, batch=4096):
    """Most likely action for each observation."""
    out = []
    with torch.no_grad():
        for start in range(0, len(obs), batch):
            piece = torch.as_tensor(obs[start:start + batch])
            out.append(policy.get_distribution(piece).distribution.logits.argmax(dim=1).numpy())
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int64)


def match(policy, data):
    """How the copy's most likely action compares with the labels of `data`."""
    labels = data["action"]
    if not len(labels):
        return {"steps": 0}
    chosen = predict(policy, data["obs"])
    moves = len(actions.MOVEMENTS)
    label_mode, chosen_mode = labels // moves, chosen // moves
    report = {"steps": int(len(labels)),
              "same_action": float((chosen == labels).mean()),
              "same_buttons": float((chosen_mode == label_mode).mean()),
              "same_movement": float((chosen % moves == labels % moves).mean()),
              "buttons": {}}
    for index, (name, _, _) in enumerate(actions.BUTTON_MODES):
        wanted = label_mode == index
        picked = chosen_mode == index
        if wanted.any() or picked.any():
            report["buttons"][name] = {
                "labelled": int(wanted.sum()), "chosen": int(picked.sum()),
                "found": float((picked & wanted).sum() / wanted.sum()) if wanted.any() else None}
    return report


def clone(model, train, held, epochs=30, batch_size=256, learning_rate=1e-3,
          value_weight=0.5, class_power=0.5, seed=0, out=print):
    """Fit `model.policy` to `train`; keep the epoch that is best on `held`
    (on `train` when nothing is held out). Returns the history of losses."""
    policy = model.policy
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    weights = torch.as_tensor(class_weights(train["action"], class_power))
    optimizer = torch.optim.Adam(policy.parameters(), lr=learning_rate)
    tensors = {name: torch.as_tensor(train[name]) for name in ("obs", "action", "value")}
    check = held if len(held["action"]) else train
    check_tensors = {name: torch.as_tensor(check[name]) for name in ("obs", "action", "value")}
    history, best, best_state = [], None, None
    for epoch in range(1, epochs + 1):
        policy.set_training_mode(True)
        order = rng.permutation(len(train["action"]))
        total = 0.0
        for start in range(0, len(order), batch_size):
            rows = torch.as_tensor(order[start:start + batch_size])
            action_loss, value_loss = _losses(policy, tensors["obs"][rows],
                                              tensors["action"][rows],
                                              tensors["value"][rows], weights)
            optimizer.zero_grad()
            (action_loss + value_weight * value_loss).backward()
            optimizer.step()
            total += action_loss.item() * len(rows)
        policy.set_training_mode(False)
        with torch.no_grad():
            action_loss, value_loss = _losses(policy, check_tensors["obs"],
                                              check_tensors["action"],
                                              check_tensors["value"], weights)
        row = {"epoch": epoch, "train_loss": total / len(order),
               "check_loss": float(action_loss), "check_value_loss": float(value_loss)}
        history.append(row)
        if best is None or row["check_loss"] < best:
            best = row["check_loss"]
            best_state = {name: value.clone() for name, value in policy.state_dict().items()}
        out(f"epoch {epoch:3d}: training loss {row['train_loss']:.4f}, "
            f"held-out loss {row['check_loss']:.4f}")
    policy.load_state_dict(best_state)
    return history


def print_match(title, report, out=print):
    if not report["steps"]:
        out(f"{title}: no steps")
        return
    out(f"{title}: {report['steps']} steps; same action {100 * report['same_action']:.1f}%, "
        f"same buttons {100 * report['same_buttons']:.1f}%, "
        f"same movement {100 * report['same_movement']:.1f}%")
    for name, row in report["buttons"].items():
        found = "-" if row["found"] is None else f"{100 * row['found']:.0f}%"
        out(f"    {name:11s} in the recording {row['labelled']:6d}, found by the copy {found:>4s}, "
            f"chosen by the copy {row['chosen']:6d}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", nargs="+")
    parser.add_argument("--name", default=None, help="folder under models/")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--holdout-every", type=int, default=5,
                        help="keep every Nth fight out of training (0: none)")
    parser.add_argument("--class-power", type=float, default=0.5,
                        help="0: every step weighs the same; 1: every action weighs the same")
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    recordings = [demos.load(path) for path in args.recordings]
    train, held = dataset(recordings, holdout_every=args.holdout_every)
    print(f"{train['fights']} fights to learn from ({len(train['action'])} steps over "
          f"{len(PHASES)} shifts of the step grid), {held['fights']} held out "
          f"({len(held['action'])} steps)")
    if not len(train["action"]):
        parser.exit(1, "No usable steps in these recordings.\n")
    model = make_model(SpacesOnly(), seed=args.seed)
    history = clone(model, train, held, epochs=args.epochs, class_power=args.class_power,
                    seed=args.seed)
    folder = os.path.join("models", args.name or time.strftime("clone-%Y%m%d-%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    model.save(os.path.join(folder, "last"))
    reports = {"train": match(model.policy, train), "held": match(model.policy, held)}
    print_match("Fights it learned from", reports["train"])
    print_match("Fights it never saw", reports["held"])
    with open(os.path.join(folder, "clone.json"), "w", encoding="utf-8") as handle:
        json.dump({"time": time.time(), "recordings": args.recordings, "args": vars(args),
                   "schema": snapshot.SCHEMA_VERSION, "history": history, "match": reports},
                  handle, indent=1)
    print(f"Saved {os.path.join(folder, 'last.zip')}")


if __name__ == "__main__":
    main()
