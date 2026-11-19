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


class FakeKernel:
    """Stands in for kernel32: serves ReadProcessMemory from a dict of blobs."""

    def __init__(self, hp=320, maximum=320):
        self.mem = {
            ROOT: struct.pack("<Q", W),
            W + 0x88: struct.pack("<Q", CHR),
            CHR + 0x1FF8: struct.pack("<Q", S),
            S + 0x18: struct.pack("<Q", D),
            D + 0x130: struct.pack("<ii", hp, maximum),
        }
        self.fail = set()
        self.partial = set()
        self.calls = 0
        self.on_call = None

    def ReadProcessMemory(self, handle, address, buffer, size, got_ref):
        self.calls += 1
        if self.on_call:
            self.on_call(self.calls)
        if address in self.fail or address not in self.mem:
            return False
        data = self.mem[address][:size]
        if address in self.partial:
            data = data[:size - 1]
        ctypes.memmove(buffer, data, len(data))
        got_ref._obj.value = len(data)
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
        kernel.mem[W + 0x88] = struct.pack("<Q", 0)
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
        kernel.mem[moved + 0x130] = struct.pack("<ii", 1, 320)

        def move_after_hp_read(call):
            if call == 6:       # 4 pointer reads and the HP read are done; re-resolve begins
                kernel.mem[S + 0x18] = struct.pack("<Q", moved)
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


class StateSourceTests(SimulatedMemoryCase):
    def test_valid_snapshot_has_only_verified_fields(self):
        snapshot = MemoryStateSource(make_reader(FakeKernel(176, 320)), BASE).read()
        self.assertTrue(snapshot.valid)
        self.assertEqual((snapshot.player_hp, snapshot.player_max_hp), (176, 320))
        for name in ("player_posture", "player_pos", "player_anim_id", "boss_hp", "igt_ms"):
            self.assertIsNone(getattr(snapshot, name), name)

    def test_zero_hp_is_a_real_value_not_unavailable(self):
        snapshot = MemoryStateSource(make_reader(FakeKernel(0, 320)), BASE).read()
        self.assertTrue(snapshot.valid)
        self.assertEqual(snapshot.player_hp, 0)

    def test_unavailable_is_none_not_zero(self):
        kernel = FakeKernel()
        kernel.mem[ROOT] = struct.pack("<Q", 0)
        snapshot = MemoryStateSource(make_reader(kernel), BASE).read()
        self.assertFalse(snapshot.valid)
        self.assertIsNone(snapshot.player_hp)
        self.assertIn("Invalid/null pointer", snapshot.reason)

    def test_implausible_pair_is_unavailable(self):
        snapshot = MemoryStateSource(make_reader(FakeKernel(500, 320)), BASE).read()
        self.assertFalse(snapshot.valid)
        self.assertIsNone(snapshot.player_hp)

    def test_exited_process_is_unavailable(self):
        reader = make_reader(FakeKernel())
        reader.running = lambda: False
        snapshot = MemoryStateSource(reader, BASE).read()
        self.assertEqual(snapshot.reason, "Process exited")

    def test_candidates_are_reported_separately(self):
        probe = MemoryStateSource(make_reader(FakeKernel()), BASE).probe_candidates()
        self.assertNotIn("player_hp_pair", probe)
        self.assertTrue(all(str(value).startswith("unavailable") for value in probe.values()))


if __name__ == "__main__":
    unittest.main()
