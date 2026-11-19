"""Step loop behaviour against the fake game. Standard library only."""
import time
import unittest

from sekirorl import actions
from sekirorl.actions import action_index as A
from sekirorl.controller import NEUTRAL, ControlState, RecordingController
from sekirorl.fake import FakeGame
from sekirorl.loop import ResetFailed, StepLoop
from sekirorl.snapshot import OBS_NAMES, OBS_SIZE, Snapshot, encode

HP = OBS_NAMES.index("player_hp_frac")
HP_MASK = len(OBS_NAMES) + HP


def make(game=None, **kwargs):
    game = game or FakeGame()
    loop = StepLoop(game, game, game, **kwargs)
    return game, loop


class ObservationTests(unittest.TestCase):
    def test_fixed_length_under_every_validity_pattern(self):
        snapshots = [
            Snapshot(t_ns=0, reason="x"),
            Snapshot(t_ns=0, player_hp=0, player_max_hp=320),
            Snapshot(t_ns=0, player_hp=320, player_max_hp=320, boss_hp=5, boss_max_hp=10,
                     player_pos=(0, 0, 0), boss_pos=(3, 9, 4), boss_anim_elapsed=99.0),
            Snapshot(t_ns=0, player_hp=1, player_max_hp=0, boss_anim_elapsed=float("nan")),
        ]
        for snapshot in snapshots:
            observation = encode(snapshot)
            self.assertEqual(len(observation), OBS_SIZE)
            self.assertTrue(all(-1.0 <= value <= 1.0 for value in observation))

    def test_unavailable_is_zero_value_with_zero_mask(self):
        self.assertEqual(encode(Snapshot(t_ns=0, reason="x")), [0.0] * OBS_SIZE)

    def test_real_zero_hp_is_distinguishable_from_unavailable(self):
        observation = encode(Snapshot(t_ns=0, player_hp=0, player_max_hp=320))
        self.assertEqual((observation[HP], observation[HP_MASK]), (0.0, 1.0))

    def test_unverified_fields_stay_masked(self):
        game, loop = make()
        observation, _ = loop.reset()
        self.assertEqual(observation[len(OBS_NAMES):], [1.0] + [0.0] * (len(OBS_NAMES) - 1))


class StepTests(unittest.TestCase):
    def test_damage_gives_negative_normalised_reward(self):
        game, loop = make(FakeGame(hits=[(0.15, 80)]))
        loop.reset()
        _, first, *_ = loop.step(actions.NOOP)
        observation, second, terminated, truncated, info = loop.step(actions.NOOP)
        self.assertEqual(first, 0.0)
        self.assertAlmostEqual(second, -80 / 320)
        self.assertAlmostEqual(observation[HP], 240 / 320)
        self.assertFalse(terminated or truncated)
        self.assertEqual(info["player_hp"], 240)

    def test_invalid_read_gives_no_reward_and_no_zero_hp(self):
        game, loop = make(FakeGame(unavailable=[(0.05, 0.25)]))
        loop.reset()
        observation, reward, terminated, truncated, info = loop.step(actions.NOOP)
        self.assertFalse(info["valid"])
        self.assertIsNone(info["player_hp"])
        self.assertEqual(reward, 0.0)
        self.assertEqual((observation[HP], observation[HP_MASK]), (0.0, 0.0))
        self.assertFalse(terminated or truncated)

    def test_no_reward_across_an_invalid_gap(self):
        game, loop = make(FakeGame(hits=[(0.15, 80)], unavailable=[(0.05, 0.25)]))
        loop.reset()
        rewards = [loop.step(actions.NOOP)[1] for _ in range(4)]
        self.assertEqual(game.hp, 240)
        self.assertEqual(rewards, [0.0, 0.0, 0.0, 0.0])

    def test_paused_in_game_time_is_invalid(self):
        game, loop = make(FakeGame(hits=[(0.15, 80)], paused=[(0.0, 10.0)]))
        loop.reset()
        _, reward, _, _, info = loop.step(actions.NOOP)
        self.assertFalse(info["valid"])
        self.assertEqual(info["reason"], "in-game time not advancing")
        self.assertEqual(loop.step(actions.NOOP)[1], 0.0)

    def test_truncates_and_releases_after_repeated_invalid_reads(self):
        game, loop = make(FakeGame(unavailable=[(0.05, 99.0)]), max_invalid_steps=3)
        loop.reset()
        results = [loop.step(A("forward+guard_hold")) for _ in range(3)]
        self.assertEqual([r[3] for r in results], [False, False, True])
        self.assertEqual(results[-1][4]["truncation_reason"], "invalid_reads")
        self.assertEqual(game.state, NEUTRAL)
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)

    def test_step_limit_truncates_never_terminates(self):
        game, loop = make(max_steps=5)
        loop.reset()
        results = [loop.step(A("forward+none")) for _ in range(5)]
        self.assertTrue(results[-1][3])
        self.assertFalse(any(r[2] for r in results))
        summary = results[-1][4]["timing_summary"]
        self.assertEqual(summary["steps"], 5)
        self.assertAlmostEqual(summary["steps_per_second"], 10.0)
        self.assertEqual(game.state, NEUTRAL)

    def test_exception_inside_step_releases_inputs_and_propagates(self):
        game, loop = make()
        loop.reset()
        loop.step(A("forward+guard_hold"))
        self.assertIn("guard", game.state.buttons)

        def boom():
            raise OSError("read exploded")
        game.read = boom
        with self.assertRaises(OSError):
            loop.step(A("forward+guard_hold"))
        self.assertEqual(game.state, NEUTRAL)
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)

    def test_rejects_out_of_range_action(self):
        game, loop = make()
        loop.reset()
        with self.assertRaises(ValueError):
            loop.step(actions.NUM_ACTIONS)
        self.assertEqual(game.state, NEUTRAL)

    def test_step_before_reset_is_an_error(self):
        game, loop = make()
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)


class InputTests(unittest.TestCase):
    def test_tap_presses_and_releases_inside_one_step(self):
        game, loop = make()
        loop.reset()
        loop.step(A("stay+attack"))
        states = [(round(t, 3), "attack" in state.buttons) for t, state in game.log[-2:]]
        self.assertEqual(states, [(0.0, True), (0.05, False)])

    def test_repeated_taps_are_distinct_presses(self):
        game, loop = make()
        loop.reset()
        for _ in range(3):
            loop.step(A("stay+attack"))
        self.assertEqual([round(t, 3) for t in game.press_times("attack")], [0.0, 0.1, 0.2])

    def test_consecutive_guard_hold_never_releases(self):
        game, loop = make()
        loop.reset()
        for _ in range(4):
            loop.step(A("stay+guard_hold"))
        self.assertEqual(len(game.press_times("guard")), 1)

    def test_guard_blocks_damage_in_fake(self):
        game, loop = make(FakeGame(hits=[(0.15, 80)]))
        loop.reset()
        rewards = [loop.step(A("stay+guard_hold"))[1] for _ in range(3)]
        self.assertEqual((game.hp, rewards), (320, [0.0, 0.0, 0.0]))

    def test_deflect_while_holding_releases_then_presses_again(self):
        game, loop = make()
        loop.reset()
        loop.step(A("stay+guard_hold"))
        loop.step(A("stay+deflect"))
        self.assertEqual([round(t, 3) for t in game.press_times("guard")], [0.0, 0.134])
        self.assertIn("guard", game.state.buttons)

    def test_deflect_when_not_holding_presses_immediately(self):
        game, loop = make()
        loop.reset()
        loop.step(A("stay+deflect"))
        self.assertEqual(game.press_times("guard"), [0.0])

    def test_movement_is_held_and_unnamed_inputs_are_released(self):
        game, loop = make()
        loop.reset()
        loop.step(A("left+guard_hold"))
        self.assertEqual(game.state, ControlState(frozenset({"guard"}), (-1.0, 0.0)))
        loop.step(A("forward+none"))
        self.assertEqual(game.state, ControlState(frozenset(), (0.0, 1.0)))

    def test_action_table(self):
        self.assertEqual(actions.NUM_ACTIONS, 30)
        self.assertEqual(len(set(actions.ACTION_NAMES)), 30)
        self.assertEqual(actions.ACTION_NAMES[actions.NOOP], "stay+none")

    def test_unknown_button_is_rejected(self):
        with self.assertRaises(ValueError):
            ControlState(frozenset({"start"}))

    def test_timing_parameters_are_validated(self):
        with self.assertRaises(ValueError):
            make(step_interval=0.06, tap_s=0.05)
        with self.assertRaises(ValueError):
            make(step_interval=0)


class SafetyTests(unittest.TestCase):
    def test_reset_releases_inputs(self):
        game, loop = make()
        loop.reset()
        loop.step(A("forward+guard_hold"))
        loop.reset()
        self.assertEqual(game.state, NEUTRAL)

    def test_reset_hook_runs_and_waits_through_loading(self):
        game = FakeGame(unavailable=[(0.0, 1.0)])
        calls = []
        _, loop = make(game, reset_hook=lambda: calls.append(1))
        observation, info = loop.reset()
        self.assertEqual(calls, [1])
        self.assertGreaterEqual(game.t, 1.0)
        self.assertEqual(observation[HP_MASK], 1.0)

    def test_reset_fails_loudly_when_state_never_arrives(self):
        game, loop = make(FakeGame(unavailable=[(0.0, 999.0)]), reset_timeout_s=2.0)
        with self.assertRaises(ResetFailed):
            loop.reset()
        self.assertEqual(game.state, NEUTRAL)

    def test_close_releases_and_closes_controller(self):
        game, loop = make()
        loop.reset()
        loop.step(A("forward+guard_hold"))
        loop.close()
        self.assertEqual(game.state, NEUTRAL)
        self.assertTrue(game.closed)
        with self.assertRaises(RuntimeError):
            loop.step(actions.NOOP)

    def test_long_gap_between_steps_truncates_without_reward(self):
        game, loop = make(FakeGame(hits=[(0.5, 80)]))
        loop.reset()
        loop.step(A("forward+guard_hold"))
        game.neutral()              # what the watchdog would have done
        game.advance(1.0)           # trainer busy; the game kept running
        logged_before = len(game.log)
        _, reward, _, truncated, info = loop.step(A("stay+attack"))
        self.assertTrue(truncated)
        self.assertEqual(info["truncation_reason"], "gap")
        self.assertEqual(reward, 0.0)
        self.assertFalse(info["valid"])
        self.assertTrue(all(state == NEUTRAL for _, state in game.log[logged_before:]))

    def test_short_overrun_resynchronises(self):
        game, loop = make()
        loop.reset()
        loop.step(actions.NOOP)
        game.advance(0.15)
        _, _, _, truncated, info = loop.step(actions.NOOP)
        self.assertFalse(truncated)
        self.assertTrue(info["timing"]["overrun"])
        self.assertAlmostEqual(game.t, 0.35)

    def test_late_action_keeps_the_grid(self):
        game, loop = make()
        loop.reset()
        loop.step(actions.NOOP)
        game.advance(0.02)
        _, _, _, _, info = loop.step(actions.NOOP)
        self.assertAlmostEqual(info["timing"]["apply_late_s"], 0.02)
        self.assertAlmostEqual(game.t, 0.2)

    def test_lost_focus_sends_nothing_and_is_invalid(self):
        focused = [True]
        game, loop = make(focus_check=lambda: focused[0])
        loop.reset()
        loop.step(A("forward+guard_hold"))
        focused[0] = False
        _, reward, _, _, info = loop.step(A("stay+attack"))
        self.assertEqual(game.state, NEUTRAL)
        self.assertEqual(game.press_times("attack"), [])
        self.assertEqual(info["reason"], "game window not in foreground")
        self.assertEqual(reward, 0.0)

    def test_watchdog_releases_inputs_when_steps_stop(self):
        game = FakeGame()
        controller = RecordingController()
        loop = StepLoop(game, controller, game, watchdog_s=0.05)
        try:
            loop.reset()
            loop.step(A("forward+guard_hold"))
            self.assertIn("guard", controller.state.buttons)
            time.sleep(0.3)
            self.assertEqual(controller.state, NEUTRAL)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
