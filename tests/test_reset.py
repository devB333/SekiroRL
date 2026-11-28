"""The automatic reset against the simulated idol and rematch, alone and
under the step loop with the encounter rules. Standard library only."""
import dataclasses
import math
import unittest

from sekirorl import actions
from sekirorl.actions import action_index as A
from sekirorl.controller import NEUTRAL, ControlState
from sekirorl.encounter import Encounter
from sekirorl.fake import FakeRematch
from sekirorl.loop import ResetFailed, StepLoop
from sekirorl.reset import (AWAY, BUSY, DEAD, FIGHT, GENICHIRO_REMATCH, IDOL, LOADING, LockKeeper,
                            OFF_MARK, SAT, Press, RematchReset, RematchScript, RetryingReset, Stick,
                            Wait, WalkTo, classify, presses)
from sekirorl.snapshot import Snapshot

MENU = ("dpad_down", "dpad_down", "pad_a", "pad_a")
SCRIPT = RematchScript(
    walk=(Stick((0.0, 1.0), 0.5),),
    sit=presses("pad_x"),
    menu=presses("dpad_down", 2) + presses("pad_a", 2),
    die=presses("pad_x"),
    respawn=FakeRematch.RESPAWN,
)


def make(script=SCRIPT, focus_check=None, **game_kwargs):
    game_kwargs.setdefault("menu", MENU)
    game = FakeRematch(**game_kwargs)
    return game, RematchReset(game, game, game, script, focus_check=focus_check)


def make_loop(game, reset, **kwargs):
    kwargs.setdefault("reset_timeout_s", 30.0)
    return StepLoop(game, game, game, encounter=Encounter(), reset_hook=reset, **kwargs)


def run_episode(loop, action, limit=5000):
    loop.reset()
    for _ in range(limit):
        _, _, terminated, truncated, info = loop.step(action)
        if terminated or truncated:
            return info
    raise AssertionError("episode did not end")


class ClassifyTests(unittest.TestCase):
    def snap(self, **fields):
        values = dict(player_hp=960, player_max_hp=960, player_anim_id=0,
                      player_pos=SCRIPT.respawn)
        values.update(fields)
        return Snapshot(t_ns=0, **values)

    def test_states(self):
        x, y, z = SCRIPT.respawn
        cases = (
            (Snapshot(t_ns=0, reason="Invalid/null pointer"), LOADING),
            (self.snap(player_hp=0, player_anim_id=110020), DEAD),
            (self.snap(boss_hp=9887, boss_max_hp=9887, player_anim_id=700010), FIGHT),
            (self.snap(player_anim_id=710320, player_pos=(x + 2.3, y, z)), SAT),
            (self.snap(player_anim_id=-1), BUSY),
            (self.snap(player_anim_id=700010), BUSY),
            (self.snap(player_anim_id=710360), BUSY),
            (self.snap(player_anim_id=None), BUSY),
            (self.snap(player_pos=None), BUSY),
            (self.snap(), IDOL),
            (self.snap(player_pos=(x + 2.3, y, z)), OFF_MARK),
            (self.snap(player_anim_id=710310, player_pos=(x + 2.3, y, z)), OFF_MARK),
            (self.snap(player_pos=(-103.0, 53.9, 243.1)), AWAY),
        )
        for snapshot, state in cases:
            with self.subTest(state=state, animation=snapshot.player_anim_id):
                self.assertEqual(classify(snapshot, SCRIPT), state)


class ResetTests(unittest.TestCase):
    def test_from_the_respawn_point_into_the_fight(self):
        game, reset = make()
        reset()
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)
        self.assertEqual(game.state, NEUTRAL)
        self.assertEqual(reset.log[-1][1], "boss loaded")

    def test_from_the_idol_menu_already_open(self):
        game, reset = make(phase="sitting")
        reset()
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)
        self.assertEqual(game.press_count("pad_x"), 0)

    def test_from_a_death_with_the_prompt_chooses_die(self):
        game, reset = make(phase="dying", resurrections=1)
        reset()
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)
        self.assertEqual(game.deaths, 1)
        self.assertEqual(game.hp, game.max_hp)

    def test_from_a_death_only_the_die_input_is_sent_until_the_idol(self):
        game, reset = make(phase="dying", resurrections=0)
        reset()
        respawned = next(t for t, message in reset.log if message == "state: idol")
        early = {button for t, state in game.log if t < respawned - 0.01
                 for button in state.buttons}
        self.assertEqual(early, {"pad_x"})
        self.assertFalse([state for t, state in game.log
                          if t < respawned - 0.01 and state.stick != (0.0, 0.0)])
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)

    def test_die_is_pressed_whatever_animation_the_death_shows(self):
        game, reset = make(phase="dying", resurrections=1)
        game.PHASES = dict(game.PHASES, dying=(227600170, 3.3, None), prompt=(227610172, None, None))
        game._enter("dying")
        reset()
        self.assertEqual(game.deaths, 1)
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)

    def test_a_moment_that_looks_like_control_during_the_respawn_is_waited_out(self):
        """On 2026-11-27 an animation that was not known showed for 0.4 s
        between the frozen respawn and the walk-in, and the walk began in it."""
        game, reset = make(phase="dying", resurrections=0)
        game.PHASES = dict(game.PHASES, respawn_frozen=(-1, 4.2, "respawn_blink"),
                           respawn_blink=(123456, 0.4, "respawn_walk"))
        reset()
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)
        walked = min(t for t, state in game.log if state.stick != (0.0, 0.0))
        self.assertGreaterEqual(walked, next(t for t, m in reversed(reset.log) if m == "state: idol"))

    def test_a_running_fight_is_left_by_dying(self):
        game, reset = make(phase="fight")
        game.advance(1.0)
        reset()
        self.assertEqual(game.deaths, 1)
        self.assertEqual(classify(game.read(), SCRIPT), FIGHT)
        game.advance(9.0)                       # the reset returns while the fight loads
        self.assertEqual(game.fights_started, 2)

    def test_abandon_inputs_are_used_when_the_script_has_them(self):
        script = RematchScript(walk=SCRIPT.walk, sit=SCRIPT.sit, menu=SCRIPT.menu, die=SCRIPT.die,
                               respawn=SCRIPT.respawn, abandon=presses("pad_start"))
        game, reset = make(script=script, phase="fight", hit_every_s=1.0)
        reset()
        self.assertEqual(game.press_count("pad_start"), 1)

    def test_presses_are_spaced_so_the_menu_takes_each_one(self):
        fast = RematchScript(walk=SCRIPT.walk, sit=SCRIPT.sit, die=SCRIPT.die,
                             respawn=SCRIPT.respawn,
                             menu=presses("dpad_down", 2, then_s=0.05) + presses("pad_a", 2))
        game, reset = make(script=fast)
        with self.assertRaisesRegex(ResetFailed, "confirming the rematch.*still 'sitting'"):
            reset()
        self.assertEqual(game.state, NEUTRAL)


class ResetFailureTests(unittest.TestCase):
    def test_wrong_menu_inputs_are_reported_as_still_sitting(self):
        game, reset = make(menu=("dpad_down", "pad_a", "pad_a"))
        with self.assertRaisesRegex(ResetFailed, "confirming the rematch.*still 'sitting'"):
            reset()
        self.assertEqual(game.state, NEUTRAL)
        self.assertTrue(reset.log[-1][1].startswith("FAILED"))

    def test_a_different_fight_is_reported(self):
        game, reset = make(menu=("pad_a",), other_menu=MENU)
        with self.assertRaisesRegex(ResetFailed, "boss is not loaded.*'away'"):
            reset()
        self.assertEqual(game.state, NEUTRAL)

    def test_a_walk_that_does_not_reach_the_idol_is_reported(self):
        short = RematchScript(walk=(Stick((0.0, 1.0), 0.1),), sit=SCRIPT.sit, menu=SCRIPT.menu,
                              die=SCRIPT.die, respawn=SCRIPT.respawn)
        game, reset = make(script=short)
        with self.assertRaisesRegex(ResetFailed, "sitting down at the idol"):
            reset()

    def test_standing_off_the_respawn_point_is_reported_without_any_input(self):
        game, reset = make()
        game.pos = (game.RESPAWN[0] + 1.5, game.RESPAWN[1], game.RESPAWN[2])
        with self.assertRaisesRegex(ResetFailed, "1.5 m from the respawn point"):
            reset()
        self.assertFalse([state for _, state in game.log if state != NEUTRAL])

    def test_prompt_without_die_inputs_is_reported(self):
        script = RematchScript(walk=SCRIPT.walk, sit=SCRIPT.sit, menu=SCRIPT.menu, die=(),
                               respawn=SCRIPT.respawn)
        game, reset = make(script=script, phase="dying", resurrections=1)
        with self.assertRaisesRegex(ResetFailed, "Resurrect/Die prompt"):
            reset()

    def test_a_wrong_die_button_is_retried_then_reported(self):
        script = RematchScript(walk=SCRIPT.walk, sit=SCRIPT.sit, menu=SCRIPT.menu,
                               die=presses("pad_b"), respawn=SCRIPT.respawn)
        game, reset = make(script=script, phase="dying", resurrections=1)
        with self.assertRaisesRegex(ResetFailed, "respawn at the idol.*still 'dead'"):
            reset()
        self.assertGreater(game.press_count("pad_b"), 3)
        self.assertEqual(game.state, NEUTRAL)

    def test_no_input_is_sent_without_focus(self):
        game, reset = make(focus_check=lambda: False)
        with self.assertRaisesRegex(ResetFailed, "not in foreground"):
            reset()
        self.assertFalse([state for _, state in game.log if state != NEUTRAL])

    def test_losing_focus_part_way_stops_the_inputs(self):
        game = FakeRematch(menu=MENU)
        reset = RematchReset(game, game, game, SCRIPT, focus_check=lambda: game.t < 3.0)
        with self.assertRaisesRegex(ResetFailed, "not in foreground"):
            reset()
        self.assertEqual(game.state, NEUTRAL)
        self.assertFalse([state for t, state in game.log if t >= 3.0 and state != NEUTRAL])

    def test_a_player_who_is_nowhere_near_the_idol_and_never_dies_times_out(self):
        game, reset = make()
        game.pos = (0.0, 0.0, 0.0)
        reset.death_timeout_s = 5.0
        with self.assertRaisesRegex(ResetFailed, "waiting to be killed.*'away'"):
            reset()


# The user's notes of 2026-11-27, written out press by press.
GENICHIRO_MENU = ("dpad_down",) * 8 + ("pad_a",) + ("dpad_down",) * 2 + ("pad_a", "pad_a")


def make_genichiro(**game_kwargs):
    return make(script=GENICHIRO_REMATCH, menu=GENICHIRO_MENU, die="guard", **game_kwargs)


class WalkToTests(unittest.TestCase):
    """The walk steers from the position alone, whatever the camera."""

    def walk(self, start=FakeRematch.RESPAWN, step=WalkTo((163.5, 22.8)), **game_kwargs):
        game, reset = make(**game_kwargs)
        game.pos = start
        reset._play((step,), "walk")
        return game, reset

    def test_reaches_the_mark_for_any_camera_direction_and_handedness(self):
        for mirrored in (False, True):
            for camera_deg in range(0, 360, 30):
                with self.subTest(camera_deg=camera_deg, mirrored=mirrored):
                    game, _ = self.walk(camera_deg=camera_deg, mirrored=mirrored)
                    self.assertLessEqual(math.dist((game.pos[0], game.pos[2]), (163.5, 22.8)), 0.3)
                    self.assertEqual(game.state, NEUTRAL)

    def test_reaches_the_mark_when_turns_and_stops_lag(self):
        for speed in (3.0, 6.5):
            for camera_deg in range(0, 360, 45):
                with self.subTest(speed=speed, camera_deg=camera_deg):
                    game, reset = make(camera_deg=camera_deg, mirrored=True, inertia_s=0.2)
                    game.WALK_SPEED = speed
                    reset._play((WalkTo((163.5, 22.8)),), "walk")
                    game.advance(1.0)               # any slide left over
                    self.assertLessEqual(math.dist((game.pos[0], game.pos[2]), (163.5, 22.8)), 0.35)

    def test_reaches_the_mark_from_anywhere_near_it(self):
        x, y, z = FakeRematch.RESPAWN
        for start in ((x, y, z + 3.0), (x + 4.0, y, z - 2.0), (x + 2.2, y, z)):
            for camera_deg in (0, 135, 250):
                with self.subTest(start=start, camera_deg=camera_deg):
                    game, _ = self.walk(start=start, camera_deg=camera_deg, mirrored=True)
                    self.assertLessEqual(math.dist((game.pos[0], game.pos[2]), (163.5, 22.8)), 0.3)

    def test_already_on_the_mark_sends_no_stick(self):
        game, _ = self.walk(start=(163.4, -29.0, 22.9))
        self.assertFalse([state for _, state in game.log if state.stick != (0.0, 0.0)])

    def test_stays_within_a_small_area(self):
        game, _ = self.walk(camera_deg=180)         # the first push goes straight away
        farthest = max(math.dist((x, z), (163.5, 22.8)) for x, z in game.path)
        self.assertLess(farthest, 2.3 + 0.5)

    def test_too_far_away_is_refused_without_any_input(self):
        x, y, z = FakeRematch.RESPAWN
        game, reset = make()
        game.pos = (x - 5.0, y, z)
        with self.assertRaisesRegex(ResetFailed, "7.3 m away"):
            reset._play((WalkTo((163.5, 22.8)),), "walk")
        self.assertFalse([state for _, state in game.log if state != NEUTRAL])

    def test_a_player_who_cannot_move_times_out_with_the_stick_released(self):
        game, reset = make()
        game.WALK_SPEED = 0.0
        with self.assertRaisesRegex(ResetFailed, "walking to the idol: 2.3 m away.*never moved"):
            reset._play((WalkTo((163.5, 22.8), timeout_s=2.0),), "walk")
        self.assertEqual(game.state, NEUTRAL)
        self.assertLess(game.t, 4.0)        # the timeout plus one stretch

    def test_losing_focus_releases_the_stick(self):
        game = FakeRematch(menu=MENU)
        reset = RematchReset(game, game, game, SCRIPT, focus_check=lambda: game.t < 0.2)
        with self.assertRaisesRegex(ResetFailed, "not in foreground"):
            reset._play((WalkTo((163.5, 22.8)),), "walk")
        self.assertEqual(game.state, NEUTRAL)


class GenichiroScriptTests(unittest.TestCase):
    """The script for the user's save against the simulation set up from the
    user's notes. This checks the script against the notes, not the game."""

    def test_the_presses_are_the_ones_in_the_notes(self):
        game, reset = make_genichiro(camera_deg=200)
        reset()
        self.assertEqual(classify(game.read(), GENICHIRO_REMATCH), FIGHT)
        sent = [button for _, state in game.log for button in state.buttons]
        self.assertEqual(tuple(sent), ("pad_x",) + GENICHIRO_MENU)

    def test_one_lost_press_is_reported_not_missed(self):
        steps = GENICHIRO_REMATCH.menu
        for lost, step in enumerate(steps):
            if not isinstance(step, Press):
                continue
            script = dataclasses.replace(GENICHIRO_REMATCH, menu=steps[:lost] + steps[lost + 1:])
            with self.subTest(lost=lost):
                game, reset = make(script=script, menu=GENICHIRO_MENU, die="guard")
                with self.assertRaisesRegex(ResetFailed, "still 'sitting'"):
                    reset()
                self.assertEqual(game.state, NEUTRAL)

    def test_death_chooses_die_with_lb_and_starts_again(self):
        game, reset = make_genichiro(phase="dying", resurrections=1, camera_deg=75)
        reset()
        self.assertEqual(game.deaths, 1)
        self.assertGreaterEqual(game.press_count("guard"), 1)
        self.assertEqual(classify(game.read(), GENICHIRO_REMATCH), FIGHT)

    def test_starts_from_anywhere_near_the_idol(self):
        game, reset = make_genichiro()
        game.pos = (162.4, -29.0, 21.5)
        reset()
        self.assertEqual(classify(game.read(), GENICHIRO_REMATCH), FIGHT)

    def test_ten_episodes_in_a_row(self):
        game, reset = make_genichiro(camera_deg=310, mirrored=True)
        loop = make_loop(game, reset)
        for episode in range(1, 11):
            info = run_episode(loop, A("stay+attack") if episode % 2 else actions.NOOP)
            self.assertEqual(info["termination_reason"], "victory" if episode % 2 else "death")
            self.assertEqual(game.fights_started, episode)
        loop.close()


class RecoveryTests(unittest.TestCase):
    """Backing out of a menu left open, and trying a failed reset again."""

    def test_a_menu_left_open_part_way_is_backed_out_of_and_started_again(self):
        game, reset = make_genichiro(phase="sitting")
        game.apply(ControlState(buttons=frozenset({"dpad_down"})))     # the cursor is not on top
        game.neutral()
        game.advance(1.0)
        reset()
        self.assertEqual(classify(game.read(), GENICHIRO_REMATCH), FIGHT)
        self.assertGreaterEqual(game.press_count("pad_b"), 2)
        self.assertEqual(game.press_count("pad_x"), 1)

    def test_a_menu_that_will_not_close_is_reported(self):
        game, reset = make_genichiro(phase="sitting")
        game._press = lambda button: None               # the game ignores everything
        with self.assertRaisesRegex(ResetFailed, "leaving the idol menu"):
            reset()

    def test_a_failed_reset_is_tried_again_from_where_it_stopped(self):
        game, reset = make_genichiro()
        game.menu_gap_s = 0.5                           # the first try loses menu presses
        retrying = RetryingReset(reset, game, wait_s=2.0)
        original = game._press

        def press(button):
            original(button)
            if game.phase == "idle" and len(retrying.failures) == 1:
                game.menu_gap_s = 0.2                   # the second try is not disturbed
        game._press = press
        retrying()
        self.assertEqual(len(retrying.failures), 1)
        self.assertIn("still 'sitting'", retrying.failures[0])
        self.assertEqual(classify(game.read(), GENICHIRO_REMATCH), FIGHT)

    def test_it_gives_up_after_the_last_try(self):
        game, reset = make(script=GENICHIRO_REMATCH, menu=("pad_a",), die="guard")
        retrying = RetryingReset(reset, game, attempts=3, wait_s=1.0)
        with self.assertRaises(ResetFailed):
            retrying()
        self.assertEqual(len(retrying.failures), 3)
        self.assertEqual(game.state, NEUTRAL)

    def test_a_window_that_is_not_in_front_is_not_retried(self):
        game = FakeRematch(menu=GENICHIRO_MENU, die="guard")
        reset = RematchReset(game, game, game, GENICHIRO_REMATCH, focus_check=lambda: False)
        retrying = RetryingReset(reset, game)
        with self.assertRaisesRegex(ResetFailed, "not in foreground"):
            retrying()
        self.assertEqual(len(retrying.failures), 1)


class LockKeeperTests(unittest.TestCase):
    """Lock-on kept on the boss through whole episodes on the simulation."""

    def fight(self, steps=60, action=None, **game_kwargs):
        game, reset = make(hit_every_s=1e9, first_hit_s=1e9, **game_kwargs)
        keeper = LockKeeper()
        loop = make_loop(game, reset, lock_keeper=keeper)
        loop.reset()
        infos = [loop.step(A("stay+guard_hold") if action is None else action)[4]
                 for _ in range(steps)]
        return game, keeper, loop, infos

    def test_one_press_at_the_start_and_no_more_while_the_lock_holds(self):
        game, keeper, loop, infos = self.fight()
        self.assertTrue(game.locked)
        self.assertEqual(game.press_count("lock_on"), 1)
        self.assertTrue(infos[0]["lock_pressed"])
        self.assertFalse(any(info["lock_pressed"] for info in infos[1:]))
        self.assertTrue(keeper.confirmed)
        loop.close()

    def test_the_press_rides_on_the_action_and_is_released_within_the_step(self):
        game, keeper, loop, infos = self.fight(steps=3)
        first = infos[0]["applied"]
        self.assertEqual(first[0][1].buttons, frozenset({"guard", "lock_on"}))
        self.assertEqual(first[-1][1].buttons, frozenset({"guard"}))
        self.assertEqual(game.state.buttons, frozenset({"guard"}))
        loop.close()

    def test_a_lost_lock_is_taken_again(self):
        game, keeper, loop, infos = self.fight(lock_drops_s=(2.0, 4.0))
        self.assertTrue(game.locked)
        self.assertEqual(game.press_count("lock_on"), 3)
        loop.close()

    def test_a_reading_that_never_shows_the_lock_stops_the_presses(self):
        """Otherwise every press after the first would switch a good lock off."""
        game, keeper, loop, infos = self.fight(lock_stuck_off=True)
        self.assertEqual(game.press_count("lock_on"), 3)
        self.assertTrue(keeper.gave_up)
        self.assertTrue(game.locked)            # three presses: on, off, on
        loop.close()

    def test_without_a_reading_lock_on_is_pressed_once_per_fight(self):
        game, keeper, loop, infos = self.fight(lock_readable=False)
        self.assertEqual(game.press_count("lock_on"), 1)
        self.assertTrue(game.locked)
        loop.close()

    def test_presses_are_at_least_a_second_apart(self):
        game, keeper, loop, infos = self.fight(lock_stuck_off=True)
        times = [t for t, state in game.log if "lock_on" in state.buttons]
        self.assertTrue(all(b - a >= 0.99 for a, b in zip(times, times[1:])))
        loop.close()

    def test_every_fight_starts_with_a_new_press(self):
        game, reset = make()
        loop = make_loop(game, reset, lock_keeper=LockKeeper())
        for episode in range(1, 4):
            run_episode(loop, actions.NOOP)
            self.assertEqual(game.press_count("lock_on"), episode)
        loop.close()

    def test_no_lock_press_without_focus(self):
        game, reset = make(hit_every_s=1e9, first_hit_s=1e9)
        loop = make_loop(game, reset, lock_keeper=LockKeeper())
        loop.reset()
        loop.focus_check = lambda: False
        loop.step(actions.NOOP)
        self.assertEqual(game.press_count("lock_on"), 0)
        loop.close()


class EpisodeTests(unittest.TestCase):
    """Whole episodes: step loop, encounter rules and reset together."""

    def test_deaths_reset_into_a_new_fight_every_time(self):
        game, reset = make()
        loop = make_loop(game, reset)
        for episode in range(1, 6):
            info = run_episode(loop, actions.NOOP)
            self.assertEqual(info["termination_reason"], "death")
            self.assertEqual(game.fights_started, episode)
        self.assertEqual(game.deaths, 4)        # the fifth death is still on screen
        loop.close()

    def test_a_deathblow_ends_the_episode_and_the_next_reset_leaves_the_fight(self):
        game, reset = make()
        loop = make_loop(game, reset)
        info = run_episode(loop, A("stay+attack"))
        self.assertEqual(info["termination_reason"], "victory")
        self.assertEqual(game.deaths, 0)
        info = run_episode(loop, A("stay+attack"))
        self.assertEqual(info["termination_reason"], "victory")
        self.assertEqual((game.deaths, game.fights_started), (1, 2))
        loop.close()

    def test_a_win_on_the_last_bar_is_left_through_the_cutscene_and_the_next_fight(self):
        game, reset = make(bars=2)
        loop = StepLoop(game, game, game, encounter=Encounter(deathblows_to_win=2),
                        reset_hook=reset, reset_timeout_s=30.0)
        for episode in range(1, 4):
            info = run_episode(loop, A("stay+attack"))
            self.assertEqual(info["termination_reason"], "victory")
            self.assertEqual(info["encounter"]["deathblows"], 2)
            self.assertEqual(game.fights_started, episode)
        self.assertEqual(game.deaths, 2)
        self.assertIn("state: away", [message for _, message in reset.log])
        loop.close()

    def test_the_step_limit_ending_resets_too(self):
        game, reset = make()
        loop = make_loop(game, reset, max_steps=15)
        for episode in range(1, 4):
            info = run_episode(loop, A("stay+guard_hold"))
            self.assertEqual(info["truncation_reason"], "step_limit")
            self.assertEqual(game.fights_started, episode)
        loop.close()

    def test_a_failed_reset_leaves_inputs_neutral_and_the_loop_unusable_until_reset(self):
        game, reset = make(menu=("pad_a",))
        loop = make_loop(game, reset)
        with self.assertRaises(ResetFailed):
            loop.reset()
        self.assertEqual(game.state, NEUTRAL)
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)
        loop.close()


if __name__ == "__main__":
    unittest.main()
