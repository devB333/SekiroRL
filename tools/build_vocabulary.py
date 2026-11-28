"""Build the animation ID vocabulary for the observation from logged fights.

Run from the repo root:
    python tools/build_vocabulary.py logs/2026-11-25/*-live.jsonl.gz logs/*/run-*.jsonl

Reads snapshot logs written by tools/log_snapshots.py or sekirorl.runner
(plain or gzip JSONL) and writes sekirorl/data/animations-genichiro.json.
Only samples with the boss loaded and the player's HP readable are counted.
Changing the vocabulary changes the observation layout and its schema
version (snapshot.py), so models trained on the old one cannot be reused.
"""
import argparse
import collections
import glob
import gzip
import json
import os

OUTPUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "sekirorl", "data",
                                       "animations-genichiro.json"))


def snapshot_rows(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if row.get("kind", "snapshot") == "snapshot" and "boss_anim_id" in row:
                yield row


def count(rows):
    """Samples per animation ID, for the boss and for the player."""
    boss, player = collections.Counter(), collections.Counter()
    for row in rows:
        if row.get("boss_hp") is None or row.get("player_hp") is None:
            continue
        if row.get("boss_anim_id") is not None:
            boss[row["boss_anim_id"]] += 1
        if row.get("player_anim_id") is not None:
            player[row["player_anim_id"]] += 1
    return boss, player


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", help="log files; wildcards are expanded")
    parser.add_argument("--min-samples", type=int, default=1,
                        help="IDs seen fewer times than this go to the unknown slot")
    parser.add_argument("--output", default=OUTPUT)
    args = parser.parse_args()
    paths = sorted({path for pattern in args.logs for path in (glob.glob(pattern) or [pattern])})
    boss, player = collections.Counter(), collections.Counter()
    used = []
    for path in paths:
        b, p = count(snapshot_rows(path))
        if b or p:
            used.append(os.path.basename(path))
            boss.update(b)
            player.update(p)
        print(f"{path}: {sum(b.values())} samples with the boss loaded")
    if not boss:
        parser.exit(1, "No samples with the boss loaded; nothing written.\n")
    vocabulary = {
        "sources": used,
        "samples": sum(boss.values()),
        "boss": sorted(i for i, n in boss.items() if n >= args.min_samples),
        "player": sorted(i for i, n in player.items() if n >= args.min_samples),
    }
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(vocabulary, handle, indent=1)
        handle.write("\n")
    print(f"{len(vocabulary['boss'])} boss and {len(vocabulary['player'])} player "
          f"animation IDs from {vocabulary['samples']} samples -> {args.output}")


if __name__ == "__main__":
    main()
