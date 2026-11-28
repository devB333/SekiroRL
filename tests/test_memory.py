"""Reader and MemoryStateSource against simulated process memory (any OS)."""
import ctypes
import struct
import unittest
from unittest import mock

from sekirorl import memory
from sekirorl.memory import MemoryStateSource, Reader

BASE = 0x140000000
ROOT = BASE + memory.WORLD_CHR_MAN_RVA
OFFSETS = memory.PLAYER_HP_OFFSETS
W, CHR, S, D = 0x7FF400000000, 0x7FF400010000, 0x7FF400020000, 0x7FF400030000
ENTRIES = 0x7FF500000000


class FakeKernel:
    """Stands in for kernel32: serves ReadProcessMemory from simulated regions.

    A read succeeds only when it lies wholly inside one region, so a wrong
    offset or size fails the way an unmapped address does.
    """

    def __init__(self, hp=320, maximum=320):
        self.regions = {}
        self.fail = set()       # read addresses that fail
        self.partial = set()    # read addresses that return one byte too few
        self.calls = 0
        self.on_call = None     # called with the call number before each read
        self.after_read = None  # called with the call number after each successful read
        self.last = None        # address of the last successful read
        self.put(ROOT, struct.pack("<Q", W))
        self.put(W, bytes(memory.WCM_BLOCKS + memory.BLOCK_SIZE * memory.BLOCK_COUNT))
        self.put(W + 0x88, struct.pack("<Q", CHR))
        self.add_character(CHR, S, hp=hp, max_hp=maximum, npc_id=0, handle=0x10050000)

    def put(self, address, data):
        """Write bytes, creating a region if the address is not inside one."""
        for base, region in self.regions.items():
            if base <= address and address + len(data) <= base + len(region):
                region[address - base:address - base + len(data)] = data
                return
        self.regions[address] = bytearray(data)

    def add_character(self, character, table, hp=320, max_hp=320, posture=(120, 120),
                      position=(1.0, 2.0, 3.0), anim=(400, 0.5), slot=3, npc_id=0, handle=0,
                      entity_id=0):
        """A character object with its module table at `table`.

        Modules sit at fixed distances after the table: Data +0x10000 (so the
        player's is `D`), Physics +0x1000, TimeAct +0x2000.
        """
        data, physics, time_act = table + 0x10000, table + 0x1000, table + 0x2000
        self.put(character, bytes(0x2000))
        self.put(character + memory.CHR_HANDLE, struct.pack("<I", handle))
        self.put(character + memory.CHR_NPC_ID, struct.pack("<i", npc_id))
        self.put(character + memory.CHR_ENTITY_ID, struct.pack("<i", entity_id))
        self.put(character + memory.CHR_MODULES, struct.pack("<Q", table))
        self.put(table, bytes(0x100))
        self.put(table + memory.MODULE_TIME_ACT, struct.pack("<Q", time_act))
        self.put(table + memory.MODULE_DATA, struct.pack("<Q", data))
        self.put(table + memory.MODULE_PHYSICS, struct.pack("<Q", physics))
        self.put(data, bytes(0x200))
        self.put(data + memory.DATA_HP, struct.pack("<ii", hp, max_hp))
        self.put(data + memory.DATA_POSTURE, struct.pack("<ii", *posture))
        self.put(physics, bytes(0x100))
        self.put(physics + memory.PHYSICS_POSITION, struct.pack("<fff", *position))
        self.put(time_act, bytes(0x100))
        self.set_animation(table, anim, slot)

    def set_animation(self, table, anim, slot):
        """Put `anim` in ring entry `slot`, a stale upper-body entry in slot 0."""
        time_act = table + 0x2000
        self.put(time_act + memory.RING_START, struct.pack("<iifff", 0, 790010, 9.0, 9.0, 9.0))
        if 0 <= slot < memory.RING_SLOTS:
            self.put(time_act + memory.RING_START + memory.RING_ENTRY * slot,
                     struct.pack("<iifff", 0, anim[0], anim[1], anim[1], 2.0))
        self.put(time_act + memory.RING_CURRENT_SLOT, struct.pack("<i", slot))

    def set_entry(self, block, index, character, capacity=512):
        """Place a character pointer in the collection (0 empties the entry)."""
        entries = ENTRIES + 0x100000 * block
        header = W + memory.WCM_BLOCKS + memory.BLOCK_SIZE * block
        if not any(base == entries for base in self.regions):
            self.put(entries, bytes(memory.ENTRY_SIZE * capacity))
            self.put(header + memory.BLOCK_CAPACITY, struct.pack("<i", capacity))
            self.put(header + memory.BLOCK_ENTRIES, struct.pack("<Q", entries))
        self.put(entries + memory.ENTRY_SIZE * index, struct.pack("<Q", character))

    def ReadProcessMemory(self, handle, address, buffer, size, got_ref):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        if address in self.fail:
            return False
        for base, region in self.regions.items():
            if base <= address and address + size <= base + len(region):
                data = bytes(region[address - base:address - base + size])
                break
        else:
            return False
        if address in self.partial:
            data = data[:size - 1]
        ctypes.memmove(buffer, data, len(data))
        got_ref._obj.value = len(data)
        self.last = address
        if self.after_read:
            self.after_read(self.calls)
        return True


def make_reader(kernel):
    reader = Reader.__new__(Reader)     # skip OpenProcess; only read paths are used
    reader.k = kernel
    reader.handle = 1
    reader.running = lambda: True
    return reader


class SimulatedMemoryCase(unittest.TestCase):
    def setUp(self):
        # ctypes.get_last_error only exists on Windows.
        patcher = mock.patch.object(memory.C, "get_last_error", lambda: 299, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)


class ReaderTests(SimulatedMemoryCase):
    def test_good_chain(self):
        sample = make_reader(FakeKernel(240, 320)).sample(ROOT, OFFSETS)
        self.assertEqual((sample["status"], sample["hp"], sample["max_hp"]),
                         ("candidate", 240, 320))

    def test_null_pointer_is_unavailable(self):
        kernel = FakeKernel()
        kernel.put(W + 0x88, struct.pack("<Q", 0))
        sample = make_reader(kernel).sample(ROOT, OFFSETS)
        self.assertEqual(sample["status"], "unavailable")
        self.assertIsNone(sample["hp"])
        self.assertIsNone(sample["max_hp"])

    def test_failed_read_is_unavailable(self):
        kernel = FakeKernel()
        kernel.fail.add(D + 0x130)
        sample = make_reader(kernel).sample(ROOT, OFFSETS)
        self.assertEqual(sample["status"], "unavailable")
        self.assertIsNone(sample["hp"])

    def test_partial_read_is_unavailable(self):
        kernel = FakeKernel()
        kernel.partial.add(D + 0x130)
        sample = make_reader(kernel).sample(ROOT, OFFSETS)
        self.assertEqual(sample["reason"], "Partial read")
        self.assertIsNone(sample["hp"])

    def test_chain_changing_during_sample_is_unavailable(self):
        kernel = FakeKernel()
        moved = 0x7FF400040000
        kernel.put(moved + 0x130, struct.pack("<ii", 1, 320))

        def move_after_hp_read(call):
            if call == 6:       # 4 pointer reads and the HP read are done; re-resolve begins
                kernel.put(S + 0x18, struct.pack("<Q", moved))
        kernel.on_call = move_after_hp_read
        sample = make_reader(kernel).sample(ROOT, OFFSETS)
        self.assertEqual(sample["reason"], "Pointer chain changed during sample")
        self.assertIsNone(sample["hp"])

    def test_invalid_hp_pairs_are_unavailable(self):
        for hp, maximum in ((321, 320), (-1, 320), (0, 0), (10, -5)):
            with self.subTest(hp=hp, maximum=maximum):
                sample = make_reader(FakeKernel(hp, maximum)).sample(ROOT, OFFSETS)
                self.assertEqual(sample["status"], "unavailable")
                self.assertIsNone(sample["hp"])

    def test_read_chain_reads_one_struct(self):
        self.assertEqual(make_reader(FakeKernel(96, 320)).read_chain(ROOT, OFFSETS, "<ii"),
                         (96, 320))


class CharacterReadTests(SimulatedMemoryCase):
    def test_reads_every_field_from_one_character(self):
        kernel = FakeKernel(240, 320)
        kernel.add_character(CHR, S, hp=240, max_hp=320, posture=(91, 120),
                             position=(160.5, -29.0, 22.5), anim=(50300100, 0.25), slot=7,
                             npc_id=71001000, handle=0x10008105, entity_id=1110800)
        state = make_reader(kernel).read_character(CHR)
        self.assertEqual((state.handle, state.npc_id, state.entity_id),
                         (0x10008105, 71001000, 1110800))
        self.assertEqual((state.hp, state.max_hp, state.posture, state.max_posture),
                         (240, 320, 91, 120))
        self.assertEqual(state.position, (160.5, -29.0, 22.5))
        self.assertEqual((state.anim_id, state.anim_elapsed), (50300100, 0.25))

    def test_animation_comes_from_the_current_slot_not_slot_zero(self):
        kernel = FakeKernel()
        kernel.set_animation(S, (1500, 0.75), slot=4)
        self.assertEqual(make_reader(kernel).read_character(CHR).anim_id, 1500)
        kernel.set_animation(S, (1500, 0.75), slot=0)
        self.assertEqual(make_reader(kernel).read_character(CHR).anim_id, 1500)

    def test_frame_token_changes_only_when_the_ring_changes(self):
        kernel = FakeKernel()
        reader = make_reader(kernel)
        first = reader.read_character(CHR).frame_token
        self.assertEqual(reader.read_character(CHR).frame_token, first)
        kernel.set_animation(S, (400, 0.5167), slot=3)
        self.assertNotEqual(reader.read_character(CHR).frame_token, first)

    def test_slot_out_of_range_gives_no_animation(self):
        for slot in (-1, 10, 1 << 20):
            with self.subTest(slot=slot):
                kernel = FakeKernel()
                kernel.set_animation(S, (400, 0.5), slot=slot)
                state = make_reader(kernel).read_character(CHR)
                self.assertIsNone(state.anim_id)
                self.assertIsNone(state.anim_elapsed)
                self.assertEqual(state.hp, 320)

    def test_implausible_posture_is_none_and_hp_is_kept(self):
        for posture in ((121, 120), (-1, 120), (0, 0)):
            with self.subTest(posture=posture):
                kernel = FakeKernel()
                kernel.add_character(CHR, S, posture=posture)
                state = make_reader(kernel).read_character(CHR)
                self.assertIsNone(state.posture)
                self.assertIsNone(state.max_posture)
                self.assertEqual((state.hp, state.max_hp), (320, 320))

    def test_zero_posture_is_a_real_value(self):
        kernel = FakeKernel()
        kernel.add_character(CHR, S, posture=(0, 120))
        self.assertEqual(make_reader(kernel).read_character(CHR).posture, 0)

    def test_null_module_pointers_give_none_for_that_module_only(self):
        kernel = FakeKernel()
        kernel.put(S + memory.MODULE_PHYSICS, struct.pack("<Q", 0))
        kernel.put(S + memory.MODULE_TIME_ACT, struct.pack("<Q", 0))
        state = make_reader(kernel).read_character(CHR)
        self.assertIsNone(state.position)
        self.assertIsNone(state.anim_id)
        self.assertIsNone(state.frame_token)
        self.assertEqual(state.hp, 320)

    def test_non_finite_position_is_none(self):
        kernel = FakeKernel()
        kernel.add_character(CHR, S, position=(1.0, float("nan"), 3.0))
        self.assertIsNone(make_reader(kernel).read_character(CHR).position)

    def test_null_data_module_is_unavailable(self):
        kernel = FakeKernel()
        kernel.put(S + memory.MODULE_DATA, struct.pack("<Q", 0))
        with self.assertRaises(memory.Unavailable):
            make_reader(kernel).read_character(CHR)

    def test_failed_and_partial_module_reads_are_unavailable(self):
        for address in (D + memory.DATA_HP, S + 0x1000 + memory.PHYSICS_POSITION,
                        S + 0x2000 + memory.RING_START):
            for kind in ("fail", "partial"):
                with self.subTest(address=hex(address), kind=kind):
                    kernel = FakeKernel()
                    getattr(kernel, kind).add(address)
                    with self.assertRaises(memory.Unavailable):
                        make_reader(kernel).read_character(CHR)

    def test_invalid_hp_pair_is_unavailable(self):
        for hp, maximum in ((321, 320), (-1, 320), (0, 0)):
            with self.subTest(hp=hp, maximum=maximum):
                with self.assertRaises(memory.Unavailable):
                    make_reader(FakeKernel(hp, maximum)).read_character(CHR)

    def test_module_table_changing_during_the_read_is_unavailable(self):
        kernel = FakeKernel()
        moved = 0x7FF400800000
        kernel.add_character(0x7FF400900000, moved, hp=1)

        def move_before_the_second_resolve(call):
            if call == 8:   # table (2), head, entity, data, physics and ring reads are done
                kernel.put(CHR + memory.CHR_MODULES, struct.pack("<Q", moved))
        kernel.on_call = move_before_the_second_resolve
        with self.assertRaisesRegex(memory.Unavailable, "changed during sample"):
            make_reader(kernel).read_character(CHR)


ALL_LIVE = {name: "live" for name in memory.STATUS}
ONLY_HP = {name: "live" if name == "player_hp" else "candidate" for name in memory.STATUS}
GAME_DATA = 0x7FF400700000


def with_game_data(kernel, deaths=393, igt_ms=171022673):
    kernel.put(BASE + memory.GAME_DATA_RVA, struct.pack("<Q", GAME_DATA))
    kernel.put(GAME_DATA, bytes(0x100))
    kernel.put(GAME_DATA + memory.GAME_DATA_DEATHS, struct.pack("<i", deaths))
    kernel.put(GAME_DATA + memory.GAME_DATA_IGT, struct.pack("<I", igt_ms))
    return kernel


def source(kernel, status=ALL_LIVE, **kwargs):
    return MemoryStateSource(make_reader(kernel), BASE, status=status, **kwargs)


class StateSourceTests(SimulatedMemoryCase):
    def test_candidate_fields_never_reach_a_snapshot(self):
        snapshot = source(with_game_data(FakeKernel(176, 320)), status=ONLY_HP).read()
        self.assertTrue(snapshot.valid)
        self.assertEqual((snapshot.player_hp, snapshot.player_max_hp), (176, 320))
        for name in ("player_posture", "player_max_posture", "player_pos", "player_anim_id",
                     "player_anim_elapsed", "frame_token", "boss_hp", "igt_ms", "death_count"):
            self.assertIsNone(getattr(snapshot, name), name)

    def test_shipped_status_table_names_every_group(self):
        self.assertEqual(memory.STATUS["player_hp"], "live")
        self.assertTrue(set(memory.STATUS.values()) <= {"live", "candidate"})
        source(with_game_data(FakeKernel()), status=None).read()    # every lookup resolves

    def test_live_fields_are_filled(self):
        kernel = with_game_data(FakeKernel(176, 320), deaths=394, igt_ms=1234)
        kernel.add_character(CHR, S, hp=176, max_hp=320, posture=(68, 120),
                             position=(1.5, 2.5, 3.5), anim=(213300, 0.125))
        snapshot = source(kernel).read()
        self.assertEqual((snapshot.player_posture, snapshot.player_max_posture), (68, 120))
        self.assertEqual(snapshot.player_pos, (1.5, 2.5, 3.5))
        self.assertEqual((snapshot.player_anim_id, snapshot.player_anim_elapsed), (213300, 0.125))
        self.assertIsNotNone(snapshot.frame_token)
        self.assertEqual((snapshot.igt_ms, snapshot.death_count), (1234, 394))

    def test_missing_game_data_leaves_the_player_valid(self):
        snapshot = source(FakeKernel()).read()      # GameData root unmapped
        self.assertTrue(snapshot.valid)
        self.assertIsNone(snapshot.igt_ms)
        self.assertIsNone(snapshot.death_count)

    def test_zero_hp_is_a_real_value_not_unavailable(self):
        snapshot = source(FakeKernel(0, 320)).read()
        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.player_hp, 0)

    def test_unavailable_is_none_not_zero(self):
        for address in (ROOT, W + 0x88):    # loading: root or player slot is null
            with self.subTest(address=hex(address)):
                kernel = FakeKernel()
                kernel.put(address, struct.pack("<Q", 0))
                snapshot = source(kernel).read()
                self.assertFalse(snapshot.valid)
                self.assertIsNone(snapshot.player_hp)
                self.assertIsNone(snapshot.player_pos)
                self.assertIn("Invalid/null pointer", snapshot.reason)

    def test_implausible_pair_is_unavailable(self):
        snapshot = source(FakeKernel(500, 320)).read()
        self.assertFalse(snapshot.valid)
        self.assertIsNone(snapshot.player_hp)

    def test_exited_process_is_unavailable(self):
        reader = make_reader(FakeKernel())
        reader.running = lambda: False
        snapshot = MemoryStateSource(reader, BASE).read()
        self.assertEqual(snapshot.reason, "Process exited")

    def test_player_slot_changing_during_the_sample_is_unavailable(self):
        kernel = FakeKernel()
        kernel.add_character(0x7FF400900000, 0x7FF400800000, hp=1)

        def swap_player_after_the_read(call):
            if call == 12:  # root, slot and the 9 character reads are done
                kernel.put(W + 0x88, struct.pack("<Q", 0x7FF400900000))
        kernel.on_call = swap_player_after_the_read
        snapshot = source(kernel).read()
        self.assertEqual(snapshot.reason, "Pointer chain changed during sample")
        self.assertIsNone(snapshot.player_hp)

    def test_candidates_are_reported_separately(self):
        probe = source(with_game_data(FakeKernel()), status=ONLY_HP).probe_candidates()
        self.assertNotIn("player_hp", probe)
        self.assertEqual(probe["player_posture"], 120)
        self.assertEqual(probe["death_count"], 393)
        kernel = FakeKernel()
        kernel.put(ROOT, struct.pack("<Q", 0))
        self.assertIn("unavailable", source(kernel, status=ONLY_HP).probe_candidates())


BOSS, BOSS_TABLE = 0x7FF401000000, 0x7FF401100000
COPY, COPY_TABLE = 0x7FF402000000, 0x7FF402100000
IDOL, IDOL_TABLE = 0x7FF403000000, 0x7FF403100000
BOSS_FIELDS = ("boss_npc_id", "boss_hp", "boss_max_hp", "boss_posture", "boss_max_posture",
               "boss_pos", "boss_anim_id", "boss_anim_elapsed")


def arena(kernel=None):
    """The tower as recorded: the boss, a dormant copy with the same NPC ID, an idol."""
    kernel = kernel or FakeKernel()
    kernel.add_character(BOSS, BOSS_TABLE, hp=9207, max_hp=9887, posture=(3610, 4083),
                         position=(-107.8, 54.0, 235.4), anim=(3015, 1.25),
                         npc_id=71001000, entity_id=1110800, handle=0x10008105)
    kernel.add_character(COPY, COPY_TABLE, hp=7670, max_hp=7670, posture=(3168, 3168),
                         anim=(-1, 0.0), slot=0, npc_id=71001000, entity_id=1110890)
    kernel.add_character(IDOL, IDOL_TABLE, hp=9999, max_hp=9999, npc_id=10010000,
                         entity_id=1110950)
    kernel.set_entry(1, 4, IDOL)
    kernel.set_entry(2, 261, BOSS)
    kernel.set_entry(2, 262, COPY)
    return kernel


LOCK_MANAGER = 0x7FF404000000


def with_lock(kernel, target):
    """Map the lock-on manager with `target` as the locked character's handle."""
    kernel.put(BASE + memory.LOCK_TGT_MAN_RVA, struct.pack("<Q", LOCK_MANAGER))
    kernel.put(LOCK_MANAGER, bytes(0x100))
    kernel.put(LOCK_MANAGER + memory.LOCK_TARGET_HANDLE, struct.pack("<I", target))
    return kernel


class LockOnTests(SimulatedMemoryCase):
    """Values as read from the live game on 2026-11-27."""

    def locked_on(self, kernel, **kwargs):
        return source(kernel, boss=memory.GENICHIRO, **kwargs).read().locked_on

    def test_the_boss_handle_in_the_manager_is_locked_on(self):
        self.assertIs(self.locked_on(with_lock(arena(), 0x10008105)), True)

    def test_no_target_is_not_locked_on(self):
        self.assertIs(self.locked_on(with_lock(arena(), memory.NO_LOCK_TARGET)), False)

    def test_a_lock_on_another_character_is_not_locked_on(self):
        self.assertIs(self.locked_on(with_lock(arena(), 0x10008106)), False)

    def test_an_unreadable_manager_is_none_and_the_rest_of_the_snapshot_stands(self):
        snapshot = source(arena(), boss=memory.GENICHIRO).read()
        self.assertIsNone(snapshot.locked_on)
        self.assertEqual(snapshot.boss_hp, 9207)
        kernel = with_lock(arena(), 0x10008105)
        kernel.put(BASE + memory.LOCK_TGT_MAN_RVA, struct.pack("<Q", 0))
        self.assertIsNone(self.locked_on(kernel))

    def test_without_the_boss_there_is_no_answer(self):
        kernel = with_lock(FakeKernel(), 0x10008105)
        self.assertIsNone(self.locked_on(kernel))

    def test_a_candidate_lock_route_never_reaches_a_snapshot(self):
        status = dict(memory.STATUS, lock_on="candidate")
        self.assertIsNone(self.locked_on(with_lock(arena(), 0x10008105), status=status))


ITEMS, ITEM_ENTRIES = 0x7FF400800000, 0x7FF400810000
# Entries as read from the live game on 2026-11-27: handle, item ID, quantity, other.
LIVE_ITEMS = ((0x9080002C, 0x10018E70, 1, 0xFFFFF111), (0xB0000BB8, 0x40000BB8, 3, 0x3E8112),
              (0xB0000DB6, 0x40000DB6, 10, 0x5B4113))


def with_items(kernel, entries=LIVE_ITEMS, count=None):
    """Map the item list behind the GameData root."""
    with_game_data(kernel)
    kernel.put(GAME_DATA + memory.GAME_DATA_ITEMS, struct.pack("<Q", ITEMS))
    kernel.put(ITEMS, bytes(0x1100))
    kernel.put(ITEMS + memory.ITEMS_ENTRIES, struct.pack("<Q", ITEM_ENTRIES))
    kernel.put(ITEMS + memory.ITEMS_COUNT,
               struct.pack("<i", len(entries) if count is None else count))
    kernel.put(ITEM_ENTRIES, b"".join(struct.pack("<IIiI", *entry) for entry in entries))
    return kernel


class GourdTests(SimulatedMemoryCase):
    def test_the_quantity_of_the_gourd_entry_is_the_charge_count(self):
        self.assertEqual(source(with_items(FakeKernel())).read().gourds, 3)

    def test_the_entry_is_found_again_after_it_moves(self):
        kernel = with_items(FakeKernel())
        reader = source(kernel)
        self.assertEqual(reader.read().gourds, 3)
        with_items(kernel, entries=(LIVE_ITEMS[1][:2] + (7, 0),) + LIVE_ITEMS[:1] + LIVE_ITEMS[2:])
        self.assertEqual(reader.read().gourds, 7)
        self.assertEqual(reader.read().gourds, 7)

    def test_the_remembered_entry_is_read_alone(self):
        kernel = with_items(FakeKernel())
        reader = source(kernel, status=dict(ONLY_HP, gourds="live"))
        reader.read()
        before = kernel.calls
        reader.read()
        first_calls = kernel.calls - before
        kernel.put(ITEMS + memory.ITEMS_COUNT, struct.pack("<i", -1))    # not looked at again
        self.assertEqual(reader.read().gourds, 3)
        self.assertEqual(kernel.calls - before, 2 * first_calls)

    def test_no_gourd_entry_is_none(self):
        kernel = with_items(FakeKernel(), entries=LIVE_ITEMS[:1] + LIVE_ITEMS[2:])
        snapshot = source(kernel).read()
        self.assertIsNone(snapshot.gourds)
        self.assertTrue(snapshot.valid)

    def test_an_entry_beyond_the_count_is_not_used(self):
        self.assertIsNone(source(with_items(FakeKernel(), count=1)).read().gourds)

    def test_an_implausible_count_or_quantity_is_none(self):
        for count in (0, -5, memory.MAX_ITEM_ENTRIES + 1):
            self.assertIsNone(source(with_items(FakeKernel(), count=count)).read().gourds)
        for quantity in (-1, 11, 9999):
            entries = ((0xB0000BB8, memory.HEALING_GOURD_ITEM, quantity, 0),)
            self.assertIsNone(source(with_items(FakeKernel(), entries=entries)).read().gourds)

    def test_an_unreadable_list_is_none_and_the_rest_of_the_snapshot_stands(self):
        kernel = with_items(FakeKernel())
        kernel.put(ITEMS + memory.ITEMS_ENTRIES, struct.pack("<Q", 0))
        snapshot = source(kernel).read()
        self.assertIsNone(snapshot.gourds)
        self.assertEqual(snapshot.player_hp, 320)
        self.assertIsNone(source(with_game_data(FakeKernel())).read().gourds)

    def test_a_candidate_route_never_reaches_a_snapshot(self):
        status = dict(memory.STATUS, gourds="candidate")
        self.assertIsNone(source(with_items(FakeKernel()), status=status).read().gourds)


class BossTests(SimulatedMemoryCase):
    def assert_no_boss(self, snapshot, reason):
        self.assertTrue(snapshot.valid, "the player must stay valid without the boss")
        for name in BOSS_FIELDS:
            self.assertIsNone(getattr(snapshot, name), name)
        self.assertIn(reason, snapshot.boss_reason)

    def test_boss_is_picked_by_entity_id_among_copies_with_the_same_npc_id(self):
        snapshot = source(arena(), boss=memory.GENICHIRO).read()
        self.assertIsNone(snapshot.boss_reason)
        self.assertEqual((snapshot.boss_npc_id, snapshot.boss_hp, snapshot.boss_max_hp),
                         (71001000, 9207, 9887))
        self.assertEqual((snapshot.boss_posture, snapshot.boss_max_posture), (3610, 4083))
        self.assertEqual((snapshot.boss_anim_id, snapshot.boss_anim_elapsed), (3015, 1.25))
        self.assertAlmostEqual(snapshot.boss_pos[2], 235.4, places=3)
        self.assertEqual(snapshot.player_hp, 320)

    def test_no_boss_configured_reads_no_boss_and_does_no_sweep(self):
        kernel = arena()
        plain = FakeKernel()
        snapshot = source(kernel).read()
        source(plain).read()
        self.assertIsNone(snapshot.boss_hp)
        self.assertIsNone(snapshot.boss_reason)
        self.assertEqual(kernel.calls, plain.calls)

    def test_candidate_boss_route_never_reaches_a_snapshot(self):
        snapshot = source(arena(), status=ONLY_HP, boss=memory.GENICHIRO).read()
        self.assertIsNone(snapshot.boss_hp)
        self.assertIsNone(snapshot.boss_npc_id)

    def test_boss_missing_leaves_player_valid_and_boss_none(self):
        kernel = arena()
        kernel.set_entry(2, 261, 0)
        self.assert_no_boss(source(kernel, boss=memory.GENICHIRO).read(),
                            "Boss not in the character collection")

    def test_same_npc_id_with_another_entity_id_is_not_the_boss(self):
        kernel = arena()
        kernel.set_entry(2, 261, COPY)      # only dormant copies are loaded
        self.assert_no_boss(source(kernel, boss=memory.GENICHIRO).read(),
                            "Boss not in the character collection")

    def test_two_characters_with_the_boss_identity_are_refused(self):
        kernel = arena()
        kernel.set_entry(3, 7, BOSS)
        self.assert_no_boss(source(kernel, boss=memory.GENICHIRO).read(),
                            "2 characters match the boss identity")

    def test_boss_object_at_a_new_address_after_a_reload_is_followed(self):
        kernel = arena()
        state = source(kernel, boss=memory.GENICHIRO)
        self.assertEqual(state.read().boss_hp, 9207)
        moved, moved_table = 0x7FF404000000, 0x7FF404100000
        kernel.add_character(moved, moved_table, hp=9887, max_hp=9887, npc_id=71001000,
                             entity_id=1110800)
        kernel.put(BOSS + memory.CHR_ENTITY_ID, struct.pack("<i", 0))   # stale object
        kernel.set_entry(2, 261, moved)
        self.assertEqual(state.read().boss_hp, 9887)

    def test_boss_in_another_entry_is_found_again(self):
        kernel = arena()
        state = source(kernel, boss=memory.GENICHIRO)
        self.assertEqual(state.read().boss_hp, 9207)
        kernel.set_entry(2, 261, 0)
        kernel.set_entry(5, 40, BOSS)
        self.assertEqual(state.read().boss_hp, 9207)

    def test_remembered_entry_holding_another_character_is_never_read_as_the_boss(self):
        kernel = arena()
        state = source(kernel, boss=memory.GENICHIRO)
        self.assertEqual(state.read().boss_hp, 9207)
        kernel.set_entry(2, 261, IDOL)      # plausible HP pair, wrong identity
        self.assert_no_boss(state.read(), "Boss not in the character collection")
        kernel.set_entry(2, 261, BOSS)
        self.assertEqual(state.read().boss_hp, 9207)

    def test_identity_changing_between_lookup_and_read_is_unavailable(self):
        kernel = arena()
        state = source(kernel, boss=memory.GENICHIRO)
        state.read()
        base = kernel.calls
        reads_of_entity = []

        def swap_identity(call):
            # The lookup reads the entity ID once; change it before the character read does.
            if call > base and kernel.last == BOSS + memory.CHR_ENTITY_ID:
                reads_of_entity.append(call)
                kernel.put(BOSS + memory.CHR_ENTITY_ID, struct.pack("<i", 1110890))
        kernel.after_read = swap_identity
        self.assert_no_boss(state.read(), "Boss identity check failed")
        self.assertEqual(len(reads_of_entity), 2)   # the lookup's read, then the character's

    def test_boss_entry_changing_during_the_sample_is_unavailable(self):
        kernel = arena()
        state = source(kernel, boss=memory.GENICHIRO)
        state.read()

        def empty_entry_after_ring_read(call):
            if kernel.last == BOSS_TABLE + 0x2000 + memory.RING_START:
                kernel.set_entry(2, 261, 0)
        kernel.after_read = empty_entry_after_ring_read
        self.assert_no_boss(state.read(), "Boss entry changed during sample")

    def test_implausible_boss_hp_is_none_not_zero(self):
        kernel = arena()
        kernel.put(BOSS_TABLE + 0x10000 + memory.DATA_HP, struct.pack("<ii", 0, 0))
        self.assert_no_boss(source(kernel, boss=memory.GENICHIRO).read(), "Implausible HP pair")

    def test_dead_boss_reads_zero_hp_as_a_real_value(self):
        kernel = arena()
        kernel.put(BOSS_TABLE + 0x10000 + memory.DATA_HP, struct.pack("<ii", 0, 9887))
        snapshot = source(kernel, boss=memory.GENICHIRO).read()
        self.assertEqual((snapshot.boss_hp, snapshot.boss_max_hp), (0, 9887))

    def test_failed_boss_read_leaves_player_valid(self):
        kernel = arena()
        kernel.fail.add(BOSS_TABLE + 0x10000 + memory.DATA_HP)
        self.assert_no_boss(source(kernel, boss=memory.GENICHIRO).read(), "Read failed")

    def test_unreadable_entry_array_is_skipped(self):
        kernel = arena()
        kernel.fail.add(ENTRIES + 0x100000 * 1)     # block 1's array cannot be read
        self.assertEqual(source(kernel, boss=memory.GENICHIRO).read().boss_hp, 9207)

    def test_characters_lists_every_loaded_entry(self):
        state = source(arena())
        self.assertEqual(list(state.characters(W)),
                         [(1, 4, IDOL), (2, 261, BOSS), (2, 262, COPY)])


if __name__ == "__main__":
    unittest.main()
