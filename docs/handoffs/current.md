# Handoff: record the user's play (Stage 5), after the first PPO run from scratch

Written: 2026-11-27, late evening. Status: **The PPO run from scratch is over and showed no
learning. The user's own play is recorded: 17 fights, 12 wins, 54 minutes of fighting. A
behaviour-cloned model (`models/clone-1/`) exists and has not played yet. The action set
grew from 30 to 90 and the observation changed (gourds, fine animation time, new
vocabulary). Everything up to here is committed and pushed (2026-11-27).**

How this folder works: `current.md` describes the task in progress. When a gate is approved,
move this file to `done/` (named `YYYY-MM-DD-short-title.md`) and write a new `current.md`.
The previous handoff, `done/2026-11-27-stage-4-repeatable-encounter.md`, has every live
result of Stage 4, the user's idol menu notes, and the known gaps of the reset.

## Read first

1. `AGENTS.md` - project rules, the read-only memory contract, all commands.
2. `docs/plan.md` - decisions, stages, gates, the log in section F.
3. `MEMORY_READER.md`, the sections dated 2026-11-27 - death and respawn animations, the
   deflect and block reading, the lock-on address.

## What the user decided on 2026-11-27

- **G04 approved** ("Yes write the script"), with the one-hour soak waived and with these
  never seen live: a victory, the second deathblow and what follows it, a step-limit ending.
- Episodes run through **both health bars of the first Genichiro** (`--deathblows 2`).
  The third bar is another character (entity `1110801`) that the reader does not find.
- **Reward** (`loop.FightReward`), per full bar: boss posture damage +2.0, boss HP +1.0,
  player HP -1.0, player posture damage -0.25. The user set the order; the numbers are the
  agent's.
- **Lock-on** on the boss in every fight, kept by `reset.LockKeeper`.
- After the PPO run: **record the user's play next**, and **add actions**: sprint, heal,
  charged thrust, combat art. **Read the gourd count from memory** instead of counting
  drinks. The user also asked for the Mikiri counter; it needs no new action (dodge toward
  a thrust is `forward+dodge`) and the user was told so.
- Raised earlier and still open: a **penalty for button spamming**. Nothing is written.

## The PPO run from scratch (`models/run-3h/`, plan task S06-T04)

Three starts into one folder; `episodes.jsonl` has all of them.

| Start | Fights | Steps at the end | Why it stopped |
|---|---|---|---|
| 1 | 16 | 4,881 | The user turned the screen off |
| 2 | 1 | 4,923 | Another window was in front |
| 3 | 88 | 35,478 | The game window lost the front after 2 hours |

- 105 fights, all lost, no deathblow. 16 invalid steps, all in the three fights that were
  cut by the focus loss. Every reset worked without a retry (24 to 45 s).
- **No learning.** In groups of about 17 fights: length 30, 37, 38, 35, 36, 29 s; boss HP
  left 96, 93, 95, 95, 94, 96%. Best single fight: 67% left.
- Lock-on: 183 presses in 105 fights, 567 steps not locked of 35,478. The longest fight
  was 764 steps.
- `winutil.keep_awake()` held for the 2 hours of the third start.
- **This model has 30 outputs and cannot be loaded now that there are 50 actions.**
- Summary script: `python.exe logs/scratch/summarise_run.py models/run-3h`.

## What was written after the run

- **Action set, 50 actions** (`sekirorl/actions.py`): four button modes added after the
  first six, so actions 0 to 29 keep their numbers. `sprint` (dodge held), `heal`
  (`use_item`, D-pad up, new in `controller.BUTTONS`), `thrust` (attack held),
  `combat_art` (guard and attack tapped together; a mode may now name several buttons).
  **None of the four has been sent to the game.** Whether the user's bindings are B and
  D-pad up, and which combat art is equipped, were asked and not answered yet.
- **`sekirorl/pad.py`**: `XInputPads.read(slot)` with `XInputGetState` only.
- **`sekirorl/demos.py`**:
  - `record`: pad state on every change (polled 125 times a second, all four slots),
    a snapshot 62.5 times a second, raw values only, to `demos/` (ignored by git).
  - `fights()`: cuts a recording the way `StepLoop` does (same start rule, same validity
    rule through the new `loop.Liveness`, same endings, same reward).
  - `Labeller`: the nearest action for each step from what the pad did between that
    step's start and the next, with flags for what the action leaves out (`diagonal`,
    `lost_press`, `outside`, `held_<button>`, `two_held`).
  - `report`: fights, label counts and the share of flagged steps (plan task S05-T03).
- `logs/scratch/find_gourds.py` (scratch, ignored by git): value scan and pointer search
  for the gourd count, reads only. Its self-test found the known HP route.

## Recording session of 2026-11-27 (game PID 16608, controller on)

- **The user's controller is a DualSense on USB.** XInput does not show it (Steam
  translates it inside the game only). `pad.DualSensePad` reads its HID input reports,
  opened for reading only, and maps them to the Xbox layout; the recorder sees it as slot
  4. The user pressed L1, R1, circle, cross, D-pad up, R3 and pushed the stick forward,
  and all seven came through in order. The Bluetooth report shapes are written from
  documentation and untested.
- **Gourd charges found in memory** (`MEMORY_READER.md`, last section): the Healing Gourd
  entry of the item list, found by item ID `0x40000BB8`. 9 to 3 over six drinks the user
  counted. In the recording every fight starts at 9 and falls as the user heals.
  `Snapshot.gourds`, `memory.STATUS["gourds"] = "live"` on this one session.
- **Lock-on reads right in this second session**: 0.6% of the user's fight steps read as
  not locked.
- **Recordings** (`demos/`, ignored by git):
  - `user-2026-11-27-a.jsonl`: 2 minutes, stopped at the user's request before a fight.
  - `user-2026-11-27-b.jsonl`: 60 minutes, **14 fights, 10 victories through both
    deathblows**, 26,787 steps (45 minutes of fighting), 0 late polls. The last fight was
    cut by the end of the recording.
  - `user-2026-11-27-c.jsonl`: 11.6 minutes, stopped when the user said they were done:
    3 fights, 2 victories, 5,786 steps.
  - Together: **17 fights, 12 victories, 32,573 steps.**
  - The "pad states missed" figure in a report is meaningless for the DualSense: its
    sticks jitter by one unit faster than the recorder polls. The shortest button presses
    in the recording are about 0.08 s, ten polls long.
  - `agent-check.jsonl`: the agent's own play, kept as the recorder's live check.
- **First live victories.** The offline cut counted both deathblows and ended those fights
  as `victory`, so the deathblow rules of `encounter.py` hold on ten real wins. The live
  loop has still not ended a fight that way.
- **With 50 actions the labels left out 15.8% of steps, all of it diagonal movement**
  (plus one press outside the action set). **The agent added the four diagonals on its
  own call** (9 movements times 10 button modes = 90 actions; the numbering of all
  actions changed). With them, one step in each recording is left flagged. The user has
  been told and can reverse it.
- Buttons in recording b: guard held 47%, nothing 36%, deflect 8%, attack 6%, dodge 2%,
  thrust 0.7%, jump 0.5%, sprint 0.3%, heal 0.1%, combat art 12 steps. Movement: forward
  51%, none 22%, right 9%, forward-right 8.5%, back-right 5%, the rest under 2% each.
- The user said Ichimonji can be charged or pressed once. In the recordings it was used
  14 times, always as a tap of about 0.1 s, so no held version was added.
- How long the user holds a tap: attack, dodge and jump about 0.09 s (the agent's tap is
  0.05 s). 19 attack presses and 12 dodge presses were held 0.3 s or more.

## Observation, changed on 2026-11-27 (schema `v3-gourds-b550a3c1`, 387 values)

- Added: `gourds` (charges / 10), and the two animation times a second time on a 1 s
  scale (`*_anim_elapsed_fine`). Reason for the second: in a cloning test on simulated
  attacks the network learned which attack it was at once and the moment inside it much
  later, because a tenth of a second is 0.02 on the 5 s scale. Not compared on real play.
- The animation vocabulary was rebuilt with all recordings: 99 boss and 262 player IDs.
  Command: `python.exe tools/build_vocabulary.py "logs/2026-11-25/*live*.jsonl.gz"
  "logs/*/run-*.jsonl" "demos/*.jsonl"`. (The pattern `*-live*` in the tool's own text
  misses `live-03.jsonl.gz`.) **Do not rebuild it again while `models/clone-1/` or
  anything trained from it is in use.**

## Behaviour cloning (`sekirorl/clone.py`, plan task S05-T05)

- `python.exe -m sekirorl.clone demos/<files> --name <run>`: cross-entropy on the action
  head with rare actions weighted up, value head fitted to the discounted reward of the
  recording, every fifth fight held out, each recording cut at four shifts of the step
  grid. Saves `models/<run>/last.zip` (loadable by `train.py --resume`) and `clone.json`.
- `python.exe -m sekirorl.runner --pid <pid> --send --auto-reset --policy model --model
  models/<run>/last.zip --episodes 5` lets a saved model play; `--sample` draws actions
  as training does instead of taking the most likely one. **Never run on the game.**
- **`models/clone-1/`**: recordings b and c, 90 actions, 20 epochs, 14 fights to learn
  from and 3 held out. On the held-out fights the most likely action matches the label
  in 47.5% of steps, the buttons in 73.2%, the movement in 64.5%. Found by the copy:
  guard held 86%, nothing 70%, deflect 45%, attack 34%, dodge 60%, jump 81%, thrust 89%,
  heal 31%. A press one step early or late counts as a miss in these numbers, and the
  high numbers for holds partly come from the player's own animation being in the
  observation. **This says nothing yet about how it plays.**
- `models/clone-trial-1/` is an earlier trial with 50 actions and an older vocabulary;
  it cannot be loaded any more.

## Checks

- `python.exe -m unittest discover -s tests` on Windows: 278 passed, none skipped.
  `tests/test_demos.py` includes a round trip: random actions sent through `StepLoop`
  are read back by the labeller as the same input. `tests/test_clone.py` clones a
  simulated recording and plays it back through the runner's model policy.
- Live, with the trainer running: `python.exe -m sekirorl.demos pads` saw the virtual pad
  in slot 0 with the agent's presses. A 4-minute `record` of the agent's own play gave
  15,000 snapshots, 0 late polls, 2 missed pad states; `report` cut the same fight the
  trainer logged (241 steps, reward -0.991, boss HP left 0.957).
- A bug in the simulation was found and fixed on the way: `FakeRematch` counted
  deathblows across fights, so a fight after one with a single deathblow went to the
  final cutscene one deathblow early.

## Next step, in order

1. Still open with the user: the diagonals (added, can be reversed), the button-spam
   penalty.
2. To watch the copy play, the game has to be restarted with the controller **off** (the
   virtual pad is ignored otherwise). Then the runner command above, first with five
   fights while the user watches. This is also the first live use of sprint, heal, thrust
   and combat art, and the first check of the gourd and lock-on readings in a third
   session. Use `--model models/clone-1/last.zip`; try it with and without `--sample`.
3. Then PPO from the copy: `python.exe -m sekirorl.train --pid <pid> --hours 3 --resume
   models/<clone>/last.zip --name <run>`. Known risk: PPO can undo a cloned policy in its
   first batches (plan S06-T03); compare fights before and after.

## How training works (`sekirorl/train.py`)

- SB3 `PPO("MlpPolicy")`, two hidden layers of 128, CPU. Settings in `train.DEFAULTS`:
  2048 steps a batch, batch size 256, 10 epochs, gamma 0.995, entropy 0.01, learning
  rate 3e-4. None of these has been tuned.
- Observation: `snapshot.encode` (see the section above). **The animation vocabulary must
  not be rebuilt while a model is in use**; a rebuild changes the layout.
- PPO cannot act while it learns. The pause is 0.30 s for a batch of 2048; the loop takes
  up to 1 s as one late step (`train.PAUSE_S`). No fight was cut by a pause in any run.
- The game window has to stay in front for the whole run. Screen off, lock, or any other
  window in front stops the run (model saved, inputs released).
- `reset.RetryingReset` tries a failed reset three times, not when the window is not in
  front. **The B back-out of an open idol menu has never run on the game.**
- Files per run: `models/<run>/last.zip` after every batch, `step_<n>.zip` every fifth
  batch, `episodes.jsonl`. `models/` is ignored by git.

## Known gaps

- Never seen live by the step loop: a victory, a step-limit ending, the four new
  actions, the B back-out, a model playing.
- The labeller's rules are the agent's own guesses at what matters: a hold of 0.3 s or
  more is a sprint or a thrust, a stick under 0.35 is "stay", more than 22.5 degrees off
  a direction is flagged. They have not been looked at against real play.
- A tap of a button that a hold mode already has down (attack right after thrust, dodge
  right after sprint) sends no new press.
- The gourd route and the DualSense reader come from one game session.
- The copy is scored step by step against the labels; there is no measure yet that
  forgives a press one step off, and none of how it plays.
- Why the lock drops in fights and why re-locking takes several presses is not known;
  the trainer logs no per-step rows.
- Running with the screen off is untested (idea: borderless window mode).
- The lock-on address and the death and respawn animation IDs come from one game session.
- The boss's animation time as read steps back now and then; not looked into.

## Open points carried forward

- Commit regularly and push to `master` without being asked (the user's request of
  2026-11-27; `docs/plan.md` section C). The work of Stage 4, the PPO run and Stage 5 so
  far went in as two commits on 2026-11-27.
- Posture is encoded as bar fill; the player's own animation is in the observation. Both
  were put to the user and not objected to.
