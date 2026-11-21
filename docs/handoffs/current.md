# Handoff: Stage 3, memory fields (in progress)

Written: 2026-11-20. Status: **Stage 3 started on 2026-11-20 and paused by the user partway
through. S03-T01 is complete. The player fields are verified in one session. The boss has
not been looked at yet. No reader code has changed. Wait for the user to say go.**

How this folder works: `current.md` describes the task in progress. When a gate is approved,
move this file to `done/` (named `YYYY-MM-DD-short-title.md`) and write a new `current.md`.
The previous handoff is `done/2026-11-18-stages-1-2-environment.md`.

## Read first

1. `AGENTS.md` - project rules, including the read-only memory contract, and all commands.
2. `docs/plan.md` - decisions, staged tasks, gates, and the gate report format (section C).
3. `MEMORY_READER.md`, section "Stage 3 live validation log" - every finding below in full,
   with the evidence. Add new results there.
4. `Plan.md` (repo root) section 4 - the original pointer list. Several entries are now known
   to be wrong; see "Corrections" below.

## Task

Stage 3 of `docs/plan.md`, tasks S03-T01 to S03-T08. Stop at gate G03 with the status report
from section C of the plan.

| Task | Status | What is left |
|---|---|---|
| S03-T01 Discovery setup | Completed | Nothing |
| S03-T02 Player HP | In progress | Three game restarts |
| S03-T03 Posture, position, animation | In progress | One check across a restart |
| S03-T04 In-game time, death counter | In progress | Death counter inside the rematch |
| S03-T05 Rematch notes (user) | Not started | All of it |
| S03-T06 Boss object | Not started | All of it; the method is ready (see "Next step") |
| S03-T07 Stable boss route | Not started | Character list layout is known; boss slot is not |
| S03-T08 Reader changes | Not started | All of it |

## Next step

The user was about to start the Genichiro rematch when the session was paused. Pick up there.

1. The user starts Sekiro, loads the save, and attaches Cheat Engine. Get the PID with the
   Cheat Engine `get_process_info` operation; it changes on every launch.
2. Start a read-only recorder (see "How the recordings were made") that logs the player at
   10 Hz and every character within 100 m once a second.
3. The user goes to a Sculptor's Idol, starts Reflection of Strength and picks Genichiro (the
   Ashina Castle tower fight), fights for at least a minute, dies without resurrecting,
   starts the rematch again, and wins one if they can.
4. While doing that the user notes, for S03-T05: the button presses from the idol menu to
   the fight starting; what the Genichiro entry is called and whether there is more than
   one; where the player lands after dying and whether the death screen waits for a button;
   where the player lands after winning; whether the gourds are full at each start.
5. From the log, find the boss: the character whose `+0x68` ID is not an idol or bystander,
   with a large HP that drops when hit. Record its ID, block number, entry index and handle.
   Check HP, posture direction, position and animation against the fight, including the
   phase change after a deathblow (the object may be replaced; watch for a new ID or slot).
6. Check whether the death counter moves on a rematch death (S03-T04).
7. Then three game restarts, each with one short recording in the rematch. That closes the
   restart checks for S03-T02, S03-T03 and S03-T07 together.
8. Then S03-T08: change the reader, the snapshot and the tests.

This instruction list was also sent to the user on Discord, where they like to have the
steps to follow while playing.

## Hard rule

Read-only memory, always (decision D3). The Cheat Engine connection exposes about 170
operations, including writes, injection, a debugger and arbitrary Lua. Only the operations
listed under S03-T01 in `MEMORY_READER.md` are used: process facts, memory viewing, value
and pattern scans, and static disassembly. Debugger and breakpoint operations are not used,
because a debugger halts game threads. So "find what accesses this address" is unavailable.

In practice nearly all of the work so far was done with the project's own Python reader
(`sekirorl.memory.Reader`, which opens the process with query/read/synchronize rights), not
through Cheat Engine. Cheat Engine was used only to confirm the process and cross-check HP.

## Findings from 2026-11-20

Executable SHA256 `637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856`, module
base `0x140000000`, Sekiro 1.06. Same as 2026-11-18. PID that day was 7032.

Player fields, each checked against the user's actions in one session (no restart yet):

- **HP** (`88, 1FF8, 18, 130`): follows damage, gourd healing and resting. Reads 0 at death
  and stays 0 until respawn or resurrection. Reads 160 after resurrecting.
- **Posture** (`88, 1FF8, 18, 148`): **counts down**. 120 means an empty posture bar; it
  drops as the bar fills and climbs back. The observation should use `1 - value / max` if it
  is meant to be "bar fill".
- **Position** (`88, 1FF8, 68, 80`): follows movement. The second value is height.
- **Death counter** (GameData root, `90`): goes up about 5.6 s after HP reaches 0 on a death
  without resurrection. Does not change on a death followed by resurrection.
- **In-game time** (GameData root, `9C`): 16 ms per frame. Stops in loading screens.
  **Does not stop in the pause menu**, so it cannot be the pause check the plan wanted.
- **Loading:** the WorldChrMan root or player slot is null for about 1.2 s, then all chains
  resolve again at new addresses.

Animation:

- The candidate `88, 1FF8, 10, 20` is **wrong as "current animation"**. It is slot 0 of a
  ring of ten entries in `SprjChrTimeActModule`, and with the player's upper-body layer
  active it alternates between two animations and can be nine frames old. The 56.9 ms
  press-to-animation figure from 2026-11-18 was measured on this field (790010 to 790040,
  both upper-body IDs); it is still a real response to the press.
- Correct read: at the module (`88, 1FF8, 10`), read `+0x1C .. +0xF4` in one call. Entries
  are 0x14 bytes from `+0x1C`: `{i32 unknown, i32 animation id, f32 elapsed, f32 elapsed
  next frame, f32 length}`. `+0xEC` (i32) is the slot of the current full-body entry.
- Over 211 s that entry followed every action and never showed an upper-body ID.
- **Pause check:** the ring stops changing while the pause menu is open and around loading
  screens, and changed on every sample otherwise. Use "ring unchanged since the last step"
  as the liveness check instead of in-game time.
- `88, 1FF8, 28, 878` is a UTF-16 state name (`StandIdle`, `GroundAttackCombo1Release`,
  `DeflectGuardIdle`). It leads the animation ID by 50 to 100 ms. Debug aid.

Character collection (groundwork for S03-T07, found without a value scan):

- WorldChrMan holds 20 `WorldBlockChr` structs inline at `+0x910 + 0x140 * b`.
- In a block: `+0x80` i32 capacity; `+0x88` pointer to an array of 0x38-byte entries; entry
  `+0x00` is the character pointer, null when not loaded.
- On a character: `+0x08` u32 handle, whose low bits equal the entry index; `+0x68` i32
  looks like the NPC ID (`10010000` for Sculptor's Idols, which are characters with 9999
  HP). The module table at `+0x1FF8` has the same layout as the player's, and HP, posture,
  position and the animation ring read sensibly on NPCs at the Dilapidated Temple.
- If the boss keeps the same block and entry index every time, the route is a fixed chain
  plus an ID check on every read. That is the thing to test in the rematch.

## Corrections to `Plan.md` section 4

`Plan.md` has not been edited. These entries are wrong or need changing when S03-T08 is done:

- Animation ID and elapsed at `1FF8, 10, 20` / `24`: use the ring and index instead.
- NPC ID `30, 628` and think ID `30, 624`: read garbage. Use character `+0x68`.
- In-game time as a pause check (section 4.5): it does not stop when paused.
- "Character ID `68`" is probably the NPC ID itself; confirm on the boss.

## How the recordings were made

Three throwaway scripts were run with Windows Python from the repo root, each importing
`sekirorl.memory.Reader` and printing one JSON line per sample to a file outside the repo.
They were in a temporary folder and are gone; rewrite them from the layouts above.

- All `CHAINS` entries at 20 Hz (player session).
- Raw bytes of the TimeAct, ActionRequest and Behavior modules at 20 Hz (animation work).
- Player at 10 Hz plus, once a second, every character in all 20 blocks within 100 m of the
  player: handle, `+0x68` ID, HP pair, posture pair, position, current animation, block and
  entry index. This is the one to use for the rematch. A full pass took under 5 ms.

RTTI names were read in Python: vtable at `[object]`, locator at `[vtable - 8]`, type
descriptor RVA at `locator + 0xC`, name at `descriptor + 0x10`.

Run a script as `python.exe - <pid> <seconds> < script.py > log.jsonl` from WSL, in the
background, and analyse the log with WSL Python.

## How to promote a field once verified

1. Change its status in `CHAINS` (`sekirorl/memory.py`) from `candidate` to `live`.
2. Fill the matching `Snapshot` field in `MemoryStateSource.read`.
3. Its validity bit then turns on automatically in `sekirorl/snapshot.py` `encode`.
4. Extend `tests/test_memory.py`; record the validation in `MEMORY_READER.md`.

The animation read does not fit the current `CHAINS` format (one struct per chain): it needs
the ring block and an index lookup. The boss route needs a block and entry index plus an
identity check. Both need a small design decision in S03-T08.

## Environment facts

- The repo is edited from WSL but runs under 64-bit Windows Python 3.15 (`python.exe` works
  from WSL). Installed there: gymnasium 1.4.0, numpy 2.5.5, stable-baselines3 2.9.2,
  torch 2.15.0 (CPU build), vgamepad 0.2.0; the ViGEmBus driver is present.
- `python.exe -m unittest discover -s tests`: 47 tests passed on Windows on 2026-11-18. Not
  re-run on 2026-11-20; no code changed.
- `session-01.jsonl` is UTF-16 and 1.3 MB; do not read it whole. Do not open `PointerMaps/`.
- The folder is now a git repository with one commit (`8504fd7`). Nothing from 2026-11-18
  onward is committed: `MEMORY_READER.md`, `docs/plan.md`, `docs/handoffs/`,
  `sekirorl/smoke_test.py` and two files under `PointerMaps/Health/` show as changed.
  `sekirorl/__pycache__/` is untracked and not ignored.
- The Cheat Engine connection (CE MCP Bridge v12.0.0) has to be attached to `sekiro.exe` by
  the user after each game launch.
- The game window must be focused for the tools to send controller input.

## Open points carried forward

- The 30-action list was presented at G02 and not objected to; treat it as accepted unless the
  user says otherwise.
- The live reader still has no pause detection. The ring check above is the proposed fix.
- Posture direction affects `player_posture_frac` and `boss_posture_frac` in
  `sekirorl/snapshot.py`; decide in S03-T08 whether to invert them.
- Death is HP 0, but HP 0 is also the state while the resurrect prompt is showing. Stage 4
  has to decide whether the first HP 0 ends the episode.
