"""Read-only Sekiro 1.06 candidate HP reader. See MEMORY_READER.md.

Standard library only. Opens the process with query/read/synchronize rights
and never writes, injects or freezes. `Reader` is the Windows API wrapper;
`MemoryStateSource` turns it into `Snapshot` objects for the environment.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import json
import os
import struct
import time

from .snapshot import Snapshot

# Module-relative roots (address of a pointer slot), Sekiro 1.06.
WORLD_CHR_MAN_RVA = 0x3D7A1E0
GAME_DATA_RVA = 0x3D5AAC0
PLAYER_HP_OFFSETS = [0x88, 0x1FF8, 0x18, 0x130]

# Chains in traversal order: every offset is added after a dereference and the
# final offset is not dereferenced. "live" means seen working against the
# running game; "candidate" means documented by a community source only and
# never fed into observations. See Plan.md section 4.
CHAINS = {
    # name: (root RVA, offsets, struct format, status)
    "player_hp_pair": (WORLD_CHR_MAN_RVA, PLAYER_HP_OFFSETS, "<ii", "live"),
    "player_posture_pair": (WORLD_CHR_MAN_RVA, [0x88, 0x1FF8, 0x18, 0x148], "<ii", "candidate"),
    "player_position_xyz": (WORLD_CHR_MAN_RVA, [0x88, 0x1FF8, 0x68, 0x80], "<fff", "candidate"),
    "player_anim_id_elapsed": (WORLD_CHR_MAN_RVA, [0x88, 0x1FF8, 0x10, 0x20], "<if", "candidate"),
    "igt_ms": (GAME_DATA_RVA, [0x9C], "<I", "candidate"),
    "death_count": (GAME_DATA_RVA, [0x90], "<i", "candidate"),
}


class Unavailable(Exception):
    pass


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
        if not 0x10000 <= address < 0x0000800000000000:
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
        if not 0x10000 <= value < 0x0000800000000000:
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


def check_hp_pair(hp, maximum):
    if maximum <= 0 or not 0 <= hp <= maximum:
        raise Unavailable(f"Implausible HP pair: {hp}/{maximum}")
    return hp, maximum


class MemoryStateSource:
    """StateSource backed by a live process. Fills verified fields only.

    `reader` needs `read_chain(root, offsets, fmt)` and `running()`, so tests
    can pass a simulated memory object instead of a Windows process.
    """

    def __init__(self, reader, base):
        self.reader = reader
        self.base = base

    def read(self):
        t_ns = time.perf_counter_ns()
        try:
            if not self.reader.running():
                raise Unavailable("Process exited")
            root, offsets, fmt, _ = CHAINS["player_hp_pair"]
            hp, maximum = check_hp_pair(*self.reader.read_chain(self.base + root, offsets, fmt))
        except Unavailable as error:
            return Snapshot(t_ns=t_ns, reason=str(error))
        return Snapshot(t_ns=t_ns, player_hp=hp, player_max_hp=maximum)

    def probe_candidates(self):
        """Read unverified chains for manual checking. Never used as observations."""
        result = {}
        for name, (root, offsets, fmt, status) in CHAINS.items():
            if status != "candidate":
                continue
            try:
                result[name] = self.reader.read_chain(self.base + root, offsets, fmt)
            except Unavailable as error:
                result[name] = f"unavailable: {error}"
        return result


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
