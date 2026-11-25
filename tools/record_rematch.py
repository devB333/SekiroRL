"""Read-only rematch recorder used for Stage 3 discovery.

Run from the repo root with Windows Python, writing the log outside the repo:
    python.exe - <pid> <seconds> < tools/record_rematch.py > log.jsonl

Also logs the characters in WATCH on every sample and dumps their objects.

Player at 10 Hz. Once a second, every character in the 20 WorldBlockChr
blocks within RADIUS of the player. Uses sekirorl.memory.Reader, which opens
the process with query/read/synchronize rights only.
"""
import hashlib
import json
import struct
import sys
import time
import zlib

from sekirorl.memory import GAME_DATA_RVA, WORLD_CHR_MAN_RVA, Reader, Unavailable

RADIUS = 100.0
PLAYER_HZ = 10.0
CHAR_EVERY = 10  # player samples per character sweep
WATCH = {71001000, 71100000, 54300000}   # logged every tick, with object dumps
DUMP_EVERY = 50  # ticks between raw dumps of watched objects
DUMP_SIZE = 0x2000
watched = []     # (block, index) from the last sweep

pid = int(sys.argv[1])
seconds = float(sys.argv[2])
reader = Reader(pid)
base = reader.module_base()
seen = set()


def u64(buf, off):
    return struct.unpack_from("<Q", buf, off)[0]


def ok_ptr(value):
    return 0x10000 <= value < 0x0000800000000000


def ring(module):
    """TimeAct module: ring +0x1C..+0xF4, current full-body slot at +0xEC."""
    buf = reader.read(module + 0x1C, 0xD8)
    slot = struct.unpack_from("<i", buf, 0xD0)[0]
    out = {"slot": slot, "next": struct.unpack_from("<i", buf, 0xCC)[0],
           "crc": zlib.crc32(buf)}
    if 0 <= slot < 10:
        _, anim, elapsed, _, length = struct.unpack_from("<iifff", buf, 0x14 * slot)
        out.update(anim=anim, el=round(elapsed, 4), len=round(length, 4))
    return out


def character(chr_ptr, full):
    """Fields of one character object. Each group fails independently."""
    out = {"ptr": hex(chr_ptr)}
    try:
        head = reader.read(chr_ptr, 0x80)
        out["handle"] = hex(struct.unpack_from("<I", head, 0x08)[0])
        out["id"] = struct.unpack_from("<i", head, 0x68)[0]
        if full and chr_ptr not in seen:
            seen.add(chr_ptr)
            out["head"] = head.hex()
        table = reader.pointer(chr_ptr + 0x1FF8)
    except Unavailable as error:
        out["err"] = str(error)
        return out
    try:
        data = reader.pointer(table + 0x18)
        out["hp"] = struct.unpack("<iii", reader.read(data + 0x130, 12))
        out["post"] = struct.unpack("<ii", reader.read(data + 0x148, 8))
    except Unavailable as error:
        out["err_data"] = str(error)
    try:
        physics = reader.pointer(table + 0x68)
        out["pos"] = [round(v, 3) for v in struct.unpack("<fff", reader.read(physics + 0x80, 12))]
    except Unavailable as error:
        out["err_pos"] = str(error)
    try:
        out["anim"] = ring(reader.pointer(table + 0x10))
    except Unavailable as error:
        out["err_anim"] = str(error)
    return out


def sweep(wcm, player_ptr, player_pos):
    chars = []
    blocks = reader.read(wcm + 0x910, 0x140 * 20)
    for b in range(20):
        capacity = struct.unpack_from("<i", blocks, 0x140 * b + 0x80)[0]
        array = u64(blocks, 0x140 * b + 0x88)
        if not 0 < capacity <= 4096 or not ok_ptr(array):
            continue
        try:
            entries = reader.read(array, 0x38 * capacity)
        except Unavailable:
            continue
        for i in range(capacity):
            chr_ptr = u64(entries, 0x38 * i)
            if not ok_ptr(chr_ptr):
                continue
            c = character(chr_ptr, True)
            c["b"], c["i"] = b, i
            if chr_ptr == player_ptr:
                c["player"] = True
            pos = c.get("pos")
            if pos and player_pos:
                d = sum((a - q) ** 2 for a, q in zip(pos, player_pos)) ** 0.5
                c["d"] = round(d, 2)
                if d > RADIUS:
                    continue
            chars.append(c)
    watched[:] = [(c["b"], c["i"]) for c in chars if c.get("id") in WATCH]
    return chars


def watch(wcm, tick):
    out = []
    for b, i in watched:
        try:
            header = reader.read(wcm + 0x910 + 0x140 * b + 0x80, 16)
            capacity, array = struct.unpack("<i4xQ", header)
            if not i < capacity or not ok_ptr(array):
                continue
            chr_ptr = u64(reader.read(array + 0x38 * i, 8), 0)
            if not ok_ptr(chr_ptr):
                continue
            c = character(chr_ptr, False)
            c["b"], c["i"] = b, i
            if tick % DUMP_EVERY == 0:
                c["dump"] = reader.read(chr_ptr, DUMP_SIZE).hex()
            out.append(c)
        except Unavailable as error:
            out.append({"b": b, "i": i, "err": str(error)})
    return out


with open(reader.image_path(), "rb") as f:
    sha = hashlib.sha256(f.read()).hexdigest()
print(json.dumps({"meta": True, "pid": pid, "base": hex(base), "sha256": sha,
                  "time": time.time()}), flush=True)

start = time.perf_counter()
n = 0
try:
    while reader.running() and time.perf_counter() - start < seconds:
        deadline = start + n / PLAYER_HZ
        delay = deadline - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        row = {"t": round(time.time(), 3), "n": n}
        wcm = player_ptr = None
        try:
            game_data = reader.pointer(base + GAME_DATA_RVA)
            row["deaths"], = struct.unpack("<i", reader.read(game_data + 0x90, 4))
            row["igt"], = struct.unpack("<I", reader.read(game_data + 0x9C, 4))
        except Unavailable as error:
            row["err_gd"] = str(error)
        try:
            wcm = reader.pointer(base + WORLD_CHR_MAN_RVA)
            player_ptr = reader.pointer(wcm + 0x88)
            row["p"] = character(player_ptr, False)
        except Unavailable as error:
            row["err_p"] = str(error)
        if n % CHAR_EVERY == 0 and wcm is not None:
            t0 = time.perf_counter()
            try:
                row["chars"] = sweep(wcm, player_ptr, (row.get("p") or {}).get("pos"))
            except Unavailable as error:
                row["err_sweep"] = str(error)
            row["sweep_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        if wcm is not None and watched:
            try:
                row["w"] = watch(wcm, n)
            except Unavailable as error:
                row["err_w"] = str(error)
        print(json.dumps(row), flush=True)
        n += 1
except KeyboardInterrupt:
    pass
finally:
    reader.close()
