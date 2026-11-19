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
