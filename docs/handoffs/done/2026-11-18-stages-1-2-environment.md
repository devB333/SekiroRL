# Handoff: Stages 1-2, waiting at gate G02

Written: 2026-11-18. Status: **all Stage 1-2 tasks completed; waiting for the user to approve G02.**

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
| `python -m unittest discover -s tests` | Windows Python 3.15 | 47 tests, all pass |
| Same | WSL Python 3.14 (no packages) | 45 pass, 2 skipped |
| `python .\read_health.py --help`, `py_compile` | Windows | Work as before the refactor |
| `python -m sekirorl.smoke_test --help` | Windows | Works |
| Smoke test `--dry-run --candidates` with a stubbed process object | Windows | Ran to the end; step lateness under 0.1 ms; inputs neutral at exit |
| `Reader` on a real Windows process, `foreground_window()` | Windows | Work (`module_base` correctly reports no `sekiro.exe`) |

### Checks that have NOT run

- Anything against the running game. Sekiro was not running.
- `read_health.py` sampling against the game since the refactor.
- The press-to-animation delay measurement (needs the game; uses an unverified chain).

### Packages (Windows Python 3.15), installed 2026-11-18

gymnasium 1.4.0, numpy 2.5.5, stable-baselines3 2.9.2, torch 2.15.0 (CPU build), vgamepad 0.2.0.
All 47 tests pass on Windows with no skips, including Stable-Baselines3 `check_env`.
`VigemController` connects a virtual pad and accepts button and stick states, so the ViGEmBus
driver is present. It has not been seen acting in the game. WSL still has no packages, so 2
tests skip there.

## Live run, 2026-11-18 (by the user)

- Executable SHA256 `637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856`, module
  base `0x140000000`. Player HP 320/320 through the refactored reader.
- Dry run with PowerShell focused: all steps invalid ("game window not in foreground"),
  truncated after 5. This is the focus check working, not a fault.
- Focused run: 31 steps, lateness max 0.01 ms, read about 0.1 ms, no overruns, no invalid steps.
  Attack press at +0.4 ms, release at +50.1 ms. Inputs neutral at exit.
- Press-to-animation delay: 56.9 ms, animation ID 790010 -> 790040 (candidate chain).
- Candidate chains all resolved with plausible values (still UNVERIFIED against behaviour):
  posture 120/120; position (160.3, -28.9, 22.4); animation ID 790010, elapsed 3.10 s;
  in-game time 9,763,418 ms; death count 5.

- User confirmed afterwards: the character attacked twice; `--hold-guard` made the character
  guard; `taskkill /F` on the Python process released guard. Sekiro accepts the virtual pad.

## Remaining for G02

All Stage 1 and Stage 2 tasks are completed. Waiting only for the user to approve G02 (and to
accept or change the 30-action list). On approval: record the date in `docs/plan.md` section
F, move this file to `done/`, and write a new `current.md` for Stage 3.

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
