"""The live runner's episode function against the simulated rematch. Standard library only."""
import unittest

from sekirorl import actions
from sekirorl.actions import action_index as A
from sekirorl.encounter import Encounter
from sekirorl.fake import FakeRematch
from sekirorl.loop import StepLoop
from sekirorl.reset import RematchReset, RematchScript, Stick, presses
from sekirorl.runner import LoggingSource, describe, run_episode, wait_for_focus
from sekirorl.snapshot import Snapshot

SCRIPT = RematchScript(walk=(Stick((0.0, 1.0), 0.5),), sit=presses("pad_x"),
                       menu=presses("dpad_down") + presses("pad_a", 2), die=presses("pad_x"),
                       respawn=FakeRematch.RESPAWN)


def setup(**loop_kwargs):
    game = FakeRematch()
    rows, lines = [], []
    source = LoggingSource(game, rows.append, out=lines.append)
    loop = StepLoop(source, game, game, encounter=Encounter(),
                    reset_hook=RematchReset(source, game, game, SCRIPT), **loop_kwargs)
    return game, loop, source, rows, lines


class WaitForFocusTests(unittest.TestCase):
    def run_it(self, focused_from, focused_until=1e9, **kwargs):
        clock = [0.0]

        def sleep(seconds):
            clock[0] += seconds
        lines = []
        result = wait_for_focus(lambda: focused_from <= clock[0] < focused_until,
                                out=lines.append, sleep=sleep, **kwargs)
        return result, clock[0], lines

    def test_waits_for_the_window_and_then_a_little_more(self):
        result, waited, lines = self.run_it(focused_from=4.0)
        self.assertTrue(result)
        self.assertEqual(waited, 7.0)
        self.assertEqual(len(lines), 2)

    def test_gives_up_when_the_window_never_comes_to_the_front(self):
        result, waited, _ = self.run_it(focused_from=1e9, timeout_s=10.0)
        self.assertFalse(result)
        self.assertEqual(waited, 10.0)

    def test_focus_lost_during_the_last_seconds_counts_as_not_focused(self):
        result, _, _ = self.run_it(focused_from=0.0, focused_until=2.0)
        self.assertFalse(result)


class DescribeTests(unittest.TestCase):
    def test_situations(self):
        alive = dict(player_hp=960, player_max_hp=960, player_anim_id=0)
        cases = (
            (Snapshot(t_ns=0, reason="x"), "no player (loading screen or no save loaded)"),
            (Snapshot(t_ns=0, **alive), "player alive, boss not loaded"),
            (Snapshot(t_ns=0, **dict(alive, player_anim_id=710320)),
             "player alive, sitting at the idol, boss not loaded"),
            (Snapshot(t_ns=0, **dict(alive, player_anim_id=-1), boss_hp=7670, boss_anim_id=-1),
             "player alive, no control, boss loaded, not active"),
            (Snapshot(t_ns=0, **alive, boss_hp=9887, boss_anim_id=3014),
             "player alive, boss active"),
            (Snapshot(t_ns=0, **dict(alive, player_hp=0), boss_hp=9887, boss_anim_id=3014),
             "player dead, boss active"),
        )
        for snapshot, text in cases:
            self.assertEqual(describe(snapshot), text)


class RunEpisodeTests(unittest.TestCase):
    def test_death_episode_is_summarised_and_logged(self):
        game, loop, source, rows, lines = setup()
        result = run_episode(loop, source, lambda snapshot: actions.NOOP, rows.append, out=lines.append)
        loop.close()
        self.assertEqual((result["ending"], result["terminated"], result["invalid"]),
                         ("death", True, 0))
        kinds = [row["kind"] for row in rows]
        self.assertEqual(kinds.count("step"), result["steps"])
        self.assertEqual(kinds[-1], "episode")
        # one snapshot per step, plus those read while resetting
        self.assertGreater(kinds.count("snapshot"), result["steps"])
        self.assertTrue(any("terminated: death" in line for line in lines))
        self.assertTrue(any("boss active" in line for line in lines))

    def test_deathblow_is_reported(self):
        game, loop, source, rows, lines = setup()
        attack = A("stay+attack")
        result = run_episode(loop, source, lambda snapshot: attack, rows.append, out=lines.append)
        loop.close()
        self.assertEqual(result["ending"], "victory")
        self.assertTrue(any("deathblow (boss HP 1, posture 0)" in line for line in lines))
        steps = [row for row in rows if row["kind"] == "step"]
        self.assertEqual(steps[0]["applied"][0][1], ["attack"])

    def test_step_limit(self):
        game, loop, source, rows, lines = setup(max_steps=12)
        guard = A("stay+guard_hold")
        result = run_episode(loop, source, lambda snapshot: guard, rows.append, out=lines.append)
        loop.close()
        self.assertEqual((result["ending"], result["truncated"], result["steps"]),
                         ("step_limit", True, 12))

    def test_watch_lines_are_printed_and_logged(self):
        game, loop, source, rows, lines = setup(max_steps=3)
        seen = []

        def watch(snapshot):
            seen.append(snapshot)
            return "noted" if len(seen) == 2 else None

        run_episode(loop, source, lambda snapshot: actions.NOOP, rows.append, out=lines.append,
                    watch=watch)
        loop.close()
        self.assertEqual(len(seen), 3)
        self.assertIn("  step 2: noted", lines)
        self.assertEqual([row["step"] for row in rows if row["kind"] == "watch"], [2])


if __name__ == "__main__":
    unittest.main()
