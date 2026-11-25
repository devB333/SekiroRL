"""Log MemoryStateSource snapshots (all fields, candidates included) at 10 Hz. Read-only.

Run from the repo root with Windows Python, writing the log outside the repo:
    python.exe - <pid> <seconds> < tools/log_snapshots.py > log.jsonl
"""
import dataclasses, json, sys, time
from sekirorl import memory

pid, seconds = int(sys.argv[1]), float(sys.argv[2])
reader = memory.Reader(pid)
base = reader.module_base()
source = memory.MemoryStateSource(reader, base, boss=memory.GENICHIRO)
start = time.perf_counter()
n = 0
try:
    while reader.running() and time.perf_counter() - start < seconds:
        delay = start + n / 10.0 - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        t0 = time.perf_counter()
        snapshot = source.read(include_candidates=True)
        row = dataclasses.asdict(snapshot)
        row.update(n=n, t=round(time.time(), 3), read_ms=round((time.perf_counter() - t0) * 1000, 3),
                   slot=source._boss_slot)
        print(json.dumps(row), flush=True)
        n += 1
finally:
    reader.close()
