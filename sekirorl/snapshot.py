"""Raw game state for one sample, and the fixed observation layout built from it.

Standard library only. `None` always means "unavailable"; it is never replaced
by zero health. Demonstrations should store `Snapshot`s, not encoded
observations, so the layout can change without re-recording.
"""
from dataclasses import dataclass
import json
import math
import os
from typing import Optional, Tuple
import zlib

VOCABULARY_PATH = os.path.join(os.path.dirname(__file__), "data", "animations-genichiro.json")

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class Snapshot:
    t_ns: int
    reason: Optional[str] = None            # why the sample is unavailable, if it is
    player_hp: Optional[int] = None
    player_max_hp: Optional[int] = None
    player_posture: Optional[int] = None    # raw value: counts DOWN, max = empty bar
    player_max_posture: Optional[int] = None
    player_pos: Optional[Vec3] = None
    player_anim_id: Optional[int] = None
    player_anim_elapsed: Optional[float] = None
    boss_reason: Optional[str] = None       # why the boss is unavailable, if it is
    boss_npc_id: Optional[int] = None       # identity of the object the boss fields came from
    boss_hp: Optional[int] = None
    boss_max_hp: Optional[int] = None
    boss_posture: Optional[int] = None
    boss_max_posture: Optional[int] = None
    boss_pos: Optional[Vec3] = None
    boss_anim_id: Optional[int] = None
    boss_anim_elapsed: Optional[float] = None
    igt_ms: Optional[int] = None            # in-game time; stops in loading, not in the pause menu
    death_count: Optional[int] = None
    # Changes on every game frame while the game is running and stands still in
    # the pause menu, so two equal consecutive tokens mean "not live".
    frame_token: Optional[int] = None
    locked_on: Optional[bool] = None        # lock-on is on the boss; not part of the observation
    gourds: Optional[int] = None            # Healing Gourd charges left

    @property
    def valid(self):
        """The minimum for a usable step: a plausible player HP pair."""
        return self.player_hp is not None and self.player_max_hp is not None


def _fraction(value, maximum):
    if value is None or maximum is None or maximum <= 0:
        return None
    return value / maximum


def _posture_fill(value, maximum):
    """How full the posture bar is: 0 = empty, 1 = broken. The raw value counts down."""
    fraction = _fraction(value, maximum)
    return None if fraction is None else 1.0 - fraction


def _clip_scaled(value, scale):
    if value is None or not math.isfinite(value):
        return None
    return max(-1.0, min(1.0, value / scale))


def _distance(snapshot):
    if snapshot.player_pos is None or snapshot.boss_pos is None:
        return None
    dx = snapshot.boss_pos[0] - snapshot.player_pos[0]
    dz = snapshot.boss_pos[2] - snapshot.player_pos[2]
    return _clip_scaled(math.hypot(dx, dz), DISTANCE_SCALE)


def _height_difference(snapshot):
    if snapshot.player_pos is None or snapshot.boss_pos is None:
        return None
    return _clip_scaled(snapshot.boss_pos[1] - snapshot.player_pos[1], HEIGHT_SCALE)


# Fixed scaling, not running statistics: drifting normalisation would silently
# invalidate cloned weights.
DISTANCE_SCALE = 20.0       # game units; assumed metres, unverified
HEIGHT_SCALE = 5.0
ANIM_ELAPSED_SCALE = 5.0    # seconds
# The same time again on a short scale. When to press inside an attack is a
# matter of tenths of a second, which is 0.02 on the 5 s scale: a cloning test
# on simulated attacks (2026-11-27) learned which attack it was at once and
# the moment inside it only much later. Not compared on real play.
ANIM_ELAPSED_FINE_SCALE = 1.0
GOURD_SCALE = 10.0          # the most charges the game allows

# Scalar fields in order.
OBS_FIELDS = (
    ("player_hp_frac", lambda s: _fraction(s.player_hp, s.player_max_hp)),
    ("player_posture_fill", lambda s: _posture_fill(s.player_posture, s.player_max_posture)),
    ("player_anim_elapsed", lambda s: _clip_scaled(s.player_anim_elapsed, ANIM_ELAPSED_SCALE)),
    ("boss_hp_frac", lambda s: _fraction(s.boss_hp, s.boss_max_hp)),
    ("boss_posture_fill", lambda s: _posture_fill(s.boss_posture, s.boss_max_posture)),
    ("boss_anim_elapsed", lambda s: _clip_scaled(s.boss_anim_elapsed, ANIM_ELAPSED_SCALE)),
    ("boss_distance", _distance),
    ("boss_height_diff", _height_difference),
    ("gourds", lambda s: _clip_scaled(s.gourds, GOURD_SCALE)),
    ("player_anim_elapsed_fine",
     lambda s: _clip_scaled(s.player_anim_elapsed, ANIM_ELAPSED_FINE_SCALE)),
    ("boss_anim_elapsed_fine", lambda s: _clip_scaled(s.boss_anim_elapsed, ANIM_ELAPSED_FINE_SCALE)),
)


def load_vocabulary(path=VOCABULARY_PATH):
    """Animation IDs that get their own slot: {"boss": (...), "player": (...)}.

    Built from logged fights by tools/build_vocabulary.py.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return {"boss": tuple(data["boss"]), "player": tuple(data["player"])}


VOCABULARY = load_vocabulary()
# Animation IDs are categorical: each is one-hot over its vocabulary, with a
# last slot for any ID that is not in it. name, getter, IDs.
ANIMATION_FIELDS = (
    ("boss_anim", lambda s: s.boss_anim_id, VOCABULARY["boss"]),
    ("player_anim", lambda s: s.player_anim_id, VOCABULARY["player"]),
)
_SLOTS = tuple({animation: slot for slot, animation in enumerate(ids)}
               for _, _, ids in ANIMATION_FIELDS)

# Layout: scalar values, one one-hot block per animation field, then the
# validity bits: one per scalar and one per animation field. A scalar's
# validity bit is at len(OBS_NAMES) + its index in OBS_NAMES.
OBS_NAMES = (tuple(name for name, _ in OBS_FIELDS)
             + tuple(f"{name}={animation}" for name, _, ids in ANIMATION_FIELDS
                     for animation in ids + ("unknown",)))
MASK_NAMES = tuple(name for name, _ in OBS_FIELDS) + tuple(name for name, _, _ in ANIMATION_FIELDS)
OBS_SIZE = len(OBS_NAMES) + len(MASK_NAMES)
OBS_LOW, OBS_HIGH = -1.0, 1.0

# v2: animation IDs added. v3: gourd charges and the fine animation times
# added. The suffix changes whenever
# the vocabulary does, so two layouts never share a version.
SCHEMA_VERSION = "v3-gourds-%08x" % zlib.crc32(json.dumps(
    [list(ids) for _, _, ids in ANIMATION_FIELDS]).encode())


def encode(snapshot):
    """Return [values..., validity bits...] as floats in [-1, 1].

    An unavailable field is written as value 0.0 with validity 0.0, so the
    policy can tell "zero" from "unknown". An unavailable animation ID is an
    all-zero block with validity 0.0. Length is always OBS_SIZE.
    """
    values, mask = [], []
    for _, getter in OBS_FIELDS:
        value = getter(snapshot)
        if value is None or not math.isfinite(value):
            values.append(0.0)
            mask.append(0.0)
        else:
            values.append(max(OBS_LOW, min(OBS_HIGH, float(value))))
            mask.append(1.0)
    for (_, getter, ids), slots in zip(ANIMATION_FIELDS, _SLOTS):
        animation = getter(snapshot)
        block = [0.0] * (len(ids) + 1)
        if animation is not None:
            block[slots.get(animation, len(ids))] = 1.0
        values += block
        mask.append(0.0 if animation is None else 1.0)
    return values + mask
