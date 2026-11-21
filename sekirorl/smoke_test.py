"""Windows smoke test: read the live game, send one controller action, print what happened.

    python -m sekirorl.smoke_test --pid <sekiro pid>
    python -m sekirorl.smoke_test --pid <sekiro pid> --dry-run        (no controller output)
    python -m sekirorl.smoke_test --pid <sekiro pid> --candidates     (also show unverified reads)
    python -m sekirorl.smoke_test --pid <sekiro pid> --hold-guard 30  (hard-kill release test)

Load a save first. During the countdown, click the game window so it has focus.
Reads are query/read only; the only output is a virtual Xbox 360 pad.
"""
import argparse
import hashlib
import struct
import sys
import time

from . import actions, memory
from .clock import RealClock
from .controller import NEUTRAL, ControlState, RecordingController, VigemController
from .loop import ResetFailed, StepLoop
from .snapshot import OBS_NAMES
from .winutil import focus_check_for, foreground_window

HP = OBS_NAMES.index("player_hp_frac")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(index, observation, reward, info):
    timing = info.get("timing", {})
    hp = "unavailable" if info["player_hp"] is None else f'{info["player_hp"]}/{info["player_max_hp"]}'
    line = (f'{index:>4}  {info.get("action", "reset"):<18} hp={hp:<11} '
            f'obs_hp={observation[HP]:.3f} mask={observation[len(OBS_NAMES) + HP]:.0f} '
            f'reward={reward:+.3f}')
    if timing:
        line += (f'  wake_late={timing["wake_late_s"] * 1000:6.2f}ms'
                 f' apply_late={timing["apply_late_s"] * 1000:6.2f}ms'
                 f' read={timing["read_s"] * 1000:5.2f}ms'
                 f'{" OVERRUN" if timing["overrun"] else ""}')
    if not info["valid"]:
        line += f'  INVALID: {info["reason"]}'
    return line


def measure_press_latency(reader, base, controller, button, timeout_s=1.0):
    """Time from a button press to the player's animation ID changing.

    Uses an UNVERIFIED candidate chain, so the figure is provisional.
    """
    root, offsets, fmt, _ = memory.CHAINS["player_anim_id_elapsed"]
    try:
        before = reader.read_chain(base + root, offsets, fmt)[0]
    except memory.Unavailable as error:
        return f"not measured: animation chain unavailable ({error})"
    start = time.perf_counter()
    controller.apply(ControlState(buttons=frozenset({button})))
    released, result = False, None
    while time.perf_counter() - start < timeout_s:
        elapsed = time.perf_counter() - start
        if not released and elapsed >= 0.05:
            controller.neutral()
            released = True
        try:
            current = reader.read_chain(base + root, offsets, fmt)[0]
        except memory.Unavailable:
            continue
        if current != before:
            result = (f"{elapsed * 1000:.1f} ms (animation ID {before} -> {current}; "
                      f"candidate chain, unverified)")
            break
    controller.neutral()
    return result or f"no animation ID change within {timeout_s} s (ID stayed {before})"


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--action", default="stay+attack", choices=actions.ACTION_NAMES,
                        metavar="NAME", help="action to send once (default stay+attack)")
    parser.add_argument("--interval", type=float, default=0.1, help="step interval in seconds")
    parser.add_argument("--warmup", type=int, default=20, help="no-op steps before the action")
    parser.add_argument("--after", type=int, default=10, help="no-op steps after the action")
    parser.add_argument("--countdown", type=float, default=5.0,
                        help="seconds to wait so you can focus the game window")
    parser.add_argument("--dry-run", action="store_true",
                        help="record controller output instead of sending it")
    parser.add_argument("--candidates", action="store_true",
                        help="also print unverified candidate reads and press-to-animation delay")
    parser.add_argument("--hold-guard", type=float, default=0.0, metavar="SECONDS",
                        help="instead of stepping, hold guard this long so you can kill "
                             "this process and see whether the game releases guard")
    parser.add_argument("--no-focus-check", action="store_true",
                        help="send input even when the game window is not in the foreground")
    args = parser.parse_args()

    print(f"Python {sys.version.split()[0]}, {struct.calcsize('P') * 8}-bit")
    reader = memory.Reader(args.pid)
    controller = None
    loop = None
    try:
        base = reader.module_base()
        path = reader.image_path()
        print(f"Executable: {path}")
        print(f"SHA256:     {sha256_of(path)}")
        print(f"Module base: {base:#x}")
        source = memory.MemoryStateSource(reader, base)
        first = source.read()
        print("Player HP:  " + (f"{first.player_hp}/{first.player_max_hp}" if first.valid
                                else f"unavailable ({first.reason})"))
        if args.candidates:
            print("Candidate reads (UNVERIFIED, not used as observations):")
            for name, value in source.probe_candidates().items():
                print(f"  {name}: {value}")

        controller = RecordingController() if args.dry_run else VigemController()
        print("Controller: " + ("dry run, nothing is sent" if args.dry_run
                                else "virtual Xbox 360 pad connected"))
        if args.countdown > 0:
            print(f"Focus the game window now ({args.countdown:.0f} s)...")
            time.sleep(args.countdown)
        pid, title = foreground_window()
        print(f'Foreground window: "{title}" (pid {pid}) -> '
              + ("game has focus" if pid == args.pid else "GAME DOES NOT HAVE FOCUS"))

        if args.hold_guard > 0:
            import os
            controller.apply(ControlState(buttons=frozenset({"guard"})))
            print(f"Holding guard for {args.hold_guard:.0f} s. To test a hard kill, run in "
                  f"another window:  taskkill /F /PID {os.getpid()}")
            time.sleep(args.hold_guard)
            print("Not killed; releasing guard normally.")
            return

        loop = StepLoop(source, controller, RealClock(), step_interval=args.interval,
                        max_steps=args.warmup + args.after + 10,
                        watchdog_s=3 * args.interval, reset_timeout_s=10.0,
                        focus_check=None if args.no_focus_check else focus_check_for(args.pid))
        observation, info = loop.reset()
        print(describe(0, observation, 0.0, info))
        infos = []
        plan = [actions.NOOP] * args.warmup + [actions.action_index(args.action)] \
            + [actions.NOOP] * args.after
        for index, action in enumerate(plan, start=1):
            observation, reward, _, truncated, info = loop.step(action)
            infos.append(info)
            print(describe(index, observation, reward, info))
            if action != actions.NOOP:
                for offset, state in info["applied"]:
                    print(f"        +{offset * 1000:6.2f} ms  buttons={sorted(state.buttons)} "
                          f"stick={state.stick}")
            if truncated:
                print(f'Truncated: {info["truncation_reason"]}')
                break

        late = sorted(i["timing"]["wake_late_s"] * 1000 for i in infos)
        if late:
            print(f"Step lateness over {len(late)} steps: median {late[len(late) // 2]:.2f} ms, "
                  f"max {late[-1]:.2f} ms; overruns "
                  f'{sum(i["timing"]["overrun"] for i in infos)}; '
                  f'invalid steps {sum(not i["valid"] for i in infos)}')

        if args.candidates and not args.dry_run:
            button = actions.BUTTON_MODES[actions.ACTIONS[actions.action_index(args.action)][1]][1]
            if button:
                time.sleep(1.0)
                print("Press-to-animation delay: "
                      + measure_press_latency(reader, base, controller, button))
    except ResetFailed as error:
        print(f"Could not start: {error}. Is a save loaded?")
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


if __name__ == "__main__":
    main()
