# Sekiro external HP reader

The supplied executable version is 1.6.0.0. Use the community's 1.06
offsets as candidates, then validate against your executable. Version text
alone does not establish binary identity. Results against the running game are
in "Stage 3 live validation log" at the end of this file.

## Evidence and pointer semantics

[ElaDiDu's table](https://github.com/ElaDiDu/Sekiro-Practice-CT/blob/main/Ela_Sekiro_Table.CT)
explicitly labels its static roots as 1.06. Its Hero stats document current
HP and MaxHP. Its TargetedNPC symbol is allocated and populated by a code
injection; it is not a game-owned static root usable by this external reader.
Do not activate that script for a read-only workflow.

[Versioned offsets](https://github.com/mstampfli/sekiro-coop/blob/main/crates/sekiro-sdk-sys/src/offsets.rs)
corroborate WorldChrMan at module RVA 0x3D7A1E0.
[Live accessors](https://github.com/mstampfli/sekiro-coop/blob/main/crates/sekiro-sdk-sys/src/live.rs)
document the following player and character HP layout:

```text
B = actual loaded base of sekiro.exe
W = read_u64(B + 0x3D7A1E0)
C = read_u64(W + 0x88)             # player character
S = read_u64(C + 0x1FF8)
D = read_u64(S + 0x18)
current_hp = read_i32(D + 0x130)
maximum_hp = read_i32(D + 0x134)
```

For an independently identified enemy character C, the same S/D/HP suffix
is documented. The missing part is a stable external route to the intended
boss instance. A lock-on target can be another enemy and can disappear when
unlocked. A plausible HP pair does not establish identity or object liveness.

## Run

Use 64-bit Python, standard library only. Start the game and load a save:

```powershell
$gameProcess = Get-Process sekiro
(Get-Item -LiteralPath $gameProcess.Path).VersionInfo | Select-Object FileVersion,ProductVersion
Get-FileHash -LiteralPath $gameProcess.Path -Algorithm SHA256
python .\read_health.py --pid $gameProcess.Id > session-01.jsonl
```

Keep the version, hash, mod list, area, boss and phase with each session log.
Restart the script with the new PID after each game restart. The reader
enumerates the module base on attachment and walks every pointer on each
sample. It opens only query/read/synchronize rights and uses
[ReadProcessMemory](https://learn.microsoft.com/en-us/windows/win32/api/memoryapi/nf-memoryapi-readprocessmemory).
It performs no injection, process-memory writes, freezes, or debugger attachment.

Null/unreadable pointers and implausible values produce unavailable samples,
never fabricated zero health. The chain is checked again after the HP read
to catch some transition races; this is not an atomic game snapshot and cannot
detect every stale-but-readable object. Player and boss samples are sequential.

## Validate before using as RL observations

1. In Cheat Engine, attach to sekiro.exe with a blank table. Manually add a
   pointer with base `sekiro.exe+3D7A1E0`. The traversal offsets are
   `88 -> 1FF8 -> 18 -> 130`; CE's displayed/XML order is reversed, with
   `130` at the top and `88` at the bottom. Set type to 4 Bytes. Duplicate
   the entry with final offset `134` for maximum. Do not tick/freeze values.
2. At full health, check current equals max. Take several different amounts
   of damage and heal. Current should follow the bar; max should stay stable.
   Check the approximate bar ratio current/max. A deflection or blocked attack
   alone is not evidence of vitality damage. Rule out posture and HUD copies.
3. Die, resurrect, rest, and quit to title/reload. Observe the log through
   transitions. Unavailable is acceptable during loading, but stale values
   should not become training observations. Confirm correct recovery afterward.
4. Travel to another area and return at least three times. Verify again with
   damage/healing each time; pointer values may change, offsets must not.
5. Completely exit and restart the game at least three times, preferably with
   a Windows reboot among sessions. Reattach CE and restart the Python reader.
   Verify the chain resolves without replacing any absolute heap addresses.
6. For the boss, damage only that boss; check the HP bar ratio. Switch targets,
   unlock/relock, leave/re-enter the encounter, die/retry, and cross phase
   transitions. Check for a new entity or health refill at each phase. Do not
   infer encounter victory from a missing pointer or one phase reaching zero.

Record each event's timestamp alongside the JSONL samples. Accept a candidate
only when all trials match observed behavior. Numeric range checks alone are
insufficient. For training, add independently validated loading/encounter and
entity identity checks; never reuse a cached boss heap pointer after reload.

## Discover a missing or incompatible chain without memory writes

Use a blank Cheat Engine table, decline downloaded table Lua execution, and
leave all freezing, patching and auto-assembler scripts disabled. The procedure
below needs only value scans, memory viewing and pointer scans.

1. Start with player health or one isolated boss. Choose 4 Bytes and Unknown
   initial value. Take real vitality damage, then Next Scan: Decreased value.
   Wait without damage, then Unchanged value. Repeat. For the player, heal and
   filter Increased value. If this produces no candidates, restart with Float;
   do not assume the bar's pixel width is the raw HP number.
2. Put candidates in the address list without freezing. Observe several hits
   and idle periods. For a boss, distinguish its changing HP from yours and
   from other enemies. Check candidates against the bar ratio, including after
   lock loss. HUD mirrors can pass simple damage tests, so keep multiple
   candidates until reload and identity testing eliminates them.
3. Inspect nearby 4-byte fields in Memory View. For the documented layout,
   current+4 is maximum and current+8 is base HP. Treat these as hypotheses on
   a differing build. Max should remain stable while damaged and equal current
   when fully healed/reset. Validate player max across a legitimate vitality
   upgrade or temporary maximum change if available; this helps distinguish
   duplicate/base values. Boss reset may move the entire object.
4. Right-click the live current-HP address, choose Pointer scan for this address,
   and save results/pointer map A. Start with maximum depth 6 and maximum offset
   0x3000 (the known 0x1FF8 hop exceeds common small scan defaults). Prefer
   module-relative roots; scan sizes can be large. Empty results may require
   more depth/offset range or a different underlying HP candidate.
5. Reload the area, independently find the new HP address, and rescan the
   saved results against that address. Repeat after complete process restarts,
   with fresh pointer maps where your CE version supports comparison. Rescan
   to the new address, not merely the same numeric HP value. See
   [CE's pointer-scan documentation](https://wiki.cheatengine.org/index.php?title=Help_File:Pointer_scan).
6. Repeat across boss retries and phases. Eliminate roots in temporary heap
   allocations or injected modules. A surviving chain must still select the
   intended entity; a fixed entity-list slot may pick a different actor later.
   If only a lock-on route survives, explicitly limit observations to confirmed
   boss lock-on until you discover stable entity identity and enumeration.
7. Translate the surviving `sekiro.exe+RVA` chain into traversal order. Supply
   its current-HP root and offsets using `--boss-rva 0xYOUR_RVA` and
   `--boss-offsets "FIRST,...,FINAL"` (replace placeholders). All offsets are
   hexadecimal. This reader assumes max HP is current+4; verify that separately
   before enabling the boss option. Otherwise change the pair-reading code.

If scanning fails because the game uses handles instead of direct pointers,
inspect the character manager's collections and handle lookup read-only, then
validate identity across reloads. There is no justified universal boss root
in the sources inspected here; do not invent one or reuse TargetedNPC from
the injected table as if it were static.

## Stage 3 live validation log

Executable for every entry below unless stated: Sekiro 1.06,
`C:\Program Files (x86)\Steam\steamapps\common\Sekiro\sekiro.exe`, SHA256
`637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856`, module base
`0x140000000`. Mods: none reported.

### S03-T01 — Cheat Engine connection (2026-11-20)

Connection: CE MCP Bridge v12.0.0, attached to `sekiro.exe` by the user. Cheat Engine
opens the process with its own (full) access rights; the project's reader still opens
with query/read/synchronize only, and nothing below changes that.

The connection exposes about 170 operations, including writes, injection, a debugger
and arbitrary Lua. Only these are used in this project:

- Connection and process facts: `ping`, `get_process_info`, `get_opened_process_id`,
  `get_processid_from_name`, `enum_modules`, `get_module_size`, `get_thread_list`.
- Memory viewing: `read_memory`, `read_integer`, `read_pointer`, `read_pointer_chain`,
  `read_string`, `get_memory_regions`, `enum_memory_regions_full`,
  `get_memory_protection`, `get_address_info`, `get_rtti_classname`, `checksum_memory`,
  `md5_memory`, `compare_memory`, `validate_pointer_chains`.
- Value and pattern scans: `scan_all`, `next_scan`, `get_scan_results`, the
  `persistent_scan_*` group, `aob_scan*`, `search_string`, `pointer_rescan`.
- Static code reading: `disassemble`, `get_instruction_info`, `analyze_function`,
  `find_function_boundaries`, `find_references`, `find_call_references`,
  `generate_signature`, `get_symbol_address`, `get_symbol_info`.

Never used, whatever the connection allows: every `write_*`, `copy_memory`,
`allocate_*`, `free_memory`, `set_memory_protection`, `map_*`, `create_section`,
`auto_assemble*`, `assemble_instruction`, `inject_*`, `execute_code*`, `execute_method`,
`create_thread`, `compile_*`, `generate_*_script`, every `debug_*` and breakpoint
operation (a debugger halts game threads, which counts as freezing),
`pause_process`/`unpause_process`, memory records that can freeze or set values,
`load_table`, `evaluate_lua`, `run_command`, `shell_execute`, the `dbk_*`/`dbvm`/`cr3`
kernel group, and keyboard/mouse/window-message input.

Consequence for the boss back-walk: "find what accesses this address" is a debugger
feature and is not available. The back-walk uses value scans for pointers to an
address, RTTI class names and static disassembly instead.

Check: `read_pointer_chain` on `sekiro.exe+3D7A1E0` with offsets `88, 1FF8, 18, 130`
ended at `0x7FF4F60ACD70` and read `0x0000014000000140` (320 / 320). The Python reader
resolved the same address and read 320 / 320 in the same session (PID 7032).

### S03-T02 to S03-T04 — player fields, first session (2026-11-20, PID 7032)

Method: the Python reader logged every chain at 20 Hz while the user played a scripted
sequence (walk, attack, guard, pause, take hits, guard hits, heal, die, respawn, die,
resurrect, rest). No restart of the game has been done yet; two area reloads were seen
(respawn after death, idol travel to the Dilapidated Temple).

| Field | Chain (WorldChrMan root unless stated) | Observed | State |
|---|---|---|---|
| HP / max HP | `88, 1FF8, 18, 130` | 320 → 208 on a hit; +9 per second from the gourd; 0 at death and stays 0 until respawn or resurrection; 160 after resurrecting; 320 after resting | Verified in one session |
| Posture / max | `88, 1FF8, 18, 148` | **Counts down.** 120 = empty bar. Guarded hits: 120 → 91 → 68 → 51. Recovers about 1 point per 50 ms when left alone. Reset to 120 on resurrection | Verified in one session |
| Position | `88, 1FF8, 68, 80` | Changes with walking and sprinting; second value is height (rose during a jump); moved to the idol after respawn | Verified in one session |
| Death counter | GameData root, `90` | 5 → 6, 5.6 s after HP reached 0 on a death without resurrection. Did not change on a death followed by resurrection. Rematch behaviour unknown | Verified in one session |
| In-game time | GameData root, `9C` | Advances 16 ms per frame (about 960 per second). Stops during loading screens. **Does not stop in the pause menu** | Verified; not usable as a pause check |
| Loading | WorldChrMan root | Root or player slot is null for about 1.2 s during a loading screen, then every chain resolves again at new addresses | Verified |

Animation. The candidate `88, 1FF8, 10, 20` is **wrong as "current animation"**. The object
at `88, 1FF8, 10` is `SprjChrTimeActModule`. It holds a ring of ten 0x14-byte entries
starting at `+0x1C`: `{i32 unknown, i32 animation id, f32 elapsed, f32 elapsed next frame,
f32 length}`. Every frame the game pushes one entry per playing layer, so with the
player's upper-body layer active (IDs 7900xx: 790010 idle, 790040 attack, 790030 gourd)
slot 0, which is what `+0x20` reads, alternates between the full-body and the upper-body
animation and can be up to nine frames old.

- `+0xE8` i32: next slot to write. `+0xEC` i32: slot of the current full-body entry.
- Current animation = entry at `+0x1C + 0x14 * [+0xEC]`. Read `+0x1C .. +0xF4` in one call.
- Checked over 211 s: the entry followed every action (0 idle, 400/500/600 walk,
  1151/1200/1500/1510 sprint, 50300000 → 50300100 attack wind-up and release,
  50300010/50300110/50300020 combo, 50203000 → 50002000 → 50203010 guard, 213300 step,
  201001/201011/201040 jump, 7103xx idol) and never showed a 7900xx ID. About 1 sample in
  200 was one frame (0.017 s) behind because the read is not synchronised with the game.
- Pause check: the ring stops changing while the pause menu is open (5.4 s in the test)
  and during the fade around a loading screen; in play it changed on every sample.
- `88, 1FF8, 28, 878` (`SprjChrBehaviorModule`) is a UTF-16 state name such as
  `StandIdle`, `GroundAttackCombo1Release`, `DeflectGuardIdle`. It leads the animation ID
  by 50 to 100 ms. Useful for debugging.

Module table at `[chr + 0x1FF8]`, by RTTI name: `+0x10` TimeAct, `+0x18` Data, `+0x28`
Behavior, `+0x40` SuperArmor, `+0x58` Event, `+0x68` Physics, `+0x80` ActionRequest.

### S03-T07 groundwork — character collection (2026-11-20)

Found by reading WorldChrMan (`WorldChrManImp`) and checking RTTI names; no value scan.

- WorldChrMan holds 20 `WorldBlockChr` structs inline at `+0x910 + 0x140 * b`.
- In a block: `+0x80` i32 capacity, `+0x88` pointer to an array of 0x38-byte entries,
  entry `+0x00` = character pointer (`EnemyIns`), null when not loaded.
- Character `+0x08` u32 handle; its low bits equal the entry index
  (`0x1000402D` is entry 45). Character `+0x68` i32 looks like the NPC ID
  (`10010000` for Sculptor's Idols, which are characters with 9999 HP).
- The candidates `30, 628` (NPC ID) and `30, 624` (think ID) read garbage: **wrong**.
- Enemy characters use the same module table as the player: HP, posture, position and
  the TimeAct ring all read plausible values on the three characters near the player.
- A separate list at WorldChrMan `+0x31A8` holds only the idols.

Not yet done: the boss itself, and whether its block and entry index are the same after
retries and restarts.

### S03-T02 to S03-T07 — rematch, first recording (2026-11-25, PID 28268)

Same executable hash and module base as above. A different save from 2026-11-20 (player
HP 960, posture 360, death counter 393) and a different launch of the game, so this is
the first check of the player chains across a restart. Cheat Engine was running but was
not used; every read was made by the project's Python reader.

Method: player at 10 Hz, every character within 100 m once a second, logged while the
user started Reflection of Strength, fought Genichiro for about 40 s and died without
resurrecting.

Player, second launch:

- HP, posture, position, the animation ring, the death counter and in-game time all
  resolved on the first read with no change to any offset.
- HP 960 → 648 → 274 → 25 → 0 over four hits. Posture 360 → 165 at the lowest and back.
- Sitting at the idol reads animation `710320`, matching the `7103xx` idol IDs seen on
  2026-11-20.
- **Death counter in the rematch: it moves.** 393 → 394 at 18.7 s after HP reached 0.
  The longer delay than on 2026-11-20 (5.6 s) is the time the death prompt was left
  open: the animation was `110000` then `110020` while the prompt showed and changed to
  `110010` 0.1 s after the counter moved.
- After the death the player was back at the same idol (1.2 m from where they sat) with
  HP 960 / 960. The loading screen read as a null root or player slot for 1.3 s.

Timeline of entering the fight (seconds from the loading screen ending):

| Time | Player | Boss (entry 261) |
|---|---|---|
| 0.0 | Chains resolve. Animation `-1`, ring not changing, in-game time not advancing | HP 1598 / 1598, posture 660 (base values) |
| 0.8 | Same | HP 7670 / 7670, posture 3168 |
| 6.7 | Ring starts changing; animation `700010` (2.0 s long) | Unchanged, animation `-1` |
| 7.8 | In-game time advancing | **HP 9887 / 9887, posture 4083**, animation `405003` |
| 9.0 | Animation `0` (idle); player has control | Approaching |

So the boss's maximum HP changes twice before the fight starts, and "boss at full HP" can
only be judged once its animation is no longer `-1`.

Boss, NPC ID `71001000`, block 2, entry 261, handle `0x10008105`:

| Field | Route from the character object | Observed | State |
|---|---|---|---|
| NPC ID | `68` | `71001000`; the next i32 (`6C`) is `7100`, the model number | One fight |
| HP / max | `1FF8, 18, 130` | 9887 → 9547 → 9207: two hits of 340, at the same moments the animation showed a hit reaction (`8501`) | One fight |
| Posture / max | `1FF8, 18, 148` | **Counts down**, like the player's. 4083 = empty bar; fell to 3610 under attack and recovered to 4083 | One fight |
| Position | `1FF8, 68, 80` | Distance to the player 15 m at the start, 1 to 5 m while trading blows | One fight |
| Animation | ring at `1FF8, 10` | Changes per attack: `3003`, `3006`, `3009`, `3014`, `3015`, `3016`, `3020`, `3037`, `3045`, `3050`, `3062`, `3076`, `3100`, `5201`; `9600`/`9700` when guarding; `8501` when hit; `405000`/`405002`/`405003` standing | One fight |

**The NPC ID does not identify the boss on its own.** The same block holds dormant
characters whose values never changed during the fight and whose animation stayed `-1`:

| Entry | NPC ID | HP | Posture | Note |
|---|---|---|---|---|
| 258 | `54300000` | 9460 | 4434 | Dormant |
| 261 | `71001000` | 9887 | 4083 | **The boss** |
| 262 | `71001000` | 7670 | 3168 | Dormant copy with the same ID |
| 263 | `71100000` | 12268 | 4320 | Dormant, at the player's spawn point |
| 264 | `71100000` | 12268 | 4320 | Dormant, at the player's spawn point |

The first 0x80 bytes of entries 261 and 262 differ only in pointers and the handle.

Pause check during the fight: the player's ring changed on every one of the 10 Hz
samples from the moment the player had control until the loading screen after death,
including the whole time the death prompt was showing. It stood still for the 6.5 s of
the fade-in before the fight and for 0.5 s before the loading screen.

Not yet seen: a deathblow, the third phase, a victory, and whether entry 261 is the boss
on every attempt and after a restart.

### S03-T06 and S03-T07 — boss identity, second recording (2026-11-25, PID 28268)

Method: as above, plus the five characters listed above logged at 10 Hz and the first
0x2000 bytes of each object dumped every 5 s. The user fought for 70 s, died,
resurrected, and died again.

**Map entity ID at character `+0x1A24` (i32).** Found by comparing the dumps of entries
261 and 262: it is the one small integer that is constant in each object and differs
between them.

| Entry | NPC ID (`+0x68`) | Entity ID (`+0x1A24`) | Role |
|---|---|---|---|
| 258 | `54300000` | `1110920` | Dormant |
| 261 | `71001000` | **`1110800`** | **The boss** (first two health bars) |
| 262 | `71001000` | `1110890` | Dormant copy |
| 263 | `71100000` | `1110891` | Dormant |
| 264 | `71100000` | `1110801` | Dormant; probably the third health bar (Way of Tomoe), not yet seen active |

Away from the arena, 89 characters were loaded and all 89 had different
(entity ID, NPC ID) pairs. The player's entity ID reads `-1` and NPC ID `0`.

Route used by the reader: walk the character collection, take the one character whose
entity ID and NPC ID both match, remember only its (block, entry) position, resolve the
pointer from the static root on every sample, and check both IDs on the object read.

Second attempt, same boss:

- Entry 261 again, handle `0x10008105` again, **object at a different address**
  (`0x7FF4A2B2C330`, was `0x7FF4ABD30D20`). The object address must never be kept.
- HP 9887 → 9547 → 9207 → 8867 → 8697: three hits of 340 and one of 170. Posture fell
  to 2812 of 4083.
- The same load sequence: 1598 → 7670 at 0.3 s → 9887 at 7.7 s when the animation
  left `-1`.
- Other fields that change when the boss activates (not used): `+0x28` i32 1 → 0,
  `+0x3C` i32 -1 → 2, `+0x1F30` i32 2 → 0.

Player in the second attempt:

- Death then resurrection: HP 0 for 4.1 s, then 480 (half of 960). The death counter
  did not move.
- Second death: HP 0, and the death counter went 394 → 395 after 0.9 s; there was no
  prompt to wait on.
- Pause check: the ring changed on every 10 Hz sample from gaining control to the
  second death. One repeated sample 2.6 s after that death and five before the loading
  screen.

Checked through `MemoryStateSource` on the live process away from the arena: the boss
reads as unavailable ("Boss not in the character collection") and the player stays
valid. One read costs 0.06 ms without a boss configured and 0.67 ms when the whole
collection has to be searched.

### S03-T08 — third fight, read through `MemoryStateSource` (2026-11-25, PID 28268)

Method: `MemoryStateSource(reader, base, boss=GENICHIRO).read(include_candidates=True)`
at 10 Hz for 275 s, covering one fight of 211 s (death, resurrection, second death).
This is the reader code the environment uses, not a throwaway script.

- The boss was found at block 2, entry 261 on the first sample after the loading
  screen and on all 2188 samples until the next loading screen. The identity check
  never failed and no sample reported the boss as unavailable while it was loaded.
- Boss HP 9887 → 4447 in sixteen steps, every one exactly 340. It never rose.
- Boss posture between 2170 and 4083. Distance to the player 0.46 to 15.35.
- 53 different boss animation IDs. Besides those listed above: `3004`, `3005`, `3007`,
  `3011`, `3018`, `3019`, `3023`, `3025`, `3028`, `3030`, `3034`, `3036`, `3044`,
  `3055`, `4100`, `8010`, `8012`, `8500`, `8511`, `8600`, `8601`, `8604`, `8704`,
  `9502`, `9503`, `9999`, `13800`, `400000`, `405010`, `405401`.
- While loading, the boss read 7670 / 7670 for 7.3 s, then **7690 / 7690 on one
  sample**, then 9887 / 9887. A sample can land in the middle of the game rescaling
  the boss, so the start of an episode must wait for a stable maximum as well as an
  animation other than `-1`.
- Player: HP 960 → 711 → 462 (249 per hit), healing back to 960, posture down to 0 at
  one point (animation `227600170`), HP 0 for 2.7 s then 480 after resurrecting, HP 0
  again at the end with the death counter moving 395 → 396 after 1.8 s.
- Pause check: with the boss active, the frame token repeated on no sample during the
  fight and on the last five samples before the loading screen. In-game time never
  stood still during the fight.
- Read time with the boss loaded: median 0.26 ms, maximum 0.73 ms. Largest gap between
  samples 101 ms.

Still not seen on 2026-11-25: a deathblow, the third health bar, a victory, and any
restart of the game. The player and boss fields stay marked `candidate` in
`sekirorl/memory.py` until the three restarts are done.

User's comparison with the screen for the third fight (reported 2026-11-25): player
maximum HP 960 matches the save, and the boss's health bar was at about half when the
fight ended, against 4447 / 9887 (45%) read from memory.

### Restart 1 of 3 (2026-11-25, PID 29772)

The game was closed and started again; same executable hash and module base. Cheat
Engine was not attached. Logged with `tools/log_snapshots.py` (10 Hz) from the title
screen through one rematch fight of 60 s (death, resurrection, second death).

- Title screen: the WorldChrMan root is null, so every sample is unavailable.
- Save loaded: HP 960 / 960, posture 360 / 360, the same position at the idol as before
  the restart, death counter 396 (the value at the end of the previous launch).
- Boss: found on the first sample after the loading screen at block 2, entry 261, NPC ID
  `71001000`, entity ID `1110800`, and on all 742 samples until the next loading screen.
- Boss HP 7670 while dormant, 9887 once active, then 9887 → 7847 in six steps of 340.
  It never rose. Posture 2032 to 4083. 31 animation IDs, all seen before.
- Player: 960 → 711 → 462 (249 per hit), HP 0 for 3.7 s then 480 after resurrecting,
  HP 0 again, death counter 396 → 397 after 1.8 s.
- Frame token: no repeat during the fight; the last five samples before the loading
  screen repeated.
- Read time with the boss loaded: median 0.26 ms, maximum 0.70 ms.

### Restart 2 of 3, with a deathblow (2026-11-25, PID 17992)

The game was closed and started again; same executable hash and module base. Cheat
Engine not attached. Logged with `tools/log_snapshots.py` and `tools/record_rematch.py`
through one rematch fight of 225 s: one deathblow on the boss, then the player died.

- Save loaded: HP 960 / 960, posture 360 / 360, death counter 397.
- Boss: block 2, entry 261, NPC ID `71001000`, entity ID `1110800`, found on all 2405
  samples from the loading screen to leaving the arena. The same load sequence as
  before, including one sample at 7690 / 7690 before 9887 / 9887.
- Boss HP 9887 → 4787 in fifteen steps of 340. Boss posture fell as low as 119 of 4083.
- Death counter 397 → 398 at the end, 4.6 s after HP 0 (the prompt was showing).
- Frame token: no repeat during the fight, deathblow included; the last four samples
  before the loading screen repeated.
- Read time with the boss loaded: median 0.31 ms, maximum 0.53 ms.

**The deathblow** (seconds from the posture break):

| Time | Boss HP | Boss posture | Boss animation | Player animation |
|---|---|---|---|---|
| before | 4787 | 119 | `3003` (an attack) | guarding |
| 0.0 | 4787 | **0** | `12100` | `227510100` |
| 0.6 | 4787 | 0 | `12110` | `227510110` |
| 1.3 | **1** | 0 | `12110` | `227510110` |
| 3.9 | **9887** | 0 | `12110` | player has control (`401`) |
| 4.0 | 9887 | 4083 | `12110` | |
| 5.1 | 9887 | 4083 | `3003` (attacking again) | |

- The second health bar is **the same object in the same entry**: same entity ID, NPC ID,
  handle and address. HP is set to 1 by the deathblow and refilled to the maximum 2.6 s
  later; posture is refilled 0.1 s after that.
- The player's HP rose from 8 to 200 at the moment the boss's HP went to 1.
- The four dormant characters (entries 258, 262, 263, 264) did not change.
- Comparing dumps of the boss object before and after the deathblow found no small
  integer in its first 0x2000 bytes that changed and stayed changed, so a
  "deathblows remaining" count is not in the part of the object that was dumped. Until
  one is found, a deathblow can be read as: boss HP 1 with posture 0 and animation
  `12110`, followed by HP returning to the maximum.

### Restart 3 of 3 (2026-11-25, PID 6976)

The game was closed and started again; same executable hash and module base. Cheat
Engine not attached. Logged through one rematch fight of 30 s ending in the player's
death.

- Save loaded: HP 960 / 960, posture 360 / 360, the same position at the idol, death
  counter 398.
- Boss: block 2, entry 261, NPC ID `71001000`, entity ID `1110800`, found on all 466
  samples. Load sequence 7670 → one sample at 7690 → 9887.
- Boss HP 9887 → 8867 in three steps of 340; it never rose. Posture 3199 to 4083.
- Player: 960 → 711 → 212 → 25 → 0; death counter 398 → 399 after 3.8 s.
- Frame token: no repeat during the fight; the last four samples before the loading
  screen repeated.
- Read time with the boss loaded: median 0.25 ms, maximum 1.48 ms.

### Summary of 2026-11-25 and promotion to `live`

Four launches of the same executable (PIDs 28268, 29772, 17992, 6976), six rematch
fights, one deathblow, no victory. On every launch every chain resolved with no change
to any offset, and on every fight:

- the boss was the character with entity ID `1110800` and NPC ID `71001000`, at block 2,
  entry 261, and was found on every sample while it was loaded (5801 samples through
  `MemoryStateSource` over four fights);
- boss HP fell only in steps of 340 or 170, and rose only once, at the refill after
  the deathblow;
- the player's HP, posture, position and animation followed the fight, the death
  counter moved once per final death, and the frame token never repeated during play.

On this evidence these groups were set to `live` in `sekirorl/memory.py` `STATUS`:
`player_posture`, `player_position`, `player_animation` (with the frame token), `boss`
(for `GENICHIRO` only), `igt`, `death_count`. `player_hp` was already live.

Limits of this validation:

- Every sample above was read with `read(include_candidates=True)`. The default `read()`
  with the promoted table runs the same code but was not run against the game after the
  promotion, and neither was `StepLoop` or the smoke test.
- The user compared player HP and the boss health bar with the screen once (third
  fight). Posture, position and animation were compared with the user's actions on
  2026-11-20 and only with the course of the fights on 2026-11-25.
- The third health bar (probably entity `1110801`), a victory, and the story fight
  outside Reflection of Strength have not been seen.
- No Windows reboot between launches, and no area travel on 2026-11-25 other than into
  and out of the arena (twelve loading screens).
