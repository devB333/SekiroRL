"""Observation layout v2: animation IDs as one-hot blocks. Standard library only."""
import gzip
import json
import os
import re
import unittest

from sekirorl import snapshot
from sekirorl.snapshot import MASK_NAMES, OBS_NAMES, OBS_SIZE, VOCABULARY, Snapshot, encode

FIGHTS = os.path.join(os.path.dirname(__file__), "data", "fights-2026-11-25.json.gz")
ALIVE = dict(player_hp=960, player_max_hp=960)


def block(observation, name):
    """The one-hot block of one animation field as {slot label: value}."""
    prefix = name + "="
    return {label[len(prefix):]: value for label, value in zip(OBS_NAMES, observation)
            if label.startswith(prefix)}


def validity(observation, name):
    return observation[len(OBS_NAMES) + MASK_NAMES.index(name)]


class LayoutTests(unittest.TestCase):
    def test_sizes_agree(self):
        scalars = len(snapshot.OBS_FIELDS)
        self.assertEqual(len(OBS_NAMES),
                         scalars + len(VOCABULARY["boss"]) + 1 + len(VOCABULARY["player"]) + 1)
        self.assertEqual(len(MASK_NAMES), scalars + 2)
        self.assertEqual(OBS_SIZE, len(OBS_NAMES) + len(MASK_NAMES))
        self.assertEqual(len(set(OBS_NAMES)), len(OBS_NAMES))
        self.assertEqual(OBS_NAMES[:scalars], MASK_NAMES[:scalars])

    def test_gourd_charges_are_a_tenth_each_and_masked_when_unknown(self):
        index = OBS_NAMES.index("gourds")
        valid = len(OBS_NAMES) + MASK_NAMES.index("gourds")
        known = encode(Snapshot(t_ns=0, gourds=3))
        self.assertAlmostEqual(known[index], 0.3)
        self.assertEqual(known[valid], 1.0)
        empty = encode(Snapshot(t_ns=0, gourds=0))
        self.assertEqual((empty[index], empty[valid]), (0.0, 1.0))
        unknown = encode(Snapshot(t_ns=0))
        self.assertEqual((unknown[index], unknown[valid]), (0.0, 0.0))

    def test_vocabulary_is_sorted_and_unique(self):
        for ids in VOCABULARY.values():
            self.assertEqual(list(ids), sorted(set(ids)))
            self.assertTrue(ids)

    def test_schema_version_names_the_vocabulary(self):
        self.assertTrue(re.fullmatch(r"v3-gourds-[0-9a-f]{8}", snapshot.SCHEMA_VERSION))


class AnimationEncodingTests(unittest.TestCase):
    def test_a_known_id_sets_its_own_slot(self):
        observation = encode(Snapshot(t_ns=0, **ALIVE, boss_anim_id=3014, player_anim_id=400))
        boss, player = block(observation, "boss_anim"), block(observation, "player_anim")
        self.assertEqual([label for label, value in boss.items() if value], ["3014"])
        self.assertEqual([label for label, value in player.items() if value], ["400"])
        self.assertEqual(validity(observation, "boss_anim"), 1.0)
        self.assertEqual(validity(observation, "player_anim"), 1.0)

    def test_an_id_outside_the_vocabulary_sets_the_unknown_slot(self):
        observation = encode(Snapshot(t_ns=0, **ALIVE, boss_anim_id=123456789))
        boss = block(observation, "boss_anim")
        self.assertEqual([label for label, value in boss.items() if value], ["unknown"])
        self.assertEqual(validity(observation, "boss_anim"), 1.0)

    def test_an_unavailable_id_is_an_empty_block_marked_invalid(self):
        observation = encode(Snapshot(t_ns=0, **ALIVE))
        for name in ("boss_anim", "player_anim"):
            self.assertFalse(any(block(observation, name).values()))
            self.assertEqual(validity(observation, name), 0.0)

    def test_every_animation_in_the_recorded_fights_has_its_own_slot(self):
        with gzip.open(FIGHTS, "rt") as handle:
            data = json.load(handle)
        boss_at = data["fields"].index("boss_anim_id")
        player_at = data["fields"].index("player_anim_id")
        hp_at = data["fields"].index("boss_hp")
        seen = {"boss": set(), "player": set()}
        for rows in data["fights"].values():
            for row in rows:
                if row[hp_at] is None:
                    continue
                seen["boss"].add(row[boss_at])
                seen["player"].add(row[player_at])
        for name, ids in seen.items():
            ids.discard(None)
            self.assertGreater(len(ids), 20)
            self.assertFalse(ids - set(VOCABULARY[name]), name)


if __name__ == "__main__":
    unittest.main()
