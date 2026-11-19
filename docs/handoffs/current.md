# Handoff: Stages 1-2, waiting at gate G02

Written: 2026-11-18. Status: **agent work done; waiting on the user for package install, the
live smoke run and gate approval.**

How this folder works: `current.md` describes the task in progress. When a gate is approved,
move this file to `done/` (named `YYYY-MM-DD-short-title.md`) and write a new `current.md`.

## Read first

1. `AGENTS.md` - project rules, including the read-only memory contract, and all commands.
2. `docs/plan.md` - decisions, staged tasks, gates, and the report format (section C).
3. `Plan.md` (repo root) - feasibility report and the memory pointer list.

## Where things stand

Stages 1 and 2 of `docs/plan.md` are implemented as far as they can be without the Python
packages and the running game. **Do not start Stage 3 until the user approves G02.**

### Checks that have run

| Check | Where | Result |
|---|---|---|
| `python -m unittest discover -s tests` | Windows Python 3.15 and WSL Python 3.14 | 47 tests: 45 pass, 2 skipped |
| `python .\read_health.py --help`, `py_compile` | Windows | Work as before the refactor |
| `python -m sekirorl.smoke_test --help` | Windows | Works |
| Smoke test `--dry-run --candidates` with a stubbed process object | Windows | Ran to the end; step lateness under 0.1 ms; inputs neutral at exit |
| `Reader` on a real Windows process, `foreground_window()` | Windows | Work (`module_base` correctly reports no `sekiro.exe`) |

### Checks that have NOT run

- Anything against the running game. Sekiro was not running.
- Stable-Baselines3 `check_env`: the package is not installed, so that one test skips.
  (Gymnasium 1.4.0 and numpy 2.5.5 are now installed on Windows; the wrapper test and
  Gymnasium's own `check_env` pass. Windows test count is now 46 pass, 1 skipped.)
- `VigemController` has never been constructed: `vgamepad` is not installed. Its calls
  (`reset`, `press_button`, `left_joystick_float`, `update`) are from memory of the library's
  documentation.
- `read_health.py` sampling against the game since the refactor.
- The press-to-animation delay measurement (needs the game; uses an unverified chain).

## Remaining for G02

1. **User:** install the remaining packages on Windows (`stable-baselines3`, `torch`,
   `vgamepad`; Gymnasium is done): `python -m pip install -r requirements.txt`, and
   the ViGEmBus driver. Windows Python is 3.15; if PyTorch has no build for it, use an older
   Python (unchecked).
2. Re-run the tests on Windows; expect 47 pass with no skips. Fix anything `check_env` raises.
   (Plan task S02-T03, S01-T06.)
3. **User, game running with a save loaded** (plan task S02-T05):
   - `python -m sekirorl.smoke_test --pid <pid> --dry-run`
   - `python -m sekirorl.smoke_test --pid <pid> --candidates`
   - Hold-guard kill test: does the pad release when the Python process is killed?
4. Record the results in `docs/plan.md` (task statuses and section F), then give the G02 report.

The G02 user checklist is in `docs/plan.md` under "Gate G02".

## Code map

| File | Contents |
|---|---|
| `read_health.py` | Thin wrapper over `sekirorl.memory` keeping the old command line. |
| `sekirorl/memory.py` | `Reader`, `CHAINS` (live / candidate), `read_chain`, `image_path`, `MemoryStateSource` (fills only player HP), `probe_candidates`. |
| `sekirorl/snapshot.py` | `Snapshot` (`None` = unavailable); `encode` gives 8 values + 8 validity bits; schema `v1-scalars`. |
| `sekirorl/controller.py` | `ControlState`, `RecordingController`, `VigemController`. |
| `sekirorl/actions.py` | 30 actions and per-step press/release schedules. |
| `sekirorl/clock.py` | `RealClock`. |
| `sekirorl/loop.py` | `StepLoop`: timing, validity, reward, truncation, watchdog. No Gymnasium import. |
| `sekirorl/env.py` | `SekiroEnv` Gymnasium wrapper. |
| `sekirorl/fake.py` | `FakeGame`: state source, controller and clock in one. |
| `sekirorl/winutil.py` | Foreground-window query and focus check. |
| `sekirorl/smoke_test.py` | Live Windows check. |
| `tests/` | `test_memory.py` (13), `test_loop.py` (32), `test_env.py` (2, skip without packages). |

## Behaviour worth knowing

- `terminated` is always false. Episodes end by truncation: step limit, 5 consecutive invalid
  reads, or a gap of more than twice the step interval between steps.
- Only `player_hp_frac` has its validity bit on. Posture, animation, boss and distance fields
  are in the layout but masked until Stage 3 verifies them.
- In-game time is used as a liveness check only when a snapshot carries it; the live reader
  does not yet, so a frozen game is not detected at this stage.
- The smoke test refuses to send input unless the game window is in the foreground
  (`--no-focus-check` overrides).

## Environment facts

- The repo is edited from WSL but runs under 64-bit Windows Python (`python.exe` works from WSL).
- WSL Python has no `pip` or `venv`; downloading a bootstrap installer was blocked by the
  permission system. Do not retry; packages go on the Windows side.
- `session-01.jsonl` is UTF-16 and 1.3 MB; do not read it whole. Do not open `PointerMaps/`.
- Not a git repository.

## Next

Stage 3 (memory fields) after G02 is approved. The user plans to provide a Cheat Engine MCP
connection. Read-only use only: value scans, memory viewing, pointer scans.
