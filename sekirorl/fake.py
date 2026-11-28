"""Simulated game for automated tests: state source, controller and clock in one.

Time only moves when the loop sleeps (or a test calls `advance`), so tests are
deterministic and instant.
"""
import cmath
import math

from .controller import NEUTRAL
from .snapshot import Snapshot


class FakeGame:
    def __init__(self, max_hp=320, hits=(), unavailable=(), paused=(), menu=(), with_igt=True,
                 boss=None, script=()):
        """
        hits: (time_s, damage) pairs; a hit does nothing while guard is held.
        unavailable: (start_s, end_s) windows where reads fail, as in a loading screen.
        paused: (start_s, end_s) windows where in-game time stands still.
        menu: (start_s, end_s) windows where the pause menu is open: the frame
            token stands still but in-game time keeps running, as in the real game.
        boss: None for no boss, or a dict of Snapshot boss fields, for example
            {"boss_hp": 9887, "boss_max_hp": 9887, "boss_posture": 4083,
             "boss_max_posture": 4083, "boss_anim_id": 405003}.
        script: (time_s, {field: value}) pairs applied in time order. A field is
            a Snapshot boss field, "player_anim_id" or "hp" (the player's).
        """
        self.max_hp = max_hp
        self.hp = max_hp
        self.hits = sorted(hits)
        self.unavailable = tuple(unavailable)
        self.paused = tuple(paused)
        self.menu = tuple(menu)
        self.with_igt = with_igt
        self.boss = dict(boss) if boss is not None else None
        self.script = sorted(script, key=lambda item: item[0])
        self.player_anim_id = 0
        self._script_index = 0
        self.t = 0.0
        self.state = NEUTRAL
        self.log = []           # (time_s, ControlState)
        self.closed = False
        self.reads = 0
        self._hit_index = 0
        self._igt = 0.0
        self._frames = 0.0

    # -- clock -------------------------------------------------------------

    def now(self):
        return self.t

    def sleep_until(self, deadline):
        if deadline <= self.t:
            return
        while self._hit_index < len(self.hits) and self.hits[self._hit_index][0] <= deadline:
            _, damage = self.hits[self._hit_index]
            self._hit_index += 1
            if "guard" not in self.state.buttons:
                self.hp = max(0, self.hp - damage)
        while (self._script_index < len(self.script)
               and self.script[self._script_index][0] <= deadline):
            for name, value in self.script[self._script_index][1].items():
                if name == "hp":
                    self.hp = value
                elif name == "player_anim_id":
                    self.player_anim_id = value
                else:
                    self.boss[name] = value
            self._script_index += 1
        if not self._in(self.paused, self.t):
            self._igt += deadline - self.t
            if not self._in(self.menu, self.t):
                self._frames += deadline - self.t
        self.t = deadline

    def _walk(self, seconds):
        x, y = self.state.stick
        wanted = (x * self._stick_axes[0] + y * self._stick_axes[1]) * self.WALK_SPEED
        step = wanted * seconds
        if self.inertia_s:
            # Exact for a constant stick: the velocity closes on `wanted` exponentially.
            decay = math.exp(-seconds / self.inertia_s)
            step += (self._velocity - wanted) * self.inertia_s * (1.0 - decay)
            self._velocity = wanted + (self._velocity - wanted) * decay
        if step:
            self.pos = (self.pos[0] + step.real, self.pos[1], self.pos[2] + step.imag)
            self.path.append((self.pos[0], self.pos[2]))

    def advance(self, seconds):
        """Simulate the caller being busy (for example a policy update)."""
        self.sleep_until(self.t + seconds)

    # -- controller --------------------------------------------------------

    def apply(self, state):
        self.state = state
        self.log.append((self.t, state))

    def neutral(self):
        self.apply(NEUTRAL)

    def close(self):
        self.neutral()
        self.closed = True

    def press_times(self, button):
        """Times of rising edges for one button."""
        times, held = [], False
        for t, state in self.log:
            down = button in state.buttons
            if down and not held:
                times.append(t)
            held = down
        return times

    # -- state source ------------------------------------------------------

    @staticmethod
    def _in(windows, t):
        return any(start <= t < end for start, end in windows)

    def read(self):
        self.reads += 1
        t_ns = int(self.t * 1e9)
        if self._in(self.unavailable, self.t):
            return Snapshot(t_ns=t_ns, reason="Invalid/null pointer (simulated)")
        fields = dict(self.boss) if self.boss is not None else {}
        if self.boss is not None:
            fields["player_anim_id"] = self.player_anim_id
        return Snapshot(t_ns=t_ns, player_hp=self.hp, player_max_hp=self.max_hp,
                        igt_ms=int(self._igt * 1000) if self.with_igt else None,
                        frame_token=int(self._frames * 1000) if self.with_igt else None,
                        **fields)

    def restart(self):
        """Stand-in for a supervised reset: back to full health."""
        self.hp = self.max_hp


class FakeRematch:
    """The idol, its menu, the rematch and death, with the animation IDs and
    timings logged on 2026-11-25. State source, controller and clock in one,
    like `FakeGame`. It reacts to controller input, so a reset script and
    whole episodes can be run against it.

    Simplifications: the stick moves the player at once, relative to a camera
    that never turns (`camera_deg` is the map direction of stick forward,
    measured from +x towards +z; `mirrored` swaps the side stick right is on;
    with `inertia_s` the player's velocity follows the stick with that time
    constant, so turns and stops lag); a menu press arriving sooner than `menu_gap_s` after the last one is lost;
    the boss hits every `hit_every_s` unless guard is held at that moment; an
    attack press takes `attack_damage` and the boss takes a deathblow when
    that would empty the bar.
    """
    RESPAWN = (161.22, -29.01, 23.0)
    IDOL = (163.6, -29.05, 22.8)        # the player sat between x 163.3 and 163.8
    ARENA = (-103.0, 53.9, 243.1)
    BOSS_ID = 71001000
    BOSS_HP, BOSS_POSTURE = 9887, 4083
    WALK_SPEED = 4.6                    # m/s: 2.3 m in the 0.5 s walks that were logged
    USE_RADIUS = 1.0                    # assumed; the real reach of the idol is not known

    # phase: (player animation, seconds or None, next phase)
    PHASES = {
        "idle": (0, None, None),
        "sit_down": (710310, 1.1, "sitting"),
        "sitting": (710320, None, None),
        "entering": (710360, 2.6, "load_in"),
        "load_in": (None, 1.4, "arena_frozen"),
        "arena_frozen": (-1, 6.3, "walk_in"),
        "walk_in": (700010, 1.0, "boss_wakes"),
        "boss_wakes": (700010, 1.0, "fight"),
        "fight": (0, None, None),
        "cutscene": (None, 2.0, "fight"),       # after the last bar; the fight goes on
        "dying": (110000, 3.3, None),           # then "prompt" or "dead"
        "prompt": (110020, None, None),
        "resurrecting": (110030, 1.5, "fight"),
        "dead": (110010, 4.1, "load_out"),
        "load_out": (None, 1.3, "respawn_frozen"),
        "respawn_frozen": (-1, 4.2, "respawn_walk"),
        "respawn_walk": (700010, 2.0, "idle"),
    }
    UNAVAILABLE = ("load_in", "load_out", "cutscene")
    FROZEN = ("arena_frozen", "respawn_frozen")
    IN_ARENA = ("arena_frozen", "walk_in", "boss_wakes", "fight", "cutscene", "dying",
                "prompt", "resurrecting", "dead")

    def __init__(self, menu=("dpad_down", "pad_a", "pad_a"), other_menu=None, sit="pad_x",
                 die="pad_x", resurrect="attack", resurrections=1, max_hp=960,
                 first_hit_s=2.3, hit_every_s=3.0, hit_damage=249, attack_damage=340,
                 menu_gap_s=0.2, phase="idle", camera_deg=0.0, mirrored=False,
                 inertia_s=0.0, bars=None, lock_readable=True, lock_stuck_off=False,
                 lock_drops_s=()):
        """
        menu: the presses that start the rematch from the idol menu.
        other_menu: presses that start a different fight, where the boss is
            never found. Any other press leaves the menu stuck.
        """
        self.menu = tuple(menu)
        self.other_menu = tuple(other_menu) if other_menu else None
        self.sit, self.die, self.resurrect = sit, die, resurrect
        self.resurrections_per_attempt = resurrections
        self.max_hp = max_hp
        self.first_hit_s, self.hit_every_s = first_hit_s, hit_every_s
        self.hit_damage, self.attack_damage = hit_damage, attack_damage
        self.menu_gap_s = menu_gap_s
        self.bars = bars
        # Lock-on: a press toggles it while the boss is there; every fight starts
        # without it. `lock_drops_s`: seconds into a fight at which it is lost.
        # `lock_readable=False`: the snapshot does not say. `lock_stuck_off`: the
        # snapshot always says it is off, whatever the game does.
        self.lock_readable, self.lock_stuck_off = lock_readable, lock_stuck_off
        self.lock_drops_s = tuple(lock_drops_s)
        self.locked = False
        self._fight_began = None
        self._drops = []
        forward = cmath.rect(1.0, math.radians(camera_deg))     # x + z*1j
        self._stick_axes = (forward * (1j if mirrored else -1j), forward)
        self.inertia_s = inertia_s
        self._velocity = 0j
        self.t = 0.0
        self.state = NEUTRAL
        self.log = []                   # (time_s, ControlState)
        self.closed = False
        self.deaths = 0
        self.deathblows = 0
        self._fight_deathblows = 0
        self.fights_started = 0
        self.hp = max_hp
        self.pos = self.RESPAWN
        self.path = []                  # (x, z) after every walked stretch
        self.resurrections = resurrections
        self._right_boss = True
        self._igt = 0.0
        self._frames = 0.0
        self._next_hit = None
        self._refill_at = None
        self.boss_hp = self.boss_max = self.BOSS_HP
        self.boss_posture = self.boss_max_posture = self.BOSS_POSTURE
        self._enter(phase)
        if phase in self.IN_ARENA:
            self.pos = self.ARENA

    # -- phases ------------------------------------------------------------

    def _enter(self, phase):
        self.phase = phase
        _, seconds, _ = self.PHASES[phase]
        self._phase_end = None if seconds is None else self.t + seconds
        if phase == "sitting":
            self._pressed, self._last_press = [], -1e9
        elif phase == "arena_frozen":
            self.locked = False
            self.pos = self.ARENA
            self.boss_hp = self.boss_max = 7670
            self.boss_posture = self.boss_max_posture = 3168
            self._refill_at = None
            self._fight_deathblows = 0      # every rematch starts with all bars again
        elif phase == "boss_wakes":
            self.boss_hp = self.boss_max = self.BOSS_HP
            self.boss_posture = self.boss_max_posture = self.BOSS_POSTURE
        elif phase == "fight":
            if self._next_hit is None:      # not set when coming back from a resurrection
                self.fights_started += 1
                self._next_hit = self.t + self.first_hit_s
                self._drops = [self.t + seconds for seconds in self.lock_drops_s]
        elif phase == "dying":
            self.hp = 0
            self._next_hit = None
        elif phase == "resurrecting":
            self.hp = self.max_hp // 2
            self.resurrections -= 1
            self._next_hit = self.t + 1.5 + self.hit_every_s
        elif phase == "dead":
            self.deaths += 1
        elif phase == "respawn_frozen":
            self.hp = self.max_hp
            self.resurrections = self.resurrections_per_attempt
            self.pos = (self.RESPAWN[0], -28.6, self.RESPAWN[2])
        elif phase == "idle":
            self.pos = self.RESPAWN

    def _phase_over(self):
        if self.phase == "dying":
            self._enter("prompt" if self.resurrections > 0 else "dead")
        else:
            self._enter(self.PHASES[self.phase][2])

    def _press(self, button):
        if self.phase == "idle":
            if button == self.sit and _dist(self.pos, self.IDOL) <= self.USE_RADIUS:
                self._enter("sit_down")
        elif self.phase == "sitting":
            if self.t - self._last_press < self.menu_gap_s:
                return
            self._last_press = self.t
            if button == "pad_b":               # back: one level up, or stand up
                if self._pressed:
                    self._pressed = []
                else:
                    where = self.pos
                    self._enter("idle")
                    self.pos = where
                return
            self._pressed.append(button)
            pressed = tuple(self._pressed)
            if pressed == self.menu or pressed == self.other_menu:
                self._right_boss = pressed == self.menu
                self._enter("entering")
        elif self.phase == "prompt":
            if button == self.die:
                self._enter("dead")
            elif button == self.resurrect:
                self._enter("resurrecting")
        elif self.phase == "fight" and button == "lock_on" and self._right_boss:
            self.locked = not self.locked
        elif self.phase == "fight" and button == "attack" and self._right_boss:
            if self._refill_at is None:
                self.boss_hp -= self.attack_damage
                if self.boss_hp <= 1:
                    self.boss_hp, self.boss_posture = 1, 0
                    self._refill_at = self.t + 2.6

    # -- clock -------------------------------------------------------------

    def now(self):
        return self.t

    def sleep_until(self, deadline):
        while self.t < deadline:
            upto = min([deadline] + self._drops[:1]
                       + [event for event in (self._phase_end, self._next_hit, self._refill_at)
                          if event is not None])
            elapsed = max(0.0, upto - self.t)
            if self.phase not in self.UNAVAILABLE:
                self._igt += elapsed
                if self.phase not in self.FROZEN:
                    self._frames += elapsed
            if self.phase == "idle":
                self._walk(elapsed)
            self.t = max(self.t, upto)
            if self._phase_end is not None and self.t >= self._phase_end:
                self._phase_over()
            while self._drops and self.t >= self._drops[0]:
                self._drops.pop(0)
                self.locked = False
            if self._refill_at is not None and self.t >= self._refill_at:
                self._refill_at = None
                self.deathblows += 1
                self._fight_deathblows += 1
                if self.bars is not None and self._fight_deathblows == self.bars:
                    # The last bar: a cutscene, then a character the lookup does not find.
                    self._right_boss = False
                    self._next_hit = self.t + 2.0 + self.first_hit_s
                    self._enter("cutscene")
                else:
                    self.boss_hp, self.boss_posture = self.boss_max, self.boss_max_posture
            if self._next_hit is not None and self.t >= self._next_hit:
                self._next_hit = self.t + self.hit_every_s
                if "guard" not in self.state.buttons:
                    self.hp = max(0, self.hp - self.hit_damage)
                    if self.hp == 0:
                        self._enter("dying")

    def _walk(self, seconds):
        x, y = self.state.stick
        wanted = (x * self._stick_axes[0] + y * self._stick_axes[1]) * self.WALK_SPEED
        step = wanted * seconds
        if self.inertia_s:
            # Exact for a constant stick: the velocity closes on `wanted` exponentially.
            decay = math.exp(-seconds / self.inertia_s)
            step += (self._velocity - wanted) * self.inertia_s * (1.0 - decay)
            self._velocity = wanted + (self._velocity - wanted) * decay
        if step:
            self.pos = (self.pos[0] + step.real, self.pos[1], self.pos[2] + step.imag)
            self.path.append((self.pos[0], self.pos[2]))

    def advance(self, seconds):
        self.sleep_until(self.t + seconds)

    # -- controller --------------------------------------------------------

    def apply(self, state):
        new = state.buttons - self.state.buttons
        self.state = state
        self.log.append((self.t, state))
        for button in sorted(new):
            self._press(button)

    def neutral(self):
        self.apply(NEUTRAL)

    def close(self):
        self.neutral()
        self.closed = True

    def press_count(self, button):
        """Number of rising edges of one button."""
        count, held = 0, False
        for _, state in self.log:
            down = button in state.buttons
            count += down and not held
            held = down
        return count

    # -- state source ------------------------------------------------------

    def read(self):
        t_ns = int(self.t * 1e9)
        if self.phase in self.UNAVAILABLE:
            return Snapshot(t_ns=t_ns, reason="Invalid/null pointer (simulated)")
        fields = dict(player_hp=self.hp, player_max_hp=self.max_hp, player_pos=self.pos,
                      player_anim_id=self.PHASES[self.phase][0],
                      igt_ms=int(self._igt * 1000), frame_token=int(self._frames * 1000),
                      death_count=self.deaths)
        if self.phase in self.IN_ARENA and self._right_boss:
            awake = self.phase not in ("arena_frozen", "walk_in")
            fields.update(boss_npc_id=self.BOSS_ID, boss_hp=self.boss_hp,
                          boss_max_hp=self.boss_max, boss_posture=self.boss_posture,
                          boss_max_posture=self.boss_max_posture,
                          boss_anim_id=405003 if awake else -1,
                          boss_pos=(self.ARENA[0] - 15.0, self.ARENA[1], self.ARENA[2]))
            if self.lock_readable:
                fields.update(locked_on=self.locked and not self.lock_stuck_off)
        elif self.phase in self.IN_ARENA:
            fields.update(boss_reason="Boss not in the character collection")
        return Snapshot(t_ns=t_ns, **fields)


def _dist(a, b):
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
