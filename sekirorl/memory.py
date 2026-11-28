"""Read-only Sekiro 1.06 memory reader. See MEMORY_READER.md.

Standard library only. Opens the process with query/read/synchronize rights
and never writes, injects or freezes. `Reader` is the Windows API wrapper;
`MemoryStateSource` turns it into `Snapshot` objects for the environment.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import math
import os
import struct
import time
from typing import NamedTuple, Optional, Tuple
import zlib

from .snapshot import Snapshot

# Module-relative roots (address of a pointer slot), Sekiro 1.06.
WORLD_CHR_MAN_RVA = 0x3D7A1E0
GAME_DATA_RVA = 0x3D5AAC0
PLAYER_HP_OFFSETS = [0x88, 0x1FF8, 0x18, 0x130]

# Traversal, in order. `[x]` is a dereference; the last offset is never
# dereferenced.
#
#   wcm     = [base + WORLD_CHR_MAN_RVA]              WorldChrManImp
#   player  = [wcm + 0x88]                            character object
#   table   = [character + 0x1FF8]                    module table
#   data    = [table + 0x18]                          SprjChrDataModule
#   physics = [table + 0x68]                          SprjChrPhysicsModule
#   timeact = [table + 0x10]                          SprjChrTimeActModule
#
# Enemy characters use the same layout from `character` down.
WCM_PLAYER = 0x88

CHR_HANDLE = 0x08           # u32; low bits are the entry index in its block
CHR_NPC_ID = 0x68           # i32; the same for every copy of an enemy type
CHR_ENTITY_ID = 0x1A24      # i32 map entity ID; unique to one placed character
CHR_MODULES = 0x1FF8        # pointer to the module table
MODULE_TIME_ACT = 0x10
MODULE_DATA = 0x18
MODULE_PHYSICS = 0x68
DATA_HP = 0x130             # i32 HP, i32 max HP
DATA_POSTURE = 0x148        # i32 posture, i32 max posture. Counts DOWN: max = empty bar
PHYSICS_POSITION = 0x80     # 3 x f32; the second value is height

# TimeAct module: a ring of ten entries {i32 unknown, i32 animation id,
# f32 elapsed, f32 elapsed next frame, f32 length}. The game pushes one entry
# per playing layer every frame, so the whole block is read in one call and the
# current full-body entry is looked up by the slot stored at +0xEC.
RING_START = 0x1C
RING_ENTRY = 0x14
RING_SLOTS = 10
RING_CURRENT_SLOT = 0xEC    # i32
RING_END = 0xF4

# Character collection: WorldChrMan holds 20 WorldBlockChr structs inline.
#   block   = wcm + 0x910 + 0x140 * b                 (no dereference)
#   entries = [block + 0x88]                          array of 0x38-byte entries
#   character = [entries + 0x38 * i]                  null when not loaded
WCM_BLOCKS = 0x910
BLOCK_SIZE = 0x140
BLOCK_COUNT = 20
BLOCK_CAPACITY = 0x80       # i32
BLOCK_ENTRIES = 0x88        # pointer
ENTRY_SIZE = 0x38
MAX_BLOCK_CAPACITY = 4096   # sanity limit on a capacity read from memory

# Boss identities as (map entity ID, NPC ID). The NPC ID alone is not enough:
# the arena also holds a dormant character with the same NPC ID (entity 1110890).
GENICHIRO = (1110800, 71001000)             # tower fight, first two health bars
GENICHIRO_WAY_OF_TOMOE = (1110801, 71100000)    # third health bar; seen dormant only

# Lock-on: [base + LOCK_TGT_MAN_RVA] is the lock-on manager. The u32 at +0x28
# is the handle (CHR_HANDLE) of the locked character, or 0xFFFFFFFF with no
# lock. Found on 2026-11-27 by pressing lock-on eight times in one fight; see
# MEMORY_READER.md. Not yet checked across game restarts.
LOCK_TGT_MAN_RVA = 0x3D78058
LOCK_TARGET_HANDLE = 0x28
NO_LOCK_TARGET = 0xFFFFFFFF

GAME_DATA_DEATHS = 0x90     # i32, from [base + GAME_DATA_RVA]
GAME_DATA_IGT = 0x9C        # u32 milliseconds

# Healing Gourd charges: the quantity of one entry of the player's item list.
# [[base + GAME_DATA_RVA] + 0x0] is the object that holds the list; in it,
# +0x1018 points to the entries and the i32 at +0x1020 is how many are in use.
# An entry is 16 bytes: u32 handle, u32 item ID, i32 quantity, u32 other.
# Found on 2026-11-27 (PID 16608): the quantity went 9 -> 3 over six drinks the
# user counted, and matched the HUD at both ends. One game session only; see
# MEMORY_READER.md. The entry's position (18 that day) is looked up by item ID.
GAME_DATA_ITEMS = 0x0
ITEMS_ENTRIES = 0x1018
ITEMS_COUNT = 0x1020
ITEM_ENTRY_SIZE = 0x10
ITEM_ID = 0x4
ITEM_QUANTITY = 0x8
MAX_ITEM_ENTRIES = 2048
HEALING_GOURD_ITEM = 0x40000BB8
MAX_GOURD_CHARGES = 10      # the game's limit; a count outside 0..10 is not believed

# Verification status of each group of snapshot fields. "live" means checked
# against the running game as recorded in MEMORY_READER.md. A "candidate" is
# never placed in a snapshot, so its observation stays masked.
STATUS = {
    "player_hp": "live",
    "player_posture": "live",
    "player_position": "live",
    "player_animation": "live",         # also supplies the frame token (pause check)
    "boss": "live",                     # GENICHIRO only; see MEMORY_READER.md
    "igt": "live",
    "death_count": "live",
    "lock_on": "live",                  # one game session only; see LOCK_TGT_MAN_RVA
    "gourds": "live",                   # one game session only; see GAME_DATA_ITEMS
}


class Unavailable(Exception):
    pass


def valid_address(value):
    return 0x10000 <= value < 0x0000800000000000


def check_hp_pair(hp, maximum):
    if maximum <= 0 or not 0 <= hp <= maximum:
        raise Unavailable(f"Implausible HP pair: {hp}/{maximum}")
    return hp, maximum


class CharacterState(NamedTuple):
    """One character object read in a single pass. `None` means unavailable."""
    handle: int
    npc_id: int
    entity_id: int
    hp: int
    max_hp: int
    posture: Optional[int]
    max_posture: Optional[int]
    position: Optional[Tuple[float, float, float]]
    anim_id: Optional[int]
    anim_elapsed: Optional[float]
    frame_token: Optional[int]      # changes whenever the animation ring changes


class Reader:
    def __init__(self, pid):
        if os.name != "nt" or C.sizeof(C.c_void_p) != 8:
            raise RuntimeError("Use 64-bit Python on Windows")
        self.k = C.WinDLL("kernel32", use_last_error=True)
        self.p = C.WinDLL("psapi", use_last_error=True)
        self.k.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        self.k.OpenProcess.restype = W.HANDLE
        self.k.CloseHandle.argtypes = [W.HANDLE]
        self.k.CloseHandle.restype = W.BOOL
        self.k.ReadProcessMemory.argtypes = [W.HANDLE, C.c_void_p, C.c_void_p,
                                           C.c_size_t, C.POINTER(C.c_size_t)]
        self.k.ReadProcessMemory.restype = W.BOOL
        self.k.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        self.k.WaitForSingleObject.restype = W.DWORD
        self.p.EnumProcessModulesEx.argtypes = [W.HANDLE, C.POINTER(W.HMODULE),
                                               W.DWORD, C.POINTER(W.DWORD), W.DWORD]
        self.p.EnumProcessModulesEx.restype = W.BOOL
        self.p.GetModuleBaseNameW.argtypes = [W.HANDLE, W.HMODULE, W.LPWSTR, W.DWORD]
        self.p.GetModuleBaseNameW.restype = W.DWORD
        self.k.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR,
                                                    C.POINTER(W.DWORD)]
        self.k.QueryFullProcessImageNameW.restype = W.BOOL
        # QUERY_INFORMATION | VM_READ | SYNCHRONIZE. No write/operation rights.
        self.handle = self.k.OpenProcess(0x400 | 0x10 | 0x100000, False, pid)
        if not self.handle:
            raise C.WinError(C.get_last_error())

    def close(self):
        if self.handle:
            self.k.CloseHandle(self.handle)
            self.handle = None

    def image_path(self):
        size = W.DWORD(32768)
        name = C.create_unicode_buffer(size.value)
        if not self.k.QueryFullProcessImageNameW(self.handle, 0, name, C.byref(size)):
            raise C.WinError(C.get_last_error())
        return name.value

    def running(self):
        result = self.k.WaitForSingleObject(self.handle, 0)
        if result == 0xFFFFFFFF:
            raise C.WinError(C.get_last_error())
        return result == 0x102

    def module_base(self):
        count = 1024
        while True:
            modules = (W.HMODULE * count)()
            needed = W.DWORD()
            if not self.p.EnumProcessModulesEx(self.handle, modules, C.sizeof(modules),
                                               C.byref(needed), 3):
                raise C.WinError(C.get_last_error())
            if needed.value <= C.sizeof(modules):
                break
            count = needed.value // C.sizeof(W.HMODULE) + 16
        for module in modules[:needed.value // C.sizeof(W.HMODULE)]:
            name = C.create_unicode_buffer(260)
            if self.p.GetModuleBaseNameW(self.handle, module, name, len(name)):
                if name.value.lower() == "sekiro.exe":
                    return module
        raise RuntimeError("sekiro.exe module not found in specified PID")

    def read(self, address, size):
        if not valid_address(address):
            raise Unavailable(f"Invalid/null address: {address:#x}")
        data = C.create_string_buffer(size)
        got = C.c_size_t()
        if not self.k.ReadProcessMemory(self.handle, address, data, size, C.byref(got)):
            raise Unavailable(f"Read failed at {address:#x}: WinError {C.get_last_error()}")
        if got.value != size:
            raise Unavailable("Partial read")
        return data.raw

    def pointer(self, address):
        value = struct.unpack("<Q", self.read(address, 8))[0]
        if not valid_address(value):
            raise Unavailable(f"Invalid/null pointer at {address:#x}")
        return value

    def resolve(self, root, offsets):
        # Root is address of a pointer slot; final offset is NOT dereferenced.
        address = root
        trace = [address]
        for offset in offsets:
            address = self.pointer(address) + offset
            trace.append(address)
        return address, trace

    def read_chain(self, root, offsets, fmt):
        """Resolve, read one struct in a single call, resolve again.

        Raises Unavailable on a null/unreadable pointer, a partial read or a
        chain that changed during the read. Not an atomic game snapshot.
        """
        address, trace = self.resolve(root, offsets)
        values = struct.unpack(fmt, self.read(address, struct.calcsize(fmt)))
        _, again = self.resolve(root, offsets)
        if trace != again:
            raise Unavailable("Pointer chain changed during sample")
        return values

    def _modules(self, character):
        """Module table address and the TimeAct, Data and Physics pointers."""
        table = self.pointer(character + CHR_MODULES)
        raw = self.read(table, MODULE_PHYSICS + 8)
        return (table,) + tuple(struct.unpack_from("<Q", raw, offset)[0] for offset in
                                (MODULE_TIME_ACT, MODULE_DATA, MODULE_PHYSICS))

    def read_character(self, character):
        """Read one character object (player or enemy) at address `character`.

        Resolves the module table, reads each module in one call, then resolves
        the table again. Raises Unavailable on a failed or partial read, an
        implausible HP pair or a table that changed during the read. Posture,
        position and animation are None when their own values are implausible
        or their module pointer is null. Not an atomic game snapshot.
        """
        modules = self._modules(character)
        _, time_act, data, physics = modules
        head = self.read(character, CHR_NPC_ID + 4)
        handle, = struct.unpack_from("<I", head, CHR_HANDLE)
        npc_id, = struct.unpack_from("<i", head, CHR_NPC_ID)
        entity_id, = struct.unpack("<i", self.read(character + CHR_ENTITY_ID, 4))

        block = self.read(data + DATA_HP, DATA_POSTURE + 8 - DATA_HP)
        hp, max_hp = check_hp_pair(*struct.unpack_from("<ii", block, 0))
        posture, max_posture = struct.unpack_from("<ii", block, DATA_POSTURE - DATA_HP)
        if max_posture <= 0 or not 0 <= posture <= max_posture:
            posture = max_posture = None

        position = None
        if valid_address(physics):
            position = struct.unpack("<fff", self.read(physics + PHYSICS_POSITION, 12))
            if not all(math.isfinite(value) for value in position):
                position = None

        anim_id = anim_elapsed = frame_token = None
        if valid_address(time_act):
            ring = self.read(time_act + RING_START, RING_END - RING_START)
            frame_token = zlib.crc32(ring)
            slot, = struct.unpack_from("<i", ring, RING_CURRENT_SLOT - RING_START)
            if 0 <= slot < RING_SLOTS:
                _, anim_id, anim_elapsed, _, _ = struct.unpack_from("<iifff", ring,
                                                                    RING_ENTRY * slot)
                if not math.isfinite(anim_elapsed):
                    anim_id = anim_elapsed = None

        if self._modules(character) != modules:
            raise Unavailable("Module table changed during sample")
        return CharacterState(handle, npc_id, entity_id, hp, max_hp, posture, max_posture, position,
                              anim_id, anim_elapsed, frame_token)

    def sample(self, root, offsets):
        try:
            address, trace = self.resolve(root, offsets)
            hp, maximum = struct.unpack("<ii", self.read(address, 8))
            _, again = self.resolve(root, offsets)
            if trace != again:
                raise Unavailable("Pointer chain changed during sample")
            if maximum <= 0 or not 0 <= hp <= maximum:
                raise Unavailable(f"Implausible HP pair: {hp}/{maximum}")
            return {"status": "candidate", "hp": hp, "max_hp": maximum,
                    "address": hex(address), "trace": [hex(x) for x in trace]}
        except Unavailable as error:
            return {"status": "unavailable", "hp": None, "max_hp": None,
                    "reason": str(error)}


class MemoryStateSource:
    """StateSource backed by a live process. Fills verified fields only.

    `reader` needs `read`, `pointer`, `read_character` and `running`, so tests
    can back a `Reader` with simulated memory instead of a Windows process.

    `boss` is the (map entity ID, NPC ID) pair of the boss, for example
    `GENICHIRO`. The boss is looked up in WorldChrMan's character collection.
    Its heap address is never kept between samples: only the (block, entry)
    position is remembered, the pointer is resolved from the static root on
    every sample, and both IDs are checked on the object that was actually read.
    """

    def __init__(self, reader, base, boss=None, status=None):
        self.reader = reader
        self.base = base
        self.boss = tuple(boss) if boss is not None else None
        self.status = dict(STATUS if status is None else status)
        self._boss_slot = None
        self._gourd_entry = None

    def _live(self, name):
        return self.status[name] == "live"

    # -- player ------------------------------------------------------------

    def _player(self):
        reader = self.reader
        wcm = reader.pointer(self.base + WORLD_CHR_MAN_RVA)
        character = reader.pointer(wcm + WCM_PLAYER)
        state = reader.read_character(character)
        if (reader.pointer(self.base + WORLD_CHR_MAN_RVA) != wcm
                or reader.pointer(wcm + WCM_PLAYER) != character):
            raise Unavailable("Pointer chain changed during sample")
        return wcm, state

    # -- boss --------------------------------------------------------------

    def _entry(self, wcm, block, index):
        """Character pointer in one collection entry, or None when empty."""
        header = self.reader.read(wcm + WCM_BLOCKS + BLOCK_SIZE * block + BLOCK_CAPACITY, 16)
        capacity, entries = struct.unpack("<i4xQ", header)
        if not index < capacity <= MAX_BLOCK_CAPACITY or not valid_address(entries):
            return None
        character, = struct.unpack("<Q", self.reader.read(entries + ENTRY_SIZE * index, 8))
        return character if valid_address(character) else None

    def _identity(self, character):
        """(map entity ID, NPC ID) of a character object."""
        return (struct.unpack("<i", self.reader.read(character + CHR_ENTITY_ID, 4))[0],
                struct.unpack("<i", self.reader.read(character + CHR_NPC_ID, 4))[0])

    def characters(self, wcm):
        """Yield (block, index, character pointer) for every loaded character."""
        blocks = self.reader.read(wcm + WCM_BLOCKS, BLOCK_SIZE * BLOCK_COUNT)
        for block in range(BLOCK_COUNT):
            capacity, entries = struct.unpack_from("<i4xQ", blocks,
                                                   BLOCK_SIZE * block + BLOCK_CAPACITY)
            if not 0 < capacity <= MAX_BLOCK_CAPACITY or not valid_address(entries):
                continue
            try:
                array = self.reader.read(entries, ENTRY_SIZE * capacity)
            except Unavailable:
                continue
            for index in range(capacity):
                character, = struct.unpack_from("<Q", array, ENTRY_SIZE * index)
                if valid_address(character):
                    yield block, index, character

    def _find_boss(self, wcm):
        if self._boss_slot is not None:
            character = self._entry(wcm, *self._boss_slot)
            if character is not None and self._identity(character) == self.boss:
                return character
            self._boss_slot = None
        matches = []
        for block, index, character in self.characters(wcm):
            try:
                if self._identity(character) == self.boss:
                    matches.append((block, index, character))
            except Unavailable:
                continue
        if not matches:
            raise Unavailable("Boss not in the character collection")
        if len(matches) > 1:
            raise Unavailable(f"{len(matches)} characters match the boss identity")
        block, index, character = matches[0]
        self._boss_slot = (block, index)
        return character

    def _boss(self, wcm):
        character = self._find_boss(wcm)
        state = self.reader.read_character(character)
        if (state.entity_id, state.npc_id) != self.boss:
            raise Unavailable(f"Boss identity check failed: entity {state.entity_id}, "
                              f"NPC ID {state.npc_id}")
        if self._entry(wcm, *self._boss_slot) != character:
            raise Unavailable("Boss entry changed during sample")
        return state

    # -- snapshot ----------------------------------------------------------

    def _lock_target(self):
        """Handle of the locked character, NO_LOCK_TARGET, or None if unreadable."""
        try:
            manager = self.reader.pointer(self.base + LOCK_TGT_MAN_RVA)
            return struct.unpack("<I", self.reader.read(manager + LOCK_TARGET_HANDLE, 4))[0]
        except Unavailable:
            return None

    def _gourds(self):
        """Healing Gourd charges left, or None if the entry cannot be found.

        The entry's position is remembered, never its address, and the item
        ID is checked on every read.
        """
        reader = self.reader
        try:
            game_data = reader.pointer(self.base + GAME_DATA_RVA)
            items = reader.pointer(game_data + GAME_DATA_ITEMS)
            entries = reader.pointer(items + ITEMS_ENTRIES)
            raw = None
            if self._gourd_entry is not None:
                raw = reader.read(entries + ITEM_ENTRY_SIZE * self._gourd_entry, ITEM_ENTRY_SIZE)
                if struct.unpack_from("<I", raw, ITEM_ID)[0] != HEALING_GOURD_ITEM:
                    raw = self._gourd_entry = None
            if raw is None:
                count, = struct.unpack("<i", reader.read(items + ITEMS_COUNT, 4))
                if not 0 < count <= MAX_ITEM_ENTRIES:
                    return None
                table = reader.read(entries, ITEM_ENTRY_SIZE * count)
                for index in range(count):
                    start = ITEM_ENTRY_SIZE * index
                    if struct.unpack_from("<I", table, start + ITEM_ID)[0] == HEALING_GOURD_ITEM:
                        self._gourd_entry = index
                        raw = table[start:start + ITEM_ENTRY_SIZE]
                        break
                else:
                    return None
        except Unavailable:
            return None
        charges, = struct.unpack_from("<i", raw, ITEM_QUANTITY)
        return charges if 0 <= charges <= MAX_GOURD_CHARGES else None

    def _game_data(self):
        game_data = self.reader.pointer(self.base + GAME_DATA_RVA)
        raw = self.reader.read(game_data + GAME_DATA_DEATHS, GAME_DATA_IGT + 4 - GAME_DATA_DEATHS)
        deaths, = struct.unpack_from("<i", raw, 0)
        igt_ms, = struct.unpack_from("<I", raw, GAME_DATA_IGT - GAME_DATA_DEATHS)
        return deaths, igt_ms

    def read(self, include_candidates=False):
        """One Snapshot. Candidate fields are filled only for manual checking."""
        want = (lambda name: True) if include_candidates else self._live
        t_ns = time.perf_counter_ns()
        try:
            if not self.reader.running():
                raise Unavailable("Process exited")
            wcm, player = self._player()
        except Unavailable as error:
            return Snapshot(t_ns=t_ns, reason=str(error))
        fields = {"player_hp": player.hp, "player_max_hp": player.max_hp}
        if want("player_posture"):
            fields.update(player_posture=player.posture, player_max_posture=player.max_posture)
        if want("player_position"):
            fields.update(player_pos=player.position)
        if want("player_animation"):
            fields.update(player_anim_id=player.anim_id, player_anim_elapsed=player.anim_elapsed,
                          frame_token=player.frame_token)
        if self.boss is not None and want("boss"):
            try:
                boss = self._boss(wcm)
            except Unavailable as error:
                fields.update(boss_reason=str(error))
            else:
                fields.update(boss_npc_id=boss.npc_id, boss_hp=boss.hp, boss_max_hp=boss.max_hp,
                              boss_posture=boss.posture, boss_max_posture=boss.max_posture,
                              boss_pos=boss.position, boss_anim_id=boss.anim_id,
                              boss_anim_elapsed=boss.anim_elapsed)
                if want("lock_on"):
                    target = self._lock_target()
                    if target is not None:
                        fields.update(locked_on=target == boss.handle)
        if want("igt") or want("death_count"):
            try:
                deaths, igt_ms = self._game_data()
            except Unavailable:
                pass
            else:
                if want("igt"):
                    fields.update(igt_ms=igt_ms)
                if want("death_count"):
                    fields.update(death_count=deaths)
        if want("gourds"):
            fields.update(gourds=self._gourds())
        return Snapshot(t_ns=t_ns, **fields)

    def probe_candidates(self):
        """Read unverified fields for manual checking. Never used as observations."""
        live = self.read()
        everything = self.read(include_candidates=True)
        if not everything.valid:
            return {"unavailable": everything.reason}
        skip = ("t_ns", "reason")
        return {name: value for name, value in vars(everything).items()
                if name not in skip and getattr(live, name) is None and value is not None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--boss-rva", type=lambda s: int(s, 0))
    parser.add_argument("--boss-offsets", help="Hex offsets in traversal order, comma separated")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if (args.boss_rva is None) != (args.boss_offsets is None):
        parser.error("Supply both --boss-rva and --boss-offsets")
    boss_offsets = None
    if args.boss_offsets is not None:
        try:
            boss_offsets = [int(x.strip(), 16) for x in args.boss_offsets.split(",")]
        except ValueError:
            parser.error("Boss offsets must be comma-separated hexadecimal integers")
    reader = Reader(args.pid)
    try:
        base = reader.module_base()
        while reader.running():
            sample = {"time_ns": time.time_ns(), "pid": args.pid, "base": hex(base),
                      "player": reader.sample(base + WORLD_CHR_MAN_RVA,
                                              PLAYER_HP_OFFSETS)}
            sample["boss"] = (reader.sample(base + args.boss_rva, boss_offsets)
                              if boss_offsets is not None else
                              {"status": "unconfigured", "hp": None, "max_hp": None})
            print(json.dumps(sample), flush=True)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        reader.close()


if __name__ == "__main__":
    main()
