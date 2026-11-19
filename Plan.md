# Plan: memory-observation Gymnasium environment for a Sekiro boss agent

Date: 2026-11-18. Target build: Sekiro 1.06 (`sekiro.exe` file version 1.6.0.0).

## 1. Verdict

**The idea is valid.** Reading game state through module-relative pointer chains is a workable
observation source for a Gymnasium environment, and an MLP can plausibly learn an early boss
from it. It is only valid if four conditions are met, and the pointer list in section 4 is
built around them:

1. **The boss's animation ID is in the observation.** HP and posture alone do not tell the
   agent what attack is coming, so no policy can learn to deflect from them. Animation ID plus
   elapsed animation time plus distance makes the state close to Markov, which is what lets a
   plain MLP (no recurrence, no pixels) work.
2. **The boss character is found by identity, not by a plausible HP value.** This is the largest
   unknown. No source we have gives a read-only static route to a specific enemy.
3. **Episodes can be reset without memory writes.** The read-only contract rules out teleport and
   HP restore, so reset has to be done by scripted inputs.
4. **The training budget fits real time.** Without writes the game cannot be sped up or run in
   parallel. At 10 decisions per second, 1 million steps is about 28 hours of fight time, plus
   reset overhead.

## 2. What is established and what is not

| Item | Status | Evidence |
|---|---|---|
| Player HP / max HP chain | Working live, one process run | `session-01.jsonl`: 2,107 samples over 211 s at a 100 ms median interval. HP read 320/320, then 240, 176, 96, 24 as damage was taken. |
| Loading / no-character detection | Working live | The same log ends with 185 "unavailable" samples: first the player slot was null, then the WorldChrMan root itself. No fabricated zeros. |
| Player death (HP = 0) | Not yet observed | The log goes from 24 straight to unavailable. |
| Restart stability | Not yet shown | One PID, one module base (`0x140000000`). `MEMORY_READER.md` requires three restarts. |
| Everything else in section 4 | Candidate offsets from two community sources | Not tested against this executable. |
| Route to the boss character | Unknown | Neither source has one. |

Two housekeeping notes:

- `session-01.jsonl` is UTF-16 because PowerShell's `>` wrote it. Any loader must open it with
  `encoding="utf-16"`, or the log should be written with `| Out-File -Encoding utf8`.
- `PointerMaps/Health/` holds a Cheat Engine pointer scan with one small non-empty result file.
  I did not decode it and nothing below relies on it.

## 3. Design constraints that shape the pointer list

- **Reset.** Recommended: train in a *Reflection of Strength* (boss rematch from a Sculptor's
  Idol, added in patch 1.05). Dying there should return to the idol with no penalty, and the
  rematch is started from a menu, so reset is a short fixed input sequence. This needs a save
  where the boss is already beaten, and only true bosses are offered (Gyoubu Oniwa, Lady
  Butterfly, Genichiro), not mini-bosses. **Check this in game before committing to it.** The
  fallback is die, respawn at the idol, and run to the arena by script, which is slower and
  more fragile.
- **Multiple health bars.** Boss HP refills after each deathblow. Boss HP reaching 0 is not
  victory, so the environment needs either a deathblow count or a boss-defeated signal.
- **Non-atomic reads.** Samples are sequential `ReadProcessMemory` calls. Keep the existing
  re-resolve check and read each struct in one call where fields are adjacent.
- **Actions are not memory.** Input goes through a virtual gamepad or `SendInput`, which stays
  inside the read-only contract. The game window must keep focus.
- **Animation IDs are categories.** Feed them as one-hot or an embedding index built from the
  IDs seen in recorded fights, never as a raw integer.

## 4. Pointers needed

Notation follows `read_health.py`: the root is the address of a pointer slot, each offset is
added after a dereference, and the final offset is not dereferenced. `B` is the loaded base of
`sekiro.exe`.

Status: **Live** = seen working in `session-01.jsonl`. **Candidate** = documented for 1.06 in a
community source, untested here. **Find** = no known read-only route.

### 4.1 Static roots

| Root | Address | Status | Used for |
|---|---|---|---|
| WorldChrMan | `B + 0x3D7A1E0` | Live | Player, and probably the character collection |
| GameData | `B + 0x3D5AAC0` | Candidate | In-game time, death counter |
| LockTgtMan | `B + 0x3D78058` | Candidate root, layout unknown | Lock-on target |
| EventFlagMan | `B + 0x3D55FE8` | Candidate root, layout unknown | Boss-defeated flag |
| FieldArea | `B + 0x3D5C0A0` | Candidate | Camera (optional) |

### 4.2 Player (`P` = player character = `[[B + 0x3D7A1E0] + 0x88]`)

Chains start at the WorldChrMan root.

| Value | Offsets | Type | Status | Priority |
|---|---|---|---|---|
| HP | `88, 1FF8, 18, 130` | i32 | Live | Must |
| Max HP | `88, 1FF8, 18, 134` | i32 | Live | Must |
| Posture | `88, 1FF8, 18, 148` | i32 | Candidate | Must |
| Max posture | `88, 1FF8, 18, 14C` | i32 | Candidate | Must |
| Position X, Y, Z | `88, 1FF8, 68, 80` (+4, +8) | 3 x f32 | Candidate | Must |
| Animation ID | `88, 1FF8, 10, 20` | i32 | Candidate | Must |
| Animation elapsed (s) | `88, 1FF8, 10, 24` | f32 | Candidate | Must |
| Animation name | `88, 1FF8, 28, 878` | UTF-16, 32 chars | Candidate | Debug aid |
| Active effect list | head at `88, 11D0`; each node: next at `+8`, ID at `+58`, duration at `+0` | list | Candidate | Should |
| Facing angle | none known | f32 | Find | Optional |
| Healing gourd charges | none known | i32 | Find | Should |
| Resurrection charges | none known | i32 | Find | Optional |

Notes:

- Posture is stored as "stamina". Check which way it moves: it may count down from max as the
  posture bar fills.
- Facing matters little while locked on, since the player turns toward the target.
- Gourd charges can be approximated by counting uses in the environment, but a use can be
  interrupted, so a real read is better.
- The effect list is the likely place to detect states such as guarding or status build-up.

### 4.3 Boss (`E` = boss character pointer, route in 4.4)

Offsets are relative to `E`, the same layout as the player.

| Value | Offsets from `E` | Type | Status | Priority |
|---|---|---|---|---|
| HP / max HP | `1FF8, 18, 130` / `134` | i32 | Candidate | Must |
| Posture / max posture | `1FF8, 18, 148` / `14C` | i32 | Candidate | Must |
| Position X, Y, Z | `1FF8, 68, 80` | 3 x f32 | Candidate | Must |
| Animation ID | `1FF8, 10, 20` | i32 | Candidate | Must |
| Animation elapsed (s) | `1FF8, 10, 24` | f32 | Candidate | Must |
| NPC ID (identity) | `30, 628` | i32 | Candidate | Must |
| Think ID | `30, 624` | i32 | Candidate | Should |
| Character ID | `68` (direct) | i32 | Candidate | Should |
| Entity handle | `08` (direct) | u32 | Candidate | Should |
| Team type | `74` (direct) | u8 | Candidate | Optional |
| Poise / max poise | `1FF8, 40, 28` / `2C` | f32 | Candidate | Optional |
| Deathblows remaining | none known | i32 | Find | Must (or use 4.5) |

### 4.4 Route to the boss character: the main thing to find

Three candidate routes, in the order I recommend trying them:

1. **Enumerate WorldChrMan's character collection** and pick the entry whose NPC ID matches the
   boss. Most robust: it does not depend on lock-on and proves identity. The collection layout
   is unknown and has to be worked out in Memory View.
2. **LockTgtMan to the locked target.** Simple if it holds a pointer or handle, but it only
   works while locked on and can point at another enemy. Always confirm with NPC ID.
3. **Pointer scan to a module-relative chain**, as described in `MEMORY_READER.md`. A fixed chain
   may select a different actor after a reload, so it also needs the NPC ID check.

Read-only shortcut for all three: once the boss HP address `A` is found by value scan, walk
backwards instead of pointer-scanning for `A`. `D = A - 0x130`; search for the 8-byte value
`D` to find the slot at `S + 0x18`; search for `S` to find the slot at `E + 0x1FF8`. That gives
`E` itself. Pointer-scanning for `E` needs three fewer levels, and `E` is the value to look for
inside WorldChrMan and LockTgtMan.

The cheat table's "Targeted NPC" entry gets `E` by code injection. It is outside the read-only
contract and is not part of this plan.

### 4.5 Episode and encounter signals

| Signal | Route | Status | Priority |
|---|---|---|---|
| Loading / no character | WorldChrMan root or player slot null | Live | Must |
| In-game time (ms) | GameData root, `9C`, u32 | Candidate | Should |
| Player death count | GameData root, `90`, i32 | Candidate | Should |
| Player dead | HP = 0, cross-checked with death count | Candidate | Must |
| Boss defeated | event flag via EventFlagMan, or deathblow count | Find | Must |
| Lock-on active | LockTgtMan | Find | Should |
| Player has control (no cutscene or menu) | none known | Find | Should |

In-game time is also a cheap liveness check: if it stops advancing, the game is paused or in a
menu and the step should not be counted.

## 5. Order of work

1. Finish validating player HP per `MEMORY_READER.md` (death, rest, three restarts).
2. Add the player candidates: posture, position, animation ID and time. These are four cheap
   Cheat Engine entries on a chain that already works.
3. Check GameData in-game time and death count.
4. Confirm the Reflection of Strength reset in game and pick the boss.
5. Find `E` for that boss with the back-walk in 4.4, then confirm the boss suffixes in 4.3,
   including NPC ID.
6. Find a stable route to `E` (4.4) and test it across retries, reloads and restarts.
7. Find the boss-defeated signal and the deathblow count.
8. Find gourd charges, lock-on state and the control flag.
9. Record a few full fights to JSONL, build the animation-ID vocabulary, then write the
   Gymnasium `reset` / `step` around the reader.

## 6. Sources

- [ElaDiDu Sekiro practice table](https://github.com/ElaDiDu/Sekiro-Practice-CT/blob/main/Ela_Sekiro_Table.CT):
  1.06 static roots, player and targeted-NPC field offsets, effect list, GameData fields.
- [sekiro-coop `live.rs`](https://github.com/mstampfli/sekiro-coop/blob/main/crates/sekiro-sdk-sys/src/live.rs)
  and [`offsets.rs`](https://github.com/mstampfli/sekiro-coop/blob/main/crates/sekiro-sdk-sys/src/offsets.rs):
  independent agreement on the WorldChrMan RVA and on HP, posture, position, animation ID,
  animation time, handle, character ID and team type.

Where both sources list a field they agree. Agreement between community sources is still not
validation against this executable.
