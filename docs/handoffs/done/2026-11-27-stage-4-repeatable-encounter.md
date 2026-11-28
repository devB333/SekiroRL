# Handoff: Stage 4, repeatable encounter (in progress)

**Closed: the user approved G04 on 2026-11-27 with the one-hour soak waived. Kept as the record of Stage 4; the status text below is as it stood when the report was given.**

Written: 2026-11-27. Status: **Stage 4 work is done as far as it will go without a
decision; the G04 report was given to the user on 2026-11-27 and is waiting for approval.
Do not start Stage 5 or 6 before the user approves. None of the Stage 4 code is
committed.**

Put to the user with the report, not yet answered: (1) waive the one-hour soak; (2)
accept that a victory, a step-limit ending and the second deathblow have not been seen
live; (3) run a PPO-from-scratch run (S06-T04) first, before the demonstrations of
Stage 5, because the game takes only one controller per launch and the pad pass-through
(S05-T01) has to solve that first. The user confirmed on 2026-11-27 that Wolf stood idle
after the alt-tab.

How this folder works: `current.md` describes the task in progress. When a gate is approved,
move this file to `done/` (named `YYYY-MM-DD-short-title.md`) and write a new `current.md`.
The previous handoff is `done/2026-11-25-stage-3-memory-fields.md`; it has the full list of
what was found in memory and how.

## Read first

1. `AGENTS.md` - project rules, including the read-only memory contract, and all commands.
2. `docs/plan.md` - decisions, staged tasks, gates, and the gate report format (section C).
3. `MEMORY_READER.md`, the last two sections - what the game does in memory at fight start,
   death, resurrection, a deathblow, at the idol, and during the boss's opening attack.

## What the user wants

Get a model training on the fight. **Changed on 2026-11-27:** the user wants episodes to
"get as far as possible in the fight", not to end at the first deathblow as agreed on
2026-11-25. For now that means both health bars of the first Genichiro: the runner's
default is `--deathblows 2` (`Encounter(deathblows_to_win=2)`). The third health bar is a
different character (entity `1110801`) that the reader does not find; going past the
second deathblow needs that found first, and the user was told so.

Not seen in any recording or live run: the second deathblow (whether HP reads 1 with
posture 0 again), and what follows it. The reset now allows for a loading screen that is
not the respawn (it goes back to waiting to be killed, up to three times); that is tested
on a guess built into `FakeRematch(bars=2)` only.

**Also asked for by the user on 2026-11-27:**

- **Lock-on.** The agent must be locked on to Genichiro in every fight (the user presses
  R3). The 33 fights before this were not locked on, so stick directions were relative to
  a free camera. The lock state was then found in memory (`MEMORY_READER.md`, last
  section): the lock-on manager holds the locked character's handle. `Snapshot.locked_on`
  is true when that is the boss's handle (not part of the observation).
  `reset.LockKeeper`, passed as `StepLoop(lock_keeper=...)`, taps `lock_on` inside a step
  when the snapshot says the lock is not on the boss, at most once a second, and gives up
  for the fight after three presses that never read as locked. The runner uses it with
  `--send` unless `--no-lock-on`. The user watched the marker toggle with all eight presses
  of the experiment. Checked in one game session only.
  Live check (`logs/2026-11-27/run-191111.jsonl`, 3 fights, random policy, the new reward
  and `--deathblows 2`): one press on step 1 of each fight, locked on the boss for every
  step after it until the death (fights of 39.8, 15.6 and 5.4 s), 0 invalid steps of 608.
  The lock never dropped during a fight, so re-locking has only run on the simulation.
- **Reward with posture.** `loop.FightReward` (used by the runner; `StepLoop` still
  defaults to `hp_reward`): boss HP lost +1.0 per bar, boss posture damage +2.0 per bar,
  player HP lost -1.0 per bar, player posture damage -0.25 per bar (the user asked for it
  to count "a bit", less than health; 0.25 is the agent's number). Recovered boss posture is not taken
  back. The user said posture matters more than HP because it is what wins the fight most
  times; the factor of 2 is the agent's choice, not the user's number.

## Task

Stage 4 of `docs/plan.md`, tasks S04-T01 to S04-T07. Stop at gate G04.

| Task | Status | What is left |
|---|---|---|
| S04-T01 Deathblow and victory signal | In progress | Ten live fights with no false victory: 10 fights on 2026-11-27 all ended `death`, none `victory`; no live deathblow under the runner yet |
| S04-T02 Player death and termination | In progress | Seen live 10 times, three different death animation sets; the user has not been asked to confirm each ended at the right moment |
| S04-T03 Episode start detection | In progress | Seen live 10 times, 8.3 to 8.4 s after the boss loaded, opening attack at step 23 or 24 every time |
| S04-T04 Automatic reset | In progress | 20 in a row done (the soak; 30 of 31 over the day). Not seen live: a reset after a step-limit ending or after a victory; both go through "stand still until killed" |
| S04-T05 Animation vocabulary | In progress | Rebuilt on 2026-11-27 (74 boss, 184 player IDs). No fight has gone past the first health bar, so IDs that only show later fall in the unknown slot |
| S04-T06 Scripted deflect | In progress | 8 of 8 live, all eight seen as deflects by the user. Not tried: other values of `--deflect-from` |
| S04-T07 Unattended-run safety | In progress | Done: a 20-minute random-policy soak; alt-tab during a fight (below). Left: a one-hour soak; alt-tab in the middle of the menu inputs (simulation only); keep-awake and reattaching after a game restart are not written |

## Next step

1. **Step-limit ending, live:** `--send --auto-reset --policy deflect --max-steps 50 --episodes 2`
   (about 3 minutes). The episode is cut after 5 s and the reset has to wait for the boss
   to kill the player. A victory ending cannot be produced by the bot yet.
2. S04-T07: the one-hour soak (`--send --auto-reset --policy random --episodes 60`). The
   user was asked on Discord.
3. The G04 report (the user was asked to waive the one-hour soak; see below).
4. Later, for throughput: HP 0 to the fight running is 40 to 45 s. The menu inputs take
   7.7 s of it and were never shortened; the boss intro takes 8.4 s.

**Before any run with `--send`: start Sekiro with the user's controller off** and load the
save with the keyboard. With the user's controller on when the game started, nothing from
the virtual pad reached the game for that whole game session, even after the controller
was switched off (two failed runs, then `logs/scratch/pad_check.py <pid>`: full stick,
0.6 stick, attack and jump all without effect; after a restart with the controller off,
all four worked). `XInputGetState` in our process showed only the virtual pad, on slot 0,
throughout, so the user's controller is not an XInput pad; the guess is that it goes
through Steam Input and holds the game's first slot. The user has not said which
controller it is. Playing by hand and running the bot in one game session is therefore
not possible for now.

## Live results, 2026-11-27 (game restarted once, PID 21488 for all of these)

| Log | Command | Result |
|---|---|---|
| `run-165554.jsonl` | `--send --auto-reset --episodes 2` | 2 resets, 2 fights ended `death`, 0 invalid steps of 142. Opening attack: hit, hit. No LB was pressed at the death (see below); the prompt gave up after 15 s |
| `run-165834.jsonl` | `... --policy deflect --episodes 5` | 3 resets and fights, then the fourth reset failed in the walk: a respawn animation that was not known (`700000`, 0.4 s) looked like standing with control. 0 invalid steps of 338. The user saw a deflect with sparks in all three fights; the scoring then in use said "block" |
| `run-170516.jsonl` | `... --policy deflect --episodes 5`, after the fixes | 5 resets, 5 fights, 0 invalid steps of 446. Opening attack: deflect 5 of 5 by the new scoring, and the user saw a deflect in each of the five |

**Alt-tab during a fight** (`run-180147.jsonl`, the user alt-tabbed 1.8 s into the first
fight): step 18 and the four after it were invalid ("game window not in foreground") with
nothing applied and the inputs released; the episode was truncated (`invalid_reads`); the
next reset waited for the death, then refused to press Die without focus and failed with
"game window not in foreground while trying to choose Die"; inputs neutral at exit. A
later run (`run-173658.jsonl`) had added 3 more clean resets and fights.

**The soak** (`run-171332.jsonl`, `--send --auto-reset --policy random --episodes 20`, the
user away from the PC): 20 resets of 20, 20 fights all ended `death`, 0 invalid steps of
4,204, wake lateness at most 0.05 ms, no overruns, inputs neutral at exit, 20 minutes.
Fights lasted 8.5 to 55.0 s. Every walk took 3 or 4 stretches. Random presses took some
boss HP in a few fights (9505 of 9887 at the end of the last). No deathblow, no victory.

- The fight was found running 8.3 to 8.4 s after the boss loaded, every time. Wake lateness
  at most 0.03 ms, no overruns.
- The walk took 3 or 4 stretches (6 the first time, from 1.8 m off to the side) and ended
  0.01 to 0.24 m from the mark. X sat the player down every time. The handedness
  correction never fired.
- The menu inputs worked 30 times of 30 over the day with the slow timings.
- Guard was pressed on the sample at 0.58 to 0.65 s of the attack (once 0.73 s by the
  runner's note, after a release and second press caused by the animation time stepping
  back; that policy fault is fixed).

Fixes made between the runs: Die is pressed every 1.5 s while HP is 0, whatever the
animation (the death animations vary with the killing move); the reset waits until the
player has stood with control for 0.5 s before walking, and `700000` is in
`PLAYER_INTRO_ANIMATIONS`; `AttackOutcomes` scores deflect or block by the player's
reaction animation (`5013xxxx` or `5012xxxx`) instead of the boss's posture;
`DeflectPolicy` only takes a step back of more than 0.25 s in the animation time as a new
attack. `MEMORY_READER.md` has the observations behind each.

## The user's menu notes (2026-11-27)

- Walk up to the idol, X to sit. The cursor always starts on the first entry.
- 8 down, A (Reflections of Strength), 2 down ("Genichiro Ashina"), A, A.
- The Resurrect/Die prompt shows after a death. LB is Die. The respawn is standing next
  to the idol, not in its menu.
- The fight goes on after the first deathblow. Only after the second deathblow and a
  cutscene is the player placed somewhere else (the user is not sure of this).
- Gourds are full at every start.
- No way out of a running fight except quitting to the main menu.
- Not answered: whether the idol can be used from the respawn point without walking. The
  script always walks.

## What was built on 2026-11-27 (uncommitted)

- `GENICHIRO_REMATCH` in `sekirorl/reset.py`: the inputs above, with LB as `guard`.
- `WalkTo` in `sekirorl/reset.py`: walks to a point (x, z) in stop-and-go stretches. The
  stick is camera-relative and the camera is not read, so the first stretch is a short push
  forward and the direction the player went gives the camera's forward; speed per stick
  size and the side stick-right is on are measured the same way. It refuses to start from
  more than 5 m and stops if the player gets 1.5 m farther away than at the start. With a
  walk made of `WalkTo` steps a reset may start from anywhere near the idol, not only the
  respawn point. The mark is (163.5, 22.8), inside the area where the user sat on
  2026-11-25 (x 163.3 to 163.8, z 22.4 to 23.2), reached to within 0.3 m.
- `FakeRematch` has `camera_deg`, `mirrored` and `inertia_s` so the walk is tested for any
  camera direction, either handedness, and lagging turns and stops.
- `runner.wait_for_focus`: with `--auto-reset` nothing is sent until the game window is in
  front and 3 s have passed.

## What was built on 2026-11-26 (uncommitted, not run live)

- `sekirorl/reset.py`: `RematchReset`, a callable for `StepLoop(reset_hook=...)`. From any
  ending it gets the player into the loading fight: it leaves a running fight (by the
  script's `abandon` inputs, or by standing still until killed), chooses Die when the
  prompt animation `110020` shows, waits for the respawn, walks, sits, plays the menu
  inputs and waits for the boss to be loaded. Each stage waits for a state read from memory
  (`classify`) and raises `ResetFailed` naming the stage. It asks `focus_check` before every
  input. `reset.log` keeps the trace of the last call.
- `sekirorl/runner.py`: live runner. Dry run unless `--send`. Logs every snapshot and step
  to `logs/<date>/run-<time>.jsonl`, prints a line when the situation changes, and scores
  every opening attack.
- `sekirorl/scripted.py`: `DeflectPolicy` (one guard press per occurrence of boss animation
  `3014`, on the first sample at or past 0.58 s) and `AttackOutcomes`.
- `sekirorl/fake.py`: `FakeRematch`, a simulated idol, menu, fight, death and respawn with
  the logged animation IDs and timings. Its combat is crude (see its docstring).
- `sekirorl/snapshot.py`: 8 scalars, one-hot boss animation, one-hot player animation,
  then 10 validity bits. Vocabulary in `sekirorl/data/animations-genichiro.json`, rebuilt
  with `tools/build_vocabulary.py <logs>`. **Rebuilt on 2026-11-27** from the four fights
  of 2026-11-25 and all runs of 2026-11-27 (21,097 samples): 74 boss and 184 player IDs,
  278 values, schema `v2-anim-ae7cf69e`. Nothing has been trained on any layout, so
  nothing was lost. Once training starts the vocabulary has to stay fixed.
- `sekirorl/controller.py`: `MENU_BUTTONS` (d-pad, A/B/X/Y, start, back) for the reset.
- Tests: `test_reset.py`, `test_runner.py`, `test_scripted.py`, `test_snapshot.py`;
  `tests/data/opening-attack-2026-11-25.json` holds the ten logged opening attacks.

Still uncommitted from 2026-11-25: `sekirorl/encounter.py`, the `encounter` argument of
`StepLoop`, `tests/test_encounter.py`, `tests/data/fights-2026-11-25.json.gz`.

Checks run on 2026-11-27: `python.exe -m unittest discover -s tests` on Windows - 172
passed, none skipped (after the last code change). A scratch sweep of 2,304 simulated
walks before the live runs: none failed. The live runs in the table above.

## Known gaps

- **The reset has met the game 11 times**, all in one game session at one idol.
  Assumptions that more runs could still break: that `710320` always means
  the idol menu is open at its top level; that no other animation shows between `710320`
  and `710360`; that a boss kills a player who stands still within 180 s. Ten live resets
  on 2026-11-27 met none of these.
- **The idol menu cannot be read from memory.** A lost d-pad press would put the cursor on
  the entry next to Reflections of Strength, and the script would go on to press A, 2 down,
  A, A there. The only check is afterwards (still sitting, or no boss loaded). The user was
  told to watch the first resets. The menu timings are guesses on the slow side.
- A failed reset is reported, not retried. Add retries after seeing real failures.
- Leaving a fight after a deathblow or the step limit means waiting to be killed; the
  user confirmed there is no other way out.
- Deflect or block is read from the player's reaction animation. That rests on the user's
  screen checks of eight deflects of `3014` and on one block by hand; no other attack has
  been checked against the screen.
- The boss's animation time as read steps back now and then (11 of 521 sample pairs in one
  run). Not looked into; it matters for anything that times a press from it.
- The fix for the early walk (steady for 0.5 s) guards against animations that are not
  known; it has run 5 times, and the `700000` case itself has not come up again.
- The deathblow rule rests on one recorded deathblow. Not seen: a boss whose HP was cut to
  zero by ordinary hits before the deathblow (HP may read 0 rather than 1 there; the refill
  rule would still count it, about 2.6 s late).
- At a deathblow the reward counts the drop to HP 1 as damage (4786 of 9887 in the
  recording), and `FightReward` also counts the posture going to 0. The user was asked
  about the HP part on 2026-11-25 and has not answered.
- `ready` requires the boss at full HP, so a fight that is left half-finished never becomes
  ready again; the reset always leaves and re-enters.
- The vocabulary is from four fights, almost all in the first phase. Unseen IDs fall in the
  unknown slot. Nothing has been trained on any layout yet.
- The player walk-in animation `700010` is hard-coded from six fights.

## Tools and environment

- `tools/log_snapshots.py` and `tools/record_rematch.py`: read-only recorders; usage is in
  their docstrings and in the Stage 3 handoff. `sekirorl.runner` now logs the same rows.
- The repo is edited from WSL but runs under 64-bit Windows Python 3.15 (`python.exe` works
  from WSL). The remote is `origin` on GitHub; its only branch is `master`.
- The game's PID changes on every launch: `tasklist.exe /FI "IMAGENAME eq sekiro.exe"`.
  Cheat Engine is not needed.
- The user's save: player HP 960, posture 360. Boss HP 9887, posture 4083 once active.
- The user follows along in the terminal and likes the steps sent to Discord when they have
  to play (the DM chat used in earlier sessions).

## Open points carried forward

- The 30-action list was presented at G02 and not objected to; treat it as accepted.
- Posture is encoded as bar fill; the user was asked and has not objected.
- The observation now includes the player's own animation as well as the boss's. That
  choice was not put to the user before 2026-11-26.
- Stage 3 was committed and pushed at the user's request (`5458066`, `3af5ef7`, `d9e0ffc`).
  Commit or push again only when asked.
