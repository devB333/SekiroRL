"""Encounter rules: when a fight has started, when the player is dead and when
the boss has taken a deathblow. Pure logic on `Snapshot`s; standard library only.

Every rule below comes from the recordings described in MEMORY_READER.md
(Genichiro rematch, 2026-11-25). What those recordings did not cover is said
next to the rule that depends on it.
"""

# A character that is loaded but not yet active plays animation -1.
INACTIVE_ANIMATION = -1
# The player's walk-in at the start of the rematch; inputs do nothing until it ends.
# `700000` showed for 0.4 s between `-1` and `700010` after one respawn (2026-11-27).
PLAYER_INTRO_ANIMATIONS = frozenset({INACTIVE_ANIMATION, 700000, 700010})
# A deathblow sets the boss's HP to exactly this and holds it for about 2.6 s
# before refilling the bar.
DEATHBLOW_HP = 1


class Encounter:
    """Tracks one fight. Call `begin()` at reset, then `ready()` until it is
    true, then `update()` once per valid step.

    `deathblows_to_win` is the curriculum: 1 ends the episode at the first
    deathblow. The third health bar is a different character and is not
    handled here.
    """

    def __init__(self, deathblows_to_win=1, stable_samples=3):
        if deathblows_to_win < 1 or stable_samples < 1:
            raise ValueError("deathblows_to_win and stable_samples must be at least 1")
        self.deathblows_to_win = deathblows_to_win
        self.stable_samples = stable_samples
        self.begin()

    def begin(self):
        self.deathblows = 0
        self._boss = None           # last (hp, max_hp) seen
        self._down = False          # HP is sitting at DEATHBLOW_HP, waiting for the refill
        self._stable = (None, 0)    # (boss max HP, consecutive ready() calls it was seen)
        self._token = None

    def ready(self, snapshot):
        """Return (ready, reason). True once stepping would act on a running fight.

        After the loading screen the game stands still for about 6.5 s, the
        boss's maximum HP is rescaled twice (one sample can land mid-change),
        and the player walks in for about 2 s more.
        """
        token, self._token = self._token, snapshot.frame_token
        if snapshot.boss_max_hp is not None and snapshot.boss_max_hp == self._stable[0]:
            self._stable = (snapshot.boss_max_hp, self._stable[1] + 1)
        else:
            self._stable = (snapshot.boss_max_hp, 1 if snapshot.boss_max_hp is not None else 0)

        if not snapshot.valid:
            return False, snapshot.reason or "player HP unavailable"
        if snapshot.player_hp <= 0:
            return False, "player is dead"
        if snapshot.boss_hp is None:
            return False, snapshot.boss_reason or "boss unavailable"
        if snapshot.boss_anim_id is None or snapshot.boss_anim_id == INACTIVE_ANIMATION:
            return False, "boss not active"
        if snapshot.boss_hp != snapshot.boss_max_hp:
            return False, "boss not at full HP"
        if self._stable[1] < self.stable_samples:
            return False, "boss maximum HP not stable yet"
        if snapshot.player_anim_id is None or snapshot.player_anim_id in PLAYER_INTRO_ANIMATIONS:
            return False, "player does not have control yet"
        if snapshot.frame_token is None or snapshot.frame_token == token:
            return False, "game frame not advancing"
        return True, None

    def update(self, snapshot):
        """Feed one valid snapshot. Returns the events of this step.

        A deathblow is counted when boss HP reaches exactly 1 with posture 0
        (seen once, at the first deathblow), or, if that sample was missed,
        when HP jumps up by more than half the bar with the maximum unchanged.
        The rescale while the boss wakes up changes the maximum, so it never
        counts. Not covered by a recording: a boss whose HP was cut to zero by
        ordinary hits before the deathblow.
        """
        deathblow = False
        hp, maximum = snapshot.boss_hp, snapshot.boss_max_hp
        if hp is not None and maximum is not None:
            previous = self._boss
            if previous is not None and previous[1] == maximum:
                if (hp == DEATHBLOW_HP and snapshot.boss_posture == 0
                        and previous[0] > DEATHBLOW_HP and not self._down):
                    deathblow, self._down = True, True
                elif hp - previous[0] > maximum / 2:
                    if self._down:
                        self._down = False          # the refill after a counted deathblow
                    else:
                        deathblow = True
            self._boss = (hp, maximum)
        if deathblow:
            self.deathblows += 1
        return {
            "player_dead": snapshot.player_hp == 0,
            "deathblow": deathblow,
            "deathblows": self.deathblows,
            "victory": self.deathblows >= self.deathblows_to_win,
        }
