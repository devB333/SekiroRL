"""Hand-written policies. They read raw `Snapshot`s, not encoded observations.

`DeflectPolicy` is the S04-T06 check of the timing budget: deflect one boss
attack from its animation ID and elapsed time, and do nothing else.

The attack used by default is boss animation `3014`, which opened every fight
logged so far. What the logs say about it (ten occurrences on 2026-11-25
played by hand, and the scripted runs of 2026-11-27):

- The hit lands between 0.77 and 0.78 s of animation time, from about 5.8 m.
- Not guarded: player HP -249 and posture -67.
- Deflected: HP unchanged, player posture -26, and the player's animation goes
  to `50130201`. The boss's posture does not change. The user watched three
  scripted presses on 2026-11-27 (guard pressed 0.11 to 0.15 s before the
  hit) and saw a deflect each time; all three read like this. So did three
  guards by hand on 2026-11-25 where guard went up 0.05 s or less before the
  hit.
- Blocked: seen once, by hand, with guard up 0.5 s before the hit: player
  posture -52 and the player's animation goes to `50120204`.

So the player's reaction animation tells the two apart, not the boss's
posture: `5013xxxx` for a deflect and `5012xxxx` for a block. That fits the
other attacks in the logs of 2026-11-25 (`5013010x` 48 times with the boss
losing posture, `5012xxxx` 50 times with the player losing 31 to 111). It
rests on one screen check of one attack.

The boss's animation time as read is not always even: in one fight of
2026-11-27 it went 0.58, 0.53, 0.78, 0.73 on consecutive samples.
"""
from . import actions

OPENING_ATTACK = 3014
OPENING_ATTACK_HIT_S = 0.775


class DeflectPolicy:
    """Press guard once per occurrence of `animation`, on the first step that
    sees its elapsed time at or past `press_from`, then keep it held until
    the animation ends. All other steps are no-ops.

    The press reaches the game one inference plus one input delay after the
    snapshot (about 57 ms was measured for press-to-animation in Stage 2).
    At 10 steps per second the first sample at or past 0.58 s lies in
    0.58 to 0.68 s, which puts the press roughly 40 to 190 ms before the hit.
    """

    # The animation time read from memory sometimes steps back by 0.05 s or so
    # between samples. Only a larger step back counts as the attack starting again.
    RESTART_S = 0.25

    def __init__(self, animation=OPENING_ATTACK, press_from=0.58, hit_s=OPENING_ATTACK_HIT_S):
        if not 0 <= press_from < hit_s:
            raise ValueError("press_from must be before the hit")
        self.animation = animation
        self.press_from = press_from
        self.hit_s = hit_s
        self._deflect = actions.action_index("stay+deflect")
        self._hold = actions.action_index("stay+guard_hold")
        self._pressed = False
        self._last_elapsed = None
        self.presses = []           # animation elapsed time of the snapshot behind each press

    def __call__(self, snapshot):
        elapsed = snapshot.boss_anim_elapsed
        if snapshot.boss_anim_id != self.animation or elapsed is None:
            self._pressed, self._last_elapsed = False, None
            return actions.NOOP
        if self._last_elapsed is not None and elapsed < self._last_elapsed - self.RESTART_S:
            self._pressed = False                   # the same attack started again
        self._last_elapsed = elapsed
        if self._pressed:
            return self._hold
        if self.press_from <= elapsed < self.hit_s:
            self._pressed = True
            self.presses.append(elapsed)
            return self._deflect
        return actions.NOOP


DEFLECT_REACTIONS = 5013        # player animation // 10000
BLOCK_REACTIONS = 5012


class AttackOutcomes:
    """Scores every occurrence of one boss attack from consecutive snapshots.

    Feed each valid snapshot to `update`. An occurrence is closed `settle`
    samples after the animation was last seen, and `update` then returns its
    outcome:

    - "hit": the player lost HP.
    - "block": the player went into a block reaction (`5012xxxx`), or lost
      posture with no reaction animation that is known.
    - "deflect": the player went into a deflect reaction (`5013xxxx`) and
      into no block reaction.
    - "nothing": none of these; it did not connect.

    Only a reaction that starts during the occurrence counts; one still
    playing from the attack before does not. Raw posture counts down, so a
    fall is damage.
    """

    def __init__(self, animation=OPENING_ATTACK, settle=2):
        self.animation = animation
        self.settle = settle
        self.outcomes = []
        self._previous = None
        self._open = None       # dict of what the open occurrence has shown so far

    def update(self, snapshot):
        previous, self._previous = self._previous, snapshot
        in_attack = snapshot.boss_anim_id == self.animation
        if self._open is None:
            if not in_attack:
                return None
            self._open = dict(hp=0, posture=0, deflect=False, block=False, since=0)
        seen = self._open
        if previous is not None:
            for key, name in (("hp", "player_hp"), ("posture", "player_posture")):
                before, after = getattr(previous, name), getattr(snapshot, name)
                if before is not None and after is not None and after < before:
                    seen[key] += before - after
            reaction = snapshot.player_anim_id
            if reaction is not None and reaction != previous.player_anim_id:
                seen["deflect"] |= reaction // 10000 == DEFLECT_REACTIONS
                seen["block"] |= reaction // 10000 == BLOCK_REACTIONS
        seen["since"] = 0 if in_attack else seen["since"] + 1
        if seen["since"] < self.settle:
            return None
        self._open = None
        outcome = ("hit" if seen["hp"] else "block" if seen["block"]
                   else "deflect" if seen["deflect"] else "block" if seen["posture"]
                   else "nothing")
        self.outcomes.append(outcome)
        return outcome
