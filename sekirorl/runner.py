"""Run the step loop with the encounter rules against the live game.

    python -m sekirorl.runner --pid <sekiro pid>
        Dry run: nothing is sent to the game. Start the rematch and fight by
        hand; this reports when the fight was found to be running, whether
        every step was valid, and how the episode ended.
    python -m sekirorl.runner --pid <sekiro pid> --send --policy deflect
        Sends controller input through the virtual pad. `deflect` presses guard
        once for the boss's opening attack and does nothing else (scripted.py);
        `random` picks any action.
    python -m sekirorl.runner --pid <sekiro pid> --send --auto-reset --policy model \
            --model models/<run>/last.zip --episodes 5
        Lets a saved model play (from `train.py` or `clone.py`). It takes the
        most likely action in every step; `--sample` draws actions the way
        training does.
    python -m sekirorl.runner --pid <sekiro pid> --send --auto-reset --episodes 20
        Also drives the idol menu between episodes (see reset.py).

Every occurrence of the opening attack is scored as hit, block or deflect,
whoever is playing. Every snapshot read and every step is written to a JSONL
log under `logs/` (ignored by git). Memory access is query/read only.
"""
import argparse
import dataclasses
import json
import os
import random
import sys
import time

from . import actions, memory, reset, scripted
from .clock import RealClock
from .controller import NEUTRAL, RecordingController, VigemController
from .encounter import INACTIVE_ANIMATION, PLAYER_INTRO_ANIMATIONS, Encounter
from .loop import FightReward, ResetFailed, StepLoop
from .winutil import focus_check_for, keep_awake


def describe(snapshot):
    """Short text for what a snapshot shows; printed when it changes."""
    if not snapshot.valid:
        return "no player (loading screen or no save loaded)"
    parts = ["player dead" if snapshot.player_hp == 0 else "player alive"]
    if snapshot.player_anim_id in PLAYER_INTRO_ANIMATIONS:
        parts.append("no control")
    elif snapshot.player_anim_id == reset.SITTING:
        parts.append("sitting at the idol")
    if snapshot.boss_hp is None:
        parts.append("boss not loaded")
    elif snapshot.boss_anim_id in (None, INACTIVE_ANIMATION):
        parts.append("boss loaded, not active")
    else:
        parts.append("boss active")
    return ", ".join(parts)


class LoggingSource:
    """Passes `read()` through, writes every snapshot to the log and prints
    one line when the situation changes."""

    def __init__(self, source, write, out=print):
        self.source = source
        self.write = write
        self.out = out
        self.start = time.perf_counter()
        self.boss_loaded_at = None      # perf_counter time the boss last appeared
        self.last = None
        self._described = None

    def read(self):
        snapshot = self.source.read()
        now = time.perf_counter()
        if snapshot.boss_hp is not None and (self.last is None or self.last.boss_hp is None):
            self.boss_loaded_at = now
        self.last = snapshot
        row = dataclasses.asdict(snapshot)
        row["kind"] = "snapshot"
        self.write(row)
        text = describe(snapshot)
        if text != self._described:
            self._described = text
            self.out(f"[{now - self.start:7.1f} s] {text}")
        return snapshot


def run_episode(loop, source, policy, write, out=print, watch=None):
    """One reset and one episode. Returns a summary dict.

    `policy(snapshot)` returns the action for the next step. `watch(snapshot)`
    is called with every valid step's snapshot and may return a line to print.
    """
    asked = time.perf_counter()
    loop.reset()
    started = time.perf_counter()
    first = source.last
    line = f"Fight running after {started - asked:.1f} s of waiting"
    if source.boss_loaded_at is not None and source.boss_loaded_at >= asked:
        line += f", {started - source.boss_loaded_at:.1f} s after the boss loaded"
    out(line + f". Player HP {first.player_hp}/{first.player_max_hp}, "
        f"boss HP {first.boss_hp}/{first.boss_max_hp}.")

    steps = invalid = lock_presses = unlocked = 0
    total_reward = 0.0
    reasons = {}
    while True:
        action = policy(source.last)
        _, reward, terminated, truncated, info = loop.step(action)
        steps += 1
        total_reward += reward
        snapshot = source.last
        write({"kind": "step", "step": steps, "action": info["action"], "reward": reward,
               "valid": info["valid"], "reason": info["reason"], "timing": info["timing"],
               "encounter": info.get("encounter"), "lock_pressed": info.get("lock_pressed"),
               "applied": [[offset, sorted(state.buttons), list(state.stick)]
                           for offset, state in info["applied"]]})
        lock_presses += bool(info.get("lock_pressed"))
        unlocked += info["valid"] and snapshot.locked_on is False
        if not info["valid"]:
            invalid += 1
            if info["reason"] not in reasons:
                out(f"  step {steps}: INVALID: {info['reason']}")
            reasons[info["reason"]] = reasons.get(info["reason"], 0) + 1
        elif info.get("encounter", {}).get("deathblow"):
            out(f"  step {steps}: deathblow (boss HP {snapshot.boss_hp}, "
                f"posture {snapshot.boss_posture})")
        if info["valid"] and watch is not None:
            seen = watch(snapshot)
            if seen:
                out(f"  step {steps}: {seen}")
                write({"kind": "watch", "step": steps, "message": seen})
        if steps % 50 == 0:
            out(f"  step {steps}: player HP {snapshot.player_hp}, boss HP {snapshot.boss_hp}, "
                f"boss posture {snapshot.boss_posture}/{snapshot.boss_max_posture}")
        if terminated or truncated:
            break

    summary = info["timing_summary"]
    ending = (f"terminated: {info['termination_reason']}" if terminated
              else f"truncated: {info['truncation_reason']}")
    out(f"Episode over after {steps} steps ({steps * loop.dt:.1f} s), {ending}. "
        f"Reward {total_reward:+.3f}. Invalid steps: {invalid}"
        + (f" {reasons}" if reasons else "") + ".")
    out(f"  player HP {snapshot.player_hp}, boss HP {snapshot.boss_hp}/{snapshot.boss_max_hp}, "
        f"death counter {snapshot.death_count}; "
        f"wake lateness p50 {summary['wake_late_s']['p50'] * 1000:.2f} ms, "
        f"max {summary['wake_late_s']['max'] * 1000:.2f} ms; overruns {summary['overruns']}")
    keeper = loop.lock_keeper
    if keeper is not None:
        out(f"  lock-on: {lock_presses} press(es), {unlocked} of {steps} steps not locked on the boss"
            + ("; GAVE UP: the lock never read as on" if keeper.gave_up else ""))
    result = {"kind": "episode", "steps": steps, "invalid": invalid, "reasons": reasons,
              "lock_presses": lock_presses, "steps_unlocked": unlocked,
              "reward": total_reward, "terminated": terminated, "truncated": truncated,
              "ending": info.get("termination_reason") or info.get("truncation_reason"),
              "timing_summary": summary}
    write(result)
    return result


def model_policy(path, sample=False):
    """`policy(snapshot)` from a saved Stable-Baselines3 model."""
    import numpy as np
    from stable_baselines3 import PPO
    from .snapshot import encode
    model = PPO.load(path, device="cpu")

    def policy(snapshot):
        observation = np.asarray(encode(snapshot), dtype=np.float32)
        action, _ = model.predict(observation, deterministic=not sample)
        return int(action)
    return policy


def wait_for_focus(focus_check, out=print, timeout_s=120.0, settle_s=3.0, sleep=time.sleep):
    """Before the reset sends anything: wait until the game window is in
    front, then `settle_s` more. Returns False if that never happened."""
    waited = 0.0
    if not focus_check():
        out(f"Click the game window; waiting up to {timeout_s:.0f} s for it to be in front.")
    while not focus_check():
        if waited >= timeout_s:
            return False
        sleep(0.5)
        waited += 0.5
    out(f"Game window in front. Hands off the pad; input starts in {settle_s:.0f} s.")
    sleep(settle_s)
    return focus_check()


def default_log_path():
    stamp = time.localtime()
    return os.path.join("logs", time.strftime("%Y-%m-%d", stamp),
                        time.strftime("run-%H%M%S.jsonl", stamp))


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--episodes", type=int, default=1)
    parser.add_argument("--policy", choices=("noop", "deflect", "random", "model"),
                        default="noop")
    parser.add_argument("--model", default=None, help="saved model for --policy model")
    parser.add_argument("--sample", action="store_true",
                        help="with --policy model: draw actions as training does instead of "
                             "taking the most likely one")
    parser.add_argument("--deflect-from", type=float, default=0.58, metavar="SECONDS",
                        help="deflect policy: press guard on the first step that sees the "
                             "opening attack at or past this animation time (default 0.58; "
                             "the hit lands at 0.775)")
    parser.add_argument("--seed", type=int, default=0, help="seed of the random policy")
    parser.add_argument("--send", action="store_true",
                        help="send controller input (default: dry run, nothing is sent)")
    parser.add_argument("--auto-reset", action="store_true",
                        help="drive the idol menu between episodes; needs --send")
    parser.add_argument("--deathblows", type=int, default=2, metavar="N",
                        help="deathblows that end the episode as a victory (default 2: both "
                             "health bars of the first Genichiro; the third bar is another "
                             "character and cannot be read yet)")
    parser.add_argument("--no-lock-on", action="store_true",
                        help="with --send, do not keep lock-on (R3) on the boss")
    parser.add_argument("--interval", type=float, default=0.1, help="step interval in seconds")
    parser.add_argument("--max-steps", type=int, default=3000)
    parser.add_argument("--wait", type=float, default=600.0,
                        help="seconds to wait for a fight to start before giving up")
    parser.add_argument("--log", default=None, help="JSONL log path (default under logs/)")
    args = parser.parse_args()
    if args.auto_reset and not args.send:
        parser.error("--auto-reset needs --send")
    if args.auto_reset and reset.GENICHIRO_REMATCH is None:
        parser.error("the reset inputs are not written yet (reset.GENICHIRO_REMATCH)")
    if args.policy != "noop" and not args.send:
        parser.error(f"--policy {args.policy} does nothing in a dry run; add --send")
    if args.policy == "model" and not args.model:
        parser.error("--policy model needs --model <path>")

    try:
        reader = memory.Reader(args.pid)
    except OSError as error:
        parser.exit(1, f"Cannot open process {args.pid}: {error}\n"
                       f"Find the PID with: tasklist /FI \"IMAGENAME eq sekiro.exe\"\n")
    path = args.log or default_log_path()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    log = open(path, "w", encoding="utf-8")

    def write(row):
        log.write(json.dumps(row) + "\n")

    rng = random.Random(args.seed)
    deflect = scripted.DeflectPolicy(press_from=args.deflect_from)
    if args.policy == "model":
        policy = model_policy(args.model, sample=args.sample)
    else:
        policy = {"noop": lambda snapshot: actions.NOOP,
                  "deflect": deflect,
                  "random": lambda snapshot: rng.randrange(actions.NUM_ACTIONS)}[args.policy]
    outcomes = scripted.AttackOutcomes()

    def watch(snapshot):
        outcome = outcomes.update(snapshot)
        if outcome is None:
            return None
        text = f"opening attack: {outcome}"
        if args.policy == "deflect" and len(deflect.presses) >= len(outcomes.outcomes):
            text += f" (guard pressed on the sample at {deflect.presses[-1]:.2f} s)"
        return text

    loop = None
    controller = None
    results = []
    try:
        base = reader.module_base()
        write({"kind": "meta", "pid": args.pid, "base": hex(base), "time": time.time(),
               "python": sys.version.split()[0], "args": vars(args)})
        source = LoggingSource(memory.MemoryStateSource(reader, base, boss=memory.GENICHIRO),
                               write)
        controller = VigemController() if args.send else RecordingController()
        clock = RealClock()
        focus_check = focus_check_for(args.pid)
        hook = None
        if args.auto_reset:
            hook = reset.RematchReset(source, controller, clock, reset.GENICHIRO_REMATCH,
                                      focus_check=focus_check)
        keeper = None
        if args.send and not args.no_lock_on:
            keeper = reset.LockKeeper()
        loop = StepLoop(source, controller, clock, step_interval=args.interval,
                        max_steps=args.max_steps, watchdog_s=3 * args.interval,
                        reset_timeout_s=args.wait, focus_check=focus_check,
                        reset_hook=hook, lock_keeper=keeper, reward_fn=FightReward(),
                        encounter=Encounter(deathblows_to_win=args.deathblows))
        print(f"Log: {path}")
        print("Controller: " + ("virtual Xbox 360 pad, INPUT IS SENT" if args.send
                                else "dry run, nothing is sent"))
        if not args.auto_reset:
            print("Start the rematch by hand; keep the game window focused.")
        else:
            keep_awake()
            if not wait_for_focus(focus_check):
                raise ResetFailed("the game window was not in front; nothing was sent")
        for episode in range(1, args.episodes + 1):
            print(f"--- Episode {episode} of {args.episodes}: waiting for the fight ---")
            try:
                results.append(run_episode(loop, source, policy, write, watch=watch))
            finally:
                if hook is not None:
                    for seconds, message in hook.log:
                        write({"kind": "reset", "t": seconds, "message": message})
    except ResetFailed as error:
        print(f"Reset failed: {error}")
        if loop is not None and loop.reset_hook is not None:
            for seconds, message in loop.reset_hook.log:
                print(f"  reset {seconds:6.1f} s  {message}")
    except KeyboardInterrupt:
        print("Interrupted.")
    finally:
        if loop is not None:
            loop.close()
        elif controller is not None:
            controller.close()
        if controller is not None:
            print("Inputs neutral at exit: " + ("yes" if controller.state == NEUTRAL else "NO"))
        reader.close()
        log.close()
    if results:
        endings = {}
        for result in results:
            endings[result["ending"]] = endings.get(result["ending"], 0) + 1
        print(f"{len(results)} episode(s): {endings}; "
              f"invalid steps {sum(r['invalid'] for r in results)} of "
              f"{sum(r['steps'] for r in results)}")
    if outcomes.outcomes:
        tally = {name: outcomes.outcomes.count(name) for name in sorted(set(outcomes.outcomes))}
        print(f"Opening attack (boss animation {outcomes.animation}): {tally}")


if __name__ == "__main__":
    main()
