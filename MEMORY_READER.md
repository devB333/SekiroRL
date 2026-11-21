# Sekiro external HP reader

The supplied executable version is 1.6.0.0. Use the community's 1.06
offsets as candidates, then validate against your executable. Version text
alone does not establish binary identity. This workspace has not been tested
against a running game.

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
