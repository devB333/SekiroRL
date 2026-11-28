# Repository Guidelines

## Project Structure & Module Organization

This project provides a read-only external Sekiro health reader for Windows, intended for reinforcement learning observations.

- `sekirorl/memory.py` contains the `ctypes` Windows API wrapper, pointer resolution, the character and collection layout, the status table (each field group marked live or candidate), the boss lookup by map entity ID and NPC ID, sampling, and the command-line entry point. `read_health.py` is a thin wrapper that keeps the documented command line.
- `sekirorl/snapshot.py` defines the raw `Snapshot` and the fixed observation layout (values, one-hot animation IDs, validity bits). The animation vocabulary is `sekirorl/data/animations-genichiro.json`, built by `tools/build_vocabulary.py`; rebuilding it changes the layout and the schema version.
- `sekirorl/controller.py`, `actions.py`, `clock.py` and `loop.py` hold controller output, the discrete action set with press/release schedules, timing, and the fixed-rate step loop. `sekirorl/encounter.py` holds the fight rules (fight started, player dead, boss deathblow) that the step loop uses to start and terminate episodes. `sekirorl/reset.py` is the automatic reset through the idol menu and the lock-on keeper; `sekirorl/scripted.py` holds hand-written policies (the scripted deflect). `sekirorl/env.py` is the Gymnasium wrapper and `sekirorl/train.py` trains PPO (Stable-Baselines3) on the live rematch, saving under `models/` (ignored by git); `sekirorl/pad.py` reads a physical controller (XInput, or a DualSense through its HID input reports; reading only) and `sekirorl/demos.py` records the user's own play (pad and snapshots, under `demos/`, ignored by git), labels it with the agent's actions and cuts it into fights; `sekirorl/clone.py` trains the PPO policy to copy such recordings (behaviour cloning); `sekirorl/fake.py` holds the simulated games used by tests (`FakeGame`, and `FakeRematch` with the idol, menu, fight and death); `sekirorl/smoke_test.py` is the live Windows check and `sekirorl/runner.py` runs whole episodes against the live game (dry run unless `--send`).
- `tools/` holds two read-only recorders for live checks (`log_snapshots.py`, `record_rematch.py`); their logs go outside the repo.
- `tests/` holds standard-library `unittest` tests. `tests/data/` holds compact recordings from four live fights that the encounter, scripted-policy and observation tests replay.
- `logs/` (ignored by git) holds raw session logs. `docs/plan.md` is the staged roadmap with gates; `docs/handoffs/current.md` is the state of the task in progress.
- `MEMORY_READER.md` documents candidate 1.06 offsets, sources, discovery procedures, and live validation. `Plan.md` is the feasibility report and pointer list.
- Keep generated session logs out of commits.

## Build, Test, and Development Commands

Use 64-bit Python on Windows. The reader, step loop and most tests use only the standard library. The Gymnasium wrapper, training and controller output need the packages in `requirements.txt` (`vgamepad` also needs the ViGEmBus driver).

```powershell
python .\read_health.py --help
python -m py_compile .\read_health.py
$gameProcess = Get-Process sekiro
python .\read_health.py --pid $gameProcess.Id | Out-File -Encoding utf8 session-01.jsonl
python -m unittest discover -s tests
python -m sekirorl.smoke_test --pid $gameProcess.Id --dry-run
python -m sekirorl.smoke_test --pid $gameProcess.Id
python -m sekirorl.runner --pid $gameProcess.Id
python -m sekirorl.runner --pid $gameProcess.Id --send --policy deflect
python -m sekirorl.runner --pid $gameProcess.Id --send --auto-reset --episodes 2
python -m sekirorl.train --pid $gameProcess.Id --hours 8
python -m sekirorl.demos pads
python -m sekirorl.demos record --pid $gameProcess.Id --minutes 30
python -m sekirorl.demos report demos\demo-<time>.jsonl
python -m sekirorl.clone demos\demo-<time>.jsonl --name clone-1
python -m sekirorl.runner --pid $gameProcess.Id --send --auto-reset --policy model --model models\clone-1\last.zip --episodes 5
```

These commands display options, check syntax, record live samples after loading a save, run the automated tests, run the smoke test (first without, then with, controller output), and run whole episodes of the rematch (first as a dry run while you play, then with the scripted deflect sending input, then with the automatic reset driving the idol menu between episodes; it waits for the game window to be in front before sending anything). Plain `>` in Windows PowerShell writes UTF-16; use `Out-File -Encoding utf8` for logs. Restart the reader with the new PID after restarting Sekiro. For any run with `--send`, start Sekiro with every other controller switched off and load the save with the keyboard: with the user's controller on at game start, the game ignored the virtual pad for that whole session (2026-11-27). Recording the user's play (`sekirorl.demos`) is the opposite case: it sends nothing, and Sekiro has to be started with the user's controller on.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and variables, and `PascalCase` classes. Preserve explicit `ctypes` argument and return types for Windows functions. Express memory offsets in hexadecimal and document pointer traversal order, dereferences, and value types. No formatter or linter is configured.

## Testing Guidelines

Tests use standard-library `unittest` in `tests/test_*.py`; run `python -m unittest discover -s tests`. Tests that need Gymnasium or Stable-Baselines3 skip themselves when those are missing, so a skip is not a pass. No coverage threshold is configured.

Test null pointers, failed or partial reads, changing chains, and invalid HP pairs using simulated memory. For offset changes, follow `MEMORY_READER.md`: verify damage/healing, at least three game restarts, repeated area reloads, and boss phase transitions. Record executable version, SHA256, mods, and observed results. Distinguish syntax checks from live validation.

## Commit & Pull Request Guidelines

No Git history was available to establish existing conventions. Use short imperative subjects, such as `Handle partial health reads`. Keep changes focused. PRs should explain behavior changes, link relevant issues and offset sources, list validation performed, and identify untested assumptions. Include sample JSONL or screenshots only when useful evidence.

## Read-Only Memory Contract

Preserve query/read/synchronize permissions; avoid process-memory writes, injection, or freezing. Resolve chains for each sample and represent unavailable observations with null health values. Treat offsets as candidates until validated, and verify boss identity independently of plausible HP values.
