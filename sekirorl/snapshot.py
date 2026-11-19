"""Raw game state for one sample, and the fixed observation layout built from it.

Standard library only. `None` always means "unavailable"; it is never replaced
by zero health. Demonstrations should store `Snapshot`s, not encoded
observations, so the layout can change without re-recording.
"""
from dataclasses import dataclass
import math
from typing import Optional, Tuple

SCHEMA_VERSION = "v1-scalars"

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class Snapshot:
    t_ns: int
    reason: Optional[str] = None            # why the sample is unavailable, if it is
    player_hp: Optional[int] = None
    player_max_hp: Optional[int] = None
    player_posture: Optional[int] = None
    player_max_posture: Optional[int] = None
    player_pos: Optional[Vec3] = None
    player_anim_id: Optional[int] = None
    player_anim_elapsed: Optional[float] = None
    boss_hp: Optional[int] = None
    boss_max_hp: Optional[int] = None
    boss_posture: Optional[int] = None
    boss_max_posture: Optional[int] = None
    boss_pos: Optional[Vec3] = None
    boss_anim_id: Optional[int] = None
    boss_anim_elapsed: Optional[float] = None
    igt_ms: Optional[int] = None            # in-game time, used as a liveness check

    @property
    def valid(self):
        """The minimum for a usable step: a plausible player HP pair."""
        return self.player_hp is not None and self.player_max_hp is not None


def _fraction(value, maximum):
    if value is None or maximum is None or maximum <= 0:
        return None
    return value / maximum


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

# Scalar fields in order. Animation IDs are categorical and need a vocabulary
# built from recorded fights, so they arrive in a later schema version.
OBS_FIELDS = (
    ("player_hp_frac", lambda s: _fraction(s.player_hp, s.player_max_hp)),
    ("player_posture_frac", lambda s: _fraction(s.player_posture, s.player_max_posture)),
    ("player_anim_elapsed", lambda s: _clip_scaled(s.player_anim_elapsed, ANIM_ELAPSED_SCALE)),
    ("boss_hp_frac", lambda s: _fraction(s.boss_hp, s.boss_max_hp)),
    ("boss_posture_frac", lambda s: _fraction(s.boss_posture, s.boss_max_posture)),
    ("boss_anim_elapsed", lambda s: _clip_scaled(s.boss_anim_elapsed, ANIM_ELAPSED_SCALE)),
    ("boss_distance", _distance),
    ("boss_height_diff", _height_difference),
)
OBS_NAMES = tuple(name for name, _ in OBS_FIELDS)
OBS_SIZE = 2 * len(OBS_FIELDS)
OBS_LOW, OBS_HIGH = -1.0, 1.0


def encode(snapshot):
    """Return [values..., validity bits...] as floats in [-1, 1].

    An unavailable field is written as value 0.0 with validity 0.0, so the
    policy can tell "zero" from "unknown". Length is always OBS_SIZE.
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
    return values + mask
