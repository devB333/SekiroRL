# Repository Guidelines

## Project Structure & Module Organization

This project provides a read-only external Sekiro health reader for Windows, intended for reinforcement learning observations.

- `sekirorl/memory.py` contains the `ctypes` Windows API wrapper, pointer resolution, the chain table (each chain marked live or candidate), sampling, and the command-line entry point. `read_health.py` is a thin wrapper that keeps the documented command line.
- `sekirorl/snapshot.py` defines the raw `Snapshot` and the fixed observation layout (values plus validity bits).
- `sekirorl/controller.py`, `actions.py`, `clock.py` and `loop.py` hold controller output, the discrete action set with press/release schedules, timing, and the fixed-rate step loop. `sekirorl/env.py` is the Gymnasium wrapper; `sekirorl/fake.py` is the simulated game used by tests; `sekirorl/smoke_test.py` is the live Windows check.
- `tests/` holds standard-library `unittest` tests. `docs/plan.md` is the staged roadmap with gates; `docs/handoffs/current.md` is the state of the task in progress.
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
```

These commands display options, check syntax, record live samples after loading a save, run the automated tests, and run the smoke test (first without, then with, controller output). Plain `>` in Windows PowerShell writes UTF-16; use `Out-File -Encoding utf8` for logs. Restart the reader with the new PID after restarting Sekiro.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` functions and variables, and `PascalCase` classes. Preserve explicit `ctypes` argument and return types for Windows functions. Express memory offsets in hexadecimal and document pointer traversal order, dereferences, and value types. No formatter or linter is configured.

## Testing Guidelines

Tests use standard-library `unittest` in `tests/test_*.py`; run `python -m unittest discover -s tests`. Tests that need Gymnasium or Stable-Baselines3 skip themselves when those are missing, so a skip is not a pass. No coverage threshold is configured.

Test null pointers, failed or partial reads, changing chains, and invalid HP pairs using simulated memory. For offset changes, follow `MEMORY_READER.md`: verify damage/healing, at least three game restarts, repeated area reloads, and boss phase transitions. Record executable version, SHA256, mods, and observed results. Distinguish syntax checks from live validation.

## Commit & Pull Request Guidelines

No Git history was available to establish existing conventions. Use short imperative subjects, such as `Handle partial health reads`. Keep changes focused. PRs should explain behavior changes, link relevant issues and offset sources, list validation performed, and identify untested assumptions. Include sample JSONL or screenshots only when useful evidence.

## Read-Only Memory Contract

Preserve query/read/synchronize permissions; avoid process-memory writes, injection, or freezing. Resolve chains for each sample and represent unavailable observations with null health values. Treat offsets as candidates until validated, and verify boss identity independently of plausible HP values.
