# Handoff: Stage 3, memory fields (at gate G03)

Written: 2026-11-25. Status: **Stage 3 work is finished and reported at gate G03. Do not
start Stage 4 until the user approves the gate. Nothing from 2026-11-25 is committed.**

How this folder works: `current.md` describes the task in progress. When a gate is approved,
move this file to `done/` (named `YYYY-MM-DD-short-title.md`) and write a new `current.md`.
The previous handoff is `done/2026-11-18-stages-1-2-environment.md`.

## Read first

1. `AGENTS.md` - project rules, including the read-only memory contract, and all commands.
2. `docs/plan.md` - decisions, staged tasks, gates, and the gate report format (section C).
3. `MEMORY_READER.md`, section "Stage 3 live validation log" - every finding with its
   evidence. Add new results there.
4. `Plan.md` (repo root) section 4 - the pointer list, corrected on 2026-11-25.

## Task

Stage 3 of `docs/plan.md`, tasks S03-T01 to S03-T08. Stop at gate G03 with the status report
from section C of the plan.

| Task | Status | What is left |
|---|---|---|
| S03-T01 Discovery setup | Completed | Nothing |
| S03-T02 Player HP | Completed | Nothing |
| S03-T03 Posture, position, animation | Completed | Nothing |
| S03-T04 In-game time, death counter | Completed | Nothing |
| S03-T05 Rematch notes (user) | In progress | The user's notes: menu path, entry name, gourds, victory |
| S03-T06 Boss object | Completed | Nothing |
| S03-T07 Stable boss route | Completed | Nothing |
| S03-T08 Reader changes | Completed | Nothing |

## Next step

1. **Wait for the user's answer on G03.** The report was given in the session of
   2026-11-25; its content is in `docs/plan.md` section F and `MEMORY_READER.md`.
2. **S03-T05 notes from the user**, needed before S04-T04 (automatic reset): button
   presses from the idol menu to the fight; what the Genichiro entry is called and whether
   there is more than one; where the player lands after winning; whether the gourds are
   full at each start.
3. **One live run of the default path**, at the start of the next session with the game
   up: `python -m sekirorl.smoke_test --pid <pid> --dry-run` with the game focused. This
   is the first run of `read()` and `StepLoop` against the game since the fields went
   live, so the frame-token check is exercised for real. Expect every step valid in play
   and invalid in the pause menu.
4. On approval: record the date in `docs/plan.md` section F, move this file to
   `done/2026-11-25-stage-3-memory-fields.md`, write a new `current.md` for Stage 4.

Things Stage 4 inherits from what was seen (details in `MEMORY_READER.md`):

- **Episode start (S04-T03):** after the loading screen the frame token stands still for
  about 6.5 s and the boss reads 7670, then one sample at 7690, then 9887 when its
  animation leaves `-1`. Stepping before that gives invalid steps and would truncate.
- **Deathblow (S04-T01):** boss posture 0 with animation `12100` then `12110`, HP set to 1
  after 1.3 s, HP back to the maximum 2.6 s later, on the same object. No counter of
  remaining deathblows was found in the first 0x2000 bytes of the object. The third
  health bar is probably another character (entity `1110801`, NPC ID `71100000`), never
  seen active.
- **Reward:** the drop from 4787 to 1 at a deathblow is read as 4786 damage by
  `hp_reward`; the refill is ignored. Decide whether that is wanted.
- **Player death (S04-T02):** HP 0 lasts 3 to 19 s while the resurrect prompt shows; the
  death counter moves only when the death is final.
- **Environment wiring:** pass `boss=memory.GENICHIRO` to `MemoryStateSource`. The smoke
  test still builds its source without a boss.

## Hard rule

Read-only memory, always (decision D3). Everything on 2026-11-25 was read with the
project's own `sekirorl.memory.Reader` (query/read/synchronize rights). Cheat Engine was
running but was not used and was not needed. If it is used, only the operations listed
under S03-T01 in `MEMORY_READER.md` are allowed.

## What was found on 2026-11-25

Executable SHA256 `637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856`, module
base `0x140000000`, Sekiro 1.06. Four launches (PIDs 28268, 29772, 17992, 6976), six
rematch fights, one deathblow, no victory. A different save from 2026-11-20: player HP
960, posture 360, death counter 393 at the start and 399 at the end.

- **Boss:** NPC ID `71001000` (character `+0x68`), map entity ID `1110800` (character
  `+0x1A24`). Block 2, entry 261, handle `0x10008105` on all six fights, at a different
  heap address each time.
- **The NPC ID is not enough.** Entry 262 is a dormant copy with the same NPC ID and entity
  ID `1110890`. The entity ID is what separates them.
- **Boss fields** use the player's layout: HP falls in steps of 340 (170 once) and rose
  only at the refill after the deathblow; posture counts down from 4083.
- **Death counter in the rematch:** it moves, when the death is final.
- **After a death** the player is back at the idol with full HP after a 1.3 s loading
  screen.
- **Pause check:** the frame token (CRC of the player's animation ring) changed on every
  10 Hz sample of every fight, deathblow included.
- **The user's screen check:** player maximum HP 960 matches; boss bar "about half" against
  45% read from memory.

## Code changed on 2026-11-25 (uncommitted)

- `sekirorl/memory.py`: `CHAINS` is gone. Layout constants, a `STATUS` table (every group
  is now `live`), `Reader.read_character` (one character in one pass, module table
  resolved twice), `MemoryStateSource` with the boss lookup (`boss=(entity ID, NPC ID)`;
  constants `GENICHIRO` and `GENICHIRO_WAY_OF_TOMOE`), `characters()` to walk the
  collection, and `read(include_candidates=True)` for manual checks.
- `sekirorl/snapshot.py`: new fields `boss_reason`, `boss_npc_id`, `death_count`,
  `frame_token`. Posture is encoded as bar fill (`1 - value / max`), observation names
  `player_posture_fill` and `boss_posture_fill`, schema `v1.1-scalars`.
- `sekirorl/loop.py`: a repeated frame token makes the step invalid, alongside the
  existing in-game time check.
- `sekirorl/fake.py`: `menu` windows (frame token stands still, in-game time runs).
- `sekirorl/smoke_test.py`: the press-to-animation measurement reads the ring.
- `tests/test_memory.py`: region-based simulated memory; character, state-source and boss
  tests. `tests/test_loop.py`: pause-menu and posture tests.
- `tools/record_rematch.py`, `tools/log_snapshots.py`: the two recorders (see "Tools").
- `MEMORY_READER.md`, `Plan.md` section 4, `AGENTS.md`, `docs/plan.md` updated.

Checks run on 2026-11-25, after the fields were set to `live`:

- Local, Windows: `python.exe -m unittest discover -s tests` - 81 passed, none skipped.
  `python.exe -m py_compile` on `read_health.py`, the changed `sekirorl` modules and both
  tools - fine. `python.exe read_health.py --help` - fine (run before the promotion).
- Local, WSL: `python3 -m unittest discover -s tests` - 81 run, 2 skipped (no Gymnasium).
- Live, before the promotion, with `read(include_candidates=True)`: 5801 samples over four
  fights on four launches, the boss found on every one; read time median 0.25 to 0.31 ms,
  maximum 1.48 ms.
- Not run: the default `read()`, `StepLoop`, `SekiroEnv` and the smoke test against the
  game since the promotion. The game was closed when the promotion was made.

## Tools

Both are read-only and run from the repo root under Windows Python, with the log written
outside the repo:

- `python.exe - <pid> <seconds> < tools/log_snapshots.py > log.jsonl` - every snapshot
  field at 10 Hz through `MemoryStateSource` with `boss=GENICHIRO`. Use this for the
  restart checks.
- `python.exe - <pid> <seconds> < tools/record_rematch.py > log.jsonl` - player at 10 Hz,
  every character within 100 m once a second, and the characters in `WATCH` every sample
  with a 0x2000-byte object dump every 5 s. Use this for a deathblow.

Run them in the background and analyse the growing log with WSL Python. Each launches two
`python.exe` processes (the launcher and the interpreter); stop both when stopping one early.

RTTI names can be read in Python: vtable at `[object]`, locator at `[vtable - 8]`, type
descriptor RVA at `locator + 0xC`, name at `descriptor + 0x10`.

## Environment facts

- The repo is edited from WSL but runs under 64-bit Windows Python 3.15 (`python.exe` works
  from WSL). Installed there: gymnasium 1.4.0, numpy 2.5.5, stable-baselines3 2.9.2,
  torch 2.15.0 (CPU build), vgamepad 0.2.0; the ViGEmBus driver is present.
- `session-01.jsonl` is UTF-16 and 1.3 MB; do not read it whole. Do not open `PointerMaps/`.
- `sekirorl/__pycache__/` is committed, so running the tests on Windows shows two `.pyc`
  files as changed. There is no `.gitignore`.
- The game window must be focused for the tools to send controller input. Reading does
  not need focus.
- The user follows along in the terminal and reports when a fight has ended.

## Open points carried forward

- The 30-action list was presented at G02 and not objected to; treat it as accepted unless the
  user says otherwise.
- Decisions made without asking, put to the user at G03: posture encoded as bar fill; boss
  identity as entity ID plus NPC ID; two recorder scripts added under `tools/`; every
  field group set to `live`.
- Death is HP 0, but HP 0 is also the state while the resurrect prompt is showing (seen
  lasting 3 to 19 s). D9 says the first HP 0 ends the episode; the reset script then has to
  answer the prompt.
- Player posture reached 0 (a full bar) once, with animation `227600170`; what the game
  does at that moment was not examined.
- `MemoryStateSource` searches the whole collection on every read while the boss is not
  loaded (0.67 ms). Fine at 10 Hz.
