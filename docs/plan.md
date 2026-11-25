# Sekiro Boss Agent — Engineering Roadmap

Planning baseline: 2026-11-18. This document is the authoritative development order. The
feasibility report and memory pointer list are in [`../Plan.md`](../Plan.md); the reader's
validation procedure is in [`../MEMORY_READER.md`](../MEMORY_READER.md); project rules are in
[`../AGENTS.md`](../AGENTS.md). The state of the task in progress is in
[`handoffs/current.md`](handoffs/current.md).

## Roadmap at a glance

| Stage | Outcome | Gate | Status |
|---|---|---|---|
| 1 | Environment modules: memory, controller, actions, timing, Gymnasium wrapper, fake game | G01 | Completed (approved 2026-11-18) |
| 2 | Loop proven: automated tests, `check_env`, Windows smoke test against the live game | G02 | Completed (approved 2026-11-18) |
| 3 | Memory fields found and verified: player state, boss object, boss state | G03 | At gate G03, waiting for the user |
| 4 | Repeatable encounter: start, death, victory, automatic reset, scripted deflect | G04 | Not started |
| 5 | Demonstrations recorded and a behaviour-cloned policy | G05 | Not started |
| 6 | PPO fine-tuning and evaluation (reduced comparison) | G06 | Not started |

Stages 1–2 are steps 1–2 of the original request. Stages 3–4 together are step 3, split so the
Cheat Engine work has its own gate. Stages 5–6 are steps 4–5.

## A. Objective

Train an agent that beats Genichiro Ashina (the fight at the top of Ashina Castle) in Sekiro
1.06 on Windows, using numbers read from process memory as observations and a virtual
controller for output. The agent is an MLP policy trained by behaviour cloning and then PPO.

## B. Decisions

| ID | Decision |
|---|---|
| D1 — Target | Genichiro Ashina, tower fight, three deathblows. Curriculum: end the episode at the first deathblow, then extend. |
| D2 — Reset | Reflection of Strength rematch menu at a Sculptor's Idol, driven by controller inputs. Needs a save where he is already beaten. Behaviour on death and victory is unconfirmed until S03-T05. |
| D3 — Read-only memory | No writes, injection, freezing or scripts, ever. This includes Cheat Engine use during discovery: value scans, memory viewing and pointer scans only. |
| D4 — Stack | Gymnasium, Stable-Baselines3, PyTorch, `vgamepad`. Numerical observations, no screenshots. |
| D5 — Action space | One flat discrete list: 5 movements × 6 button behaviours = 30 actions. Guard has two behaviours: hold, and re-press (deflect). |
| D6 — Observation layout | Flat vector of scaled values followed by one validity bit per value. A field stays masked as unavailable until its memory route is verified. |
| D7 — Invalid data | A missing or stale read is invalid: never zero HP, never used for reward. Reward is only taken between two consecutive valid samples. |
| D8 — Step rate | 10 per second (100 ms) to start, configurable. |
| D9 — Player death | First HP 0 ends the episode; the reset script chooses "Die". Resurrection is not modelled. |
| D10 — Training scope | Reduced: evaluate the cloned policy, train PPO from the cloned policy, one short PPO-from-scratch run. |

## C. Conventions

### Task format

Each task has an ID (`S<stage>-T<number>`), a checkbox and status, and the fields Purpose,
Prerequisites, Actions, Acceptance, Validation and Risks. Statuses: Not started, In progress,
Blocked, Completed. The sub-tasks are predictions; add, split or drop them as the work shows
what is needed, and record the change in section F.

A check only counts if it actually ran. Syntax checks, automated tests against the fake game,
and checks against the running game are three different things and are always reported
separately. A task whose live check cannot be run is Blocked, not Completed.

### Gates

Every stage ends with a gate. At a gate the agent **stops and does not start the next stage**
until the user approves. The agent gives a status report containing:

1. **Tasks:** each task in the stage with its status, and why any is blocked or dropped.
2. **Checks run:** exact commands and results, split into local automated checks and live-game
   checks.
3. **Not verified:** what was not run or could not be confirmed, stated plainly.
4. **Changes:** files added or changed, and any deviation from this plan.
5. **User checklist:** the items listed under the gate, each with how to check it.
6. **Decisions needed:** anything the user has to choose before the next stage.
7. **Handoff:** confirmation that `handoffs/current.md` is updated.

When the user approves, record the date in section F, move the handoff to `handoffs/done/`
and write a new `handoffs/current.md` for the next stage.

## D. Development roadmap

### Stage 1 — Environment modules

#### S01-T01 — Move the reader into a package without breaking its command line

- [x] **Status: Completed** (tests pass; `--help` and `py_compile` run on Windows, 2026-11-18)
- **Purpose:** Let the environment reuse the reader while the documented commands keep working.
- **Prerequisites:** None.
- **Actions:** Move `Reader` into `sekirorl/memory.py`; keep `read_health.py` as a wrapper.
  Add a chain table marking each chain live or candidate, a single-struct read with
  double resolve, and a state source that fills verified fields only.
- **Acceptance:** `python .\read_health.py --help` and `py_compile` behave as before.
  Unverified chains are never placed in a snapshot.
- **Validation:** Run both commands under Windows Python.
- **Risks:** The reader has only ever been run before this refactor.

#### S01-T02 — Snapshot and observation layout

- [x] **Status: Completed** (automated tests pass on Windows and WSL, 2026-11-18)
- **Purpose:** One raw record per sample and a fixed observation vector built from it.
- **Prerequisites:** None.
- **Actions:** `Snapshot` dataclass where `None` means unavailable; `encode` producing scaled
  values plus validity bits; a schema version string.
- **Acceptance:** Vector length never changes; unavailable is value 0 with validity 0; a real
  HP of 0 is distinguishable from unavailable.
- **Validation:** S02-T02.
- **Risks:** Distance and time scales are guesses until S03.

#### S01-T03 — Controller output

- [x] **Status: Completed** (virtual pad seen acting in the game by the user, 2026-11-18)
- **Purpose:** Send complete pad states, with nothing held outside the last state sent.
- **Prerequisites:** None.
- **Actions:** `ControlState`; a recording controller for tests; a `vgamepad` controller that
  imports the library lazily and releases inputs at interpreter exit.
- **Acceptance:** Applying a state releases everything not named in it.
- **Validation:** S02-T02 for the recording controller; S02-T05 for the real pad.
- **Risks:** The `vgamepad` calls are written from memory of its documentation.

#### S01-T04 — Action set and press/release schedules

- [x] **Status: Completed** (automated tests pass on Windows and WSL, 2026-11-18)
- **Purpose:** Map each discrete action to explicit timed presses and releases.
- **Prerequisites:** S01-T03.
- **Actions:** 30 actions; tap, hold and re-press behaviours; timing validation so repeated
  taps are separate presses.
- **Acceptance:** A tap presses and releases inside one step; consecutive guard-hold steps
  never release; deflect while holding releases then presses again.
- **Validation:** S02-T02.
- **Risks:** Jump and forward-dodge timings needed for Genichiro are unknown until S04.

#### S01-T05 — Fixed-rate step loop

- [x] **Status: Completed** (automated tests pass on Windows and WSL, 2026-11-18) — live timing still to measure in S02-T05
- **Purpose:** Apply the action, wait to the deadline, read, and report timing honestly.
- **Prerequisites:** S01-T02, S01-T04.
- **Actions:** Absolute deadlines with overrun resync; validity checks; reward between
  consecutive valid samples only; truncation only; release on every exit path; optional
  focus check; watchdog thread that releases inputs when steps stop.
- **Acceptance:** D7 holds in every path; inputs are neutral after reset, close, truncation
  and an exception.
- **Validation:** S02-T02.
- **Risks:** Windows sleep granularity; measured in S02-T05.

#### S01-T06 — Gymnasium wrapper

- [x] **Status: Completed** (wrapper test, Gymnasium `check_env` and Stable-Baselines3 `check_env` pass on Windows, 2026-11-18)
- **Purpose:** Expose the loop as a standard environment with fixed spaces.
- **Prerequisites:** S01-T05.
- **Actions:** `SekiroEnv` with a `Box` observation space and a `Discrete` action space.
- **Acceptance:** Passes Stable-Baselines3 `check_env`.
- **Validation:** S02-T03.
- **Risks:** None known.

#### S01-T07 — Fake game

- [x] **Status: Completed** (automated tests pass on Windows and WSL, 2026-11-18)
- **Purpose:** Test the loop instantly and deterministically without the game.
- **Prerequisites:** S01-T02, S01-T03.
- **Actions:** One object acting as state source, controller and clock, with scripted hits,
  loading windows and pauses.
- **Acceptance:** Tests run in simulated time with no real sleeping.
- **Validation:** S02-T02.
- **Risks:** The fake can only prove the loop's logic, not the game's behaviour.

#### Gate G01

Stage 1 has no live-game checks, so G01 is reported together with G02 unless the user asks
for it separately.

**User checks:**
- [ ] The module split matches what you want to test independently.
- [ ] The 30-action list is acceptable (see `sekirorl/actions.py`).
- [ ] The observation fields listed in `sekirorl/snapshot.py` are the ones you expect.

### Stage 2 — Prove the loop

#### S02-T01 — Reader tests against simulated memory

- [x] **Status: Completed** (13 tests pass on Windows and WSL, 2026-11-18)
- **Purpose:** Cover the cases `AGENTS.md` requires.
- **Prerequisites:** S01-T01.
- **Actions:** Tests for null pointer, failed read, partial read, chain changing mid-sample,
  invalid HP pairs, exited process.
- **Acceptance:** Every case yields unavailable with null health, never zero.
- **Validation:** `python -m unittest discover -s tests` on Windows and WSL.
- **Risks:** None known.

#### S02-T02 — Loop tests against the fake game

- [x] **Status: Completed** (32 tests pass on Windows and WSL, 2026-11-18)
- **Purpose:** Prove the step loop's rules.
- **Prerequisites:** Stage 1 tasks.
- **Actions:** Tests for observation shape and masks, reward rules, invalid and paused
  samples, truncation, input edges for each behaviour, release on every exit path, reset
  timeout, gap and overrun handling, lost focus, watchdog.
- **Acceptance:** All pass using the standard library only.
- **Validation:** Same command as S02-T01.
- **Risks:** None known.

#### S02-T03 — Install packages and run `check_env`

- [x] **Status: Completed** (gymnasium 1.4.0, stable-baselines3 2.9.2, torch 2.15.0 CPU build, vgamepad 0.2.0 on Windows Python 3.15; all 47 tests pass with no skips, 2026-11-18)
- **Purpose:** Confirm the wrapper is a valid environment.
- **Prerequisites:** S01-T06; packages installed on Windows.
- **Actions:** Add `requirements.txt`; the user installs `gymnasium`, `stable-baselines3`,
  `torch`, `vgamepad`; add a wrapper test that runs `check_env` on the fake game.
- **Acceptance:** `check_env` passes with no warnings that matter.
- **Validation:** Run the test under Windows Python.
- **Risks:** Windows Python is 3.15; PyTorch may not support it yet, in which case an older
  Python is needed.

#### S02-T04 — Windows smoke test script

- [x] **Status: Completed** (dry run with a stubbed process on Windows; not yet run against the game)
- **Purpose:** One command that proves reads and controller output against the real game.
- **Prerequisites:** S01 tasks.
- **Actions:** Print Python bitness, executable path and SHA256, module base and foreground
  window; run no-op steps; send one action with press and release times; optionally poll the
  candidate player animation ID to measure press-to-animation delay; confirm neutral at exit.
- **Acceptance:** Runs to completion and releases all inputs, including on Ctrl+C.
- **Validation:** Dry run with the recording controller locally; live run in S02-T05.
- **Risks:** The animation ID route is an unverified candidate, so that figure is provisional.

#### S02-T05 — Live smoke run

- [x] **Status: Completed** (2026-11-18: user saw both attacks; guard held in game and released when the Python process was killed)
- **Purpose:** First end-to-end proof.
- **Prerequisites:** S02-T03, S02-T04; ViGEmBus driver installed.
- **Actions:** Run the smoke test with a save loaded; record step lateness and the
  press-to-animation delay; hold guard and kill the Python process to see whether the pad
  releases.
- **Acceptance:** The character performs the action; HP reads match the screen; timing
  figures are recorded.
- **Validation:** Observed in game by the user.
- **Risks:** Sekiro may ignore the virtual pad, need Steam Input disabled, or need focus.

#### S02-T06 — Update project documentation

- [x] **Status: Completed** (2026-11-18)
- **Purpose:** Keep `AGENTS.md` true.
- **Prerequisites:** S02-T01 to S02-T04.
- **Actions:** Update the structure and commands sections of `AGENTS.md`.
- **Acceptance:** A new session can run every check from the document alone.
- **Validation:** Read-through.
- **Risks:** None.

#### Gate G02

**User checks:**
- [x] Automated tests pass on your Windows Python.
- [x] `check_env` passes.
- [x] In game, the smoke test's action visibly happens once and nothing stays held afterwards.
- [ ] The HP printed matches the health bar.
- [x] Killing the script while guard is held releases guard.
- [ ] Step lateness and press-to-animation delay are acceptable for a 100 ms step.

### Stage 3 — Memory fields

All work here uses Cheat Engine read-only (D3). Pointer details and the back-walk method are
in `../Plan.md` section 4. Record each result in `MEMORY_READER.md` with executable version,
SHA256 and mods.

#### S03-T01 — Discovery setup

- [x] **Status: Completed** (2026-11-20; list in `MEMORY_READER.md`)
- **Purpose:** A safe, repeatable Cheat Engine session.
- **Prerequisites:** G02; the Cheat Engine connection from the user.
- **Actions:** Confirm which operations the connection exposes and that none used here write
  memory; record executable version and hash.
- **Acceptance:** A written list of allowed operations.
- **Validation:** Read player HP through the connection and match the Python reader.
- **Risks:** The connection may expose write or script operations; they stay unused.

#### S03-T02 — Finish validating player HP

- [x] **Status: Completed** (2026-11-25: death, resurrection, rest and loading screens seen; resolves on four launches, three of them restarts in a row)
- **Purpose:** Close the gaps left by the first session log.
- **Prerequisites:** S03-T01.
- **Actions:** Observe HP at death, after resurrection and rest, across area reloads and
  three game restarts.
- **Acceptance:** Death reads as HP 0 (or the actual behaviour is documented); the chain
  resolves after every restart.
- **Validation:** Live, per `MEMORY_READER.md`.
- **Risks:** Death may read as unavailable, which changes how S04-T02 detects it.

#### S03-T03 — Player posture, position and animation

- [x] **Status: Completed** (2026-11-25: consistent over four launches and six fights; compared with the user's actions on 2026-11-20. Posture counts down)
- **Purpose:** Promote the player candidates to verified.
- **Prerequisites:** S03-T02.
- **Actions:** Check each candidate chain against behaviour: posture direction and range,
  position changing with movement, animation ID and elapsed time per move.
- **Acceptance:** Each field matches what is seen on screen across a restart.
- **Validation:** Live.
- **Risks:** Posture may count down rather than up.

#### S03-T04 — In-game time and death counter

- [x] **Status: Completed** (2026-11-25: the death counter moves in the rematch when the death is final; in-game time does not stop in the pause menu, so the animation ring is the liveness signal)
- **Purpose:** A liveness signal and an independent death signal.
- **Prerequisites:** S03-T01.
- **Actions:** Check that in-game time advances in play and stops when paused; check whether
  the death counter increments, including inside a rematch.
- **Acceptance:** Behaviour documented for normal play and the rematch.
- **Validation:** Live.
- **Risks:** The counter may not move inside a rematch.

#### S03-T05 — Confirm the rematch in game

- [ ] **Status: In progress** (user task; 2026-11-25 logs show a death returns the player to the idol at full HP; menu path, entry name, gourds and victory still to be noted by the user)
- **Purpose:** Confirm D2 before building on it.
- **Prerequisites:** A save with Genichiro beaten.
- **Actions:** Start the rematch; note the menu path and button count, which entry is the
  three-deathblow fight, where the player lands after death and after victory, whether
  gourds are full, and whether the death prompt waits.
- **Acceptance:** A written description of the reset sequence.
- **Validation:** Observed by the user.
- **Risks:** If the rematch does not behave as assumed, D2 needs a new decision.

#### S03-T06 — Find the boss character object

- [x] **Status: Completed** (2026-11-25: found in the character collection without a value scan; NPC ID `71001000` and entity ID `1110800` on three attempts; HP, posture, position and animation follow the fight. A deathblow has not been seen: that is S04-T01)
- **Purpose:** Get the boss object's address and confirm the field layout.
- **Prerequisites:** S03-T03, S03-T05.
- **Actions:** Value-scan boss HP, walk back to the character object, then verify HP,
  posture, position, animation ID, elapsed time and NPC ID against the fight.
- **Acceptance:** NPC ID is the same on three retries; animation ID changes per attack.
- **Validation:** Live.
- **Risks:** Layout may differ from the player's.

#### S03-T07 — Stable route to the boss

- [x] **Status: Completed** (2026-11-25: collection walk with an entity ID and NPC ID check found the boss on six attempts over four launches, three of them restarts in a row, and across a deathblow)
- **Purpose:** Reach the boss from a static root without injection.
- **Prerequisites:** S03-T06.
- **Actions:** Try, in order: the character collection in WorldChrMan filtered by NPC ID, the
  lock-on manager, a pointer scan. Test across retries, reloads and restarts.
- **Acceptance:** The route finds the right character after every retry and three restarts.
- **Validation:** Live.
- **Risks:** This is the task most likely to block the project.

#### S03-T08 — Add verified fields to the reader

- [x] **Status: Completed** (2026-11-25: reader, snapshot, loop check and tests; 81 tests pass; four fights read live through the new code; all field groups set to `live`. The default `read()` and the step loop were not run against the game after the promotion)
- **Purpose:** Turn findings into observations.
- **Prerequisites:** S03-T03, S03-T04, S03-T07.
- **Actions:** Mark verified chains live, fill the snapshot, add an identity check on every
  read, extend the tests.
- **Acceptance:** Masks for verified fields turn on; an identity mismatch gives unavailable.
- **Validation:** Automated tests plus a live read during a fight.
- **Risks:** None beyond the earlier tasks.

#### Gate G03

**User checks:**
- [ ] Every field marked live has a recorded live validation in `MEMORY_READER.md`.
- [ ] Watching a logged fight, boss HP, posture and distance follow what is on screen.
- [ ] The boss route survived three game restarts.
- [ ] Anything still unavailable is listed, with what it blocks.

### Stage 4 — Repeatable encounter

#### S04-T01 — Deathblow and victory signal

- [ ] **Status: Not started**
- **Purpose:** Know when a health bar is broken and when the fight is won.
- **Prerequisites:** G03.
- **Actions:** Find a deathblow count or derive it from boss HP refills; define victory for
  the current curriculum stage; never read a refill as negative damage.
- **Acceptance:** Correct over ten fights with no false victory.
- **Validation:** Live.
- **Risks:** The boss-defeated flag is probably already set in a rematch save.

#### S04-T02 — Player death and episode termination

- [ ] **Status: Not started**
- **Purpose:** Turn on `terminated`.
- **Prerequisites:** S03-T02, S04-T01.
- **Actions:** Detect death; add termination on death and on victory; keep the step limit as
  truncation.
- **Acceptance:** Death is never confused with a loading screen.
- **Validation:** Automated tests plus live fights.
- **Risks:** Depends on what S03-T02 found.

#### S04-T03 — Episode start detection

- [ ] **Status: Not started**
- **Purpose:** Start stepping only when the fight is really running.
- **Prerequisites:** S03-T08.
- **Actions:** Require the player loaded, the boss identified at full HP, and in-game time
  advancing.
- **Acceptance:** No step is taken during loading or the intro.
- **Validation:** Live.
- **Risks:** Intro length may vary.

#### S04-T04 — Automatic reset

- [ ] **Status: Not started**
- **Purpose:** Restart the fight without a human.
- **Prerequisites:** S03-T05, S04-T02, S04-T03.
- **Actions:** Script the menu inputs for death, victory and step-limit endings; detect and
  report a failed reset.
- **Acceptance:** 20 consecutive automatic resets with the right boss every time.
- **Validation:** Live.
- **Risks:** Menu cursor position may not be stable.

#### S04-T05 — Log fights and build the animation vocabulary

- [ ] **Status: Not started**
- **Purpose:** Make animation IDs usable by the policy.
- **Prerequisites:** S03-T08.
- **Actions:** Log raw snapshots for several fights as UTF-8; build the ID vocabulary with an
  unknown bucket; release observation schema v2.
- **Acceptance:** Every animation seen in the logs maps to a slot.
- **Validation:** Automated tests on the logs.
- **Risks:** Rare attacks may be missing from the first logs.

#### S04-T06 — Scripted deflect

- [ ] **Status: Not started**
- **Purpose:** Prove the timing budget before any learning.
- **Prerequisites:** S04-T05.
- **Actions:** A hand-written policy that deflects one chosen attack from its animation ID
  and elapsed time.
- **Acceptance:** Deflects that attack reliably.
- **Validation:** Live.
- **Risks:** If this fails at 100 ms, raise the step rate or cut latency first.

#### S04-T07 — Unattended-run safety

- [ ] **Status: Not started**
- **Purpose:** Hours of virtual input without accidents.
- **Prerequisites:** S04-T04.
- **Actions:** Foreground-window check, keep-awake, reattach after a game restart, and a
  random-policy soak.
- **Acceptance:** Alt-tabbing and closing the game mid-run both end with inputs released; a
  one-hour soak has no reset failure.
- **Validation:** Live.
- **Risks:** Inputs reaching another window.

#### Gate G04

**User checks:**
- [ ] 20 automatic resets in a row, watched or logged, with no wrong boss.
- [ ] Death and victory are reported correctly in the log.
- [ ] The scripted deflect works.
- [ ] Alt-tab and closing the game leave nothing held.
- [ ] You are comfortable leaving it running unattended.

### Stage 5 — Demonstrations and behaviour cloning

#### S05-T01 — Physical pad pass-through

- [ ] **Status: Not started**
- **Purpose:** Let the human play through the agent's own action wrapper.
- **Prerequisites:** G04.
- **Actions:** Read the physical pad, map it to the action set, send it through the virtual
  pad; hide the physical pad from the game or identify the slots.
- **Acceptance:** The fight is playable through the wrapper.
- **Validation:** Live, by the user.
- **Risks:** The game may bind the wrong pad; added input delay.

#### S05-T02 — Recorder

- [ ] **Status: Not started**
- **Purpose:** Data that survives later changes to features and step rate.
- **Prerequisites:** S05-T01.
- **Actions:** Record raw snapshots, raw pad state and the wrapper's action with timestamps
  at 60 Hz or more.
- **Acceptance:** A recording can be re-encoded into training pairs offline.
- **Validation:** Automated tests on a short recording.
- **Risks:** Recording overhead affecting step timing.

#### S05-T03 — Representability check

- [ ] **Status: Not started**
- **Purpose:** Find out early whether the action set is enough.
- **Prerequisites:** S05-T02.
- **Actions:** Record five minutes; measure the share of human input the action set cannot
  express; adjust the set if needed.
- **Acceptance:** The share is small enough to proceed, or the action set is revised.
- **Validation:** Offline analysis.
- **Risks:** Changing the action set changes D5.

#### S05-T04 — Collect the dataset

- [ ] **Status: Not started** (user task)
- **Purpose:** Enough winning and near-winning fights to clone.
- **Prerequisites:** S05-T03.
- **Actions:** Play locked on, using only the agent's actions.
- **Acceptance:** A dataset size agreed at the time, split by fight for validation.
- **Validation:** Dataset summary.
- **Risks:** Human time.

#### S05-T05 — Behaviour cloning

- [ ] **Status: Not started**
- **Purpose:** A policy that loads straight into PPO.
- **Prerequisites:** S05-T04.
- **Actions:** Hand-written supervised loop on the Stable-Baselines3 policy; class weighting
  or oversampling around presses; labels shifted by the agent's actuation delay; save weights.
- **Acceptance:** Per-class recall and press-timing error reported on held-out fights.
- **Validation:** Offline, then live in S05-T06.
- **Risks:** High offline scores with poor live play.

#### S05-T06 — Evaluate and correct

- [ ] **Status: Not started**
- **Purpose:** Measure the cloned policy and fix its worst mistakes.
- **Prerequisites:** S05-T05.
- **Actions:** Run evaluation fights; do takeover-correction rounds where the human takes
  over when the agent errs; retrain.
- **Acceptance:** Fraction of boss health removed per fight is reported before and after.
- **Validation:** Live.
- **Risks:** Human time.

#### Gate G05

**User checks:**
- [ ] The wrapper feels playable to you.
- [ ] The unrepresentable share from S05-T03 is acceptable.
- [ ] Watching the cloned policy, it blocks and attacks at sensible moments.
- [ ] The evaluation numbers are recorded with how many fights they cover.

### Stage 6 — PPO fine-tuning and evaluation

#### S06-T01 — Training harness

- [ ] **Status: Not started**
- **Purpose:** Train in real time without corrupting episodes.
- **Prerequisites:** G05.
- **Actions:** Update at episode boundaries or with the game paused; log win rate, fight
  duration, boss health removed, reset failures, invalid-step rate and steps per second;
  stop the run if the invalid-step rate is too high.
- **Acceptance:** A short run completes with clean logs.
- **Validation:** Live.
- **Risks:** Idle time during updates being blamed on the policy.

#### S06-T02 — Critic warm-up

- [ ] **Status: Not started**
- **Purpose:** Stop PPO wrecking the cloned policy in its first updates.
- **Prerequisites:** S06-T01.
- **Actions:** Run the cloned policy with the actor frozen and fit only the value head.
- **Acceptance:** Value loss settles before the actor is unfrozen.
- **Validation:** Training logs.
- **Risks:** Costs fight time with no policy improvement.

#### S06-T03 — PPO from the cloned policy

- [ ] **Status: Not started**
- **Purpose:** The main training run.
- **Prerequisites:** S06-T02.
- **Actions:** Low learning rate, small clip range; reward from normalised HP changes,
  deathblows and confirmed outcomes; two runs if time allows.
- **Acceptance:** Performance does not fall below the cloned policy; ideally it wins.
- **Validation:** Live evaluation fights.
- **Risks:** Collapse early in fine-tuning; real-time cost.

#### S06-T04 — Short PPO-from-scratch run

- [ ] **Status: Not started**
- **Purpose:** A reference point only (D10).
- **Prerequisites:** S06-T01.
- **Actions:** One short run with the same environment settings.
- **Acceptance:** Result recorded, with its budget stated.
- **Validation:** Training logs.
- **Risks:** Not a fair comparison, and reported as such.

#### S06-T05 — Final evaluation

- [ ] **Status: Not started**
- **Purpose:** State what was achieved and how sure we are.
- **Prerequisites:** S06-T03, S06-T04.
- **Actions:** Evaluate each policy in interleaved fights; report boss health removed, win
  rate with its uncertainty, and fight counts.
- **Acceptance:** A written result that does not claim more than the fight counts support.
- **Validation:** Live.
- **Risks:** Too few fights to separate the policies.

#### Gate G06

**User checks:**
- [ ] You have watched the final policy fight.
- [ ] The report states the number of fights behind every figure.
- [ ] Decide what comes next: extend the curriculum, more training, or stop.

## E. Before any training run

1. Boss identity, animation ID, death and victory signals validated across three restarts (G03, G04).
2. Measured latency and jitter, and the scripted deflect passing (G02, S04-T06).
3. Watchdog and focus checks tested live, and a soak with no reset failures (S04-T07).

## F. Progress and gate log

| Date | Item | Note |
|---|---|---|
| 2026-11-18 | Plan | Feasibility report written (`../Plan.md`); approach reviewed by four reviewers; D1–D10 agreed. |
| 2026-11-18 | Stage 1 | Modules written and syntax-checked. No test suite run. Paused at the user's request. |
| 2026-11-18 | S01-T06 | Gymnasium 1.4.0 and numpy 2.5.5 installed on Windows Python 3.15. Wrapper test and Gymnasium `check_env` pass; 46 pass, 1 skipped (Stable-Baselines3). |
| 2026-11-18 | S02-T03 | All packages installed on Windows. 47 tests pass, none skipped. Virtual pad connects, so the ViGEmBus driver is present. PyTorch is the CPU build. |
| 2026-11-18 | S02-T05 | First live run by the user. Executable SHA256 `637aca527538c0ec6e1f136c8ed66046e95dfbdbb1f51926e134d9916398b856`, base `0x140000000`. Player HP 320/320 read through the refactored reader. Dry run without focus: every step invalid and truncated after 5, as designed. Focused run: 31 steps at 100 ms, lateness max 0.01 ms, read about 0.1 ms, no overruns. Attack sent as press at +0.4 ms and release at +50.1 ms. Press-to-animation delay 56.9 ms (animation ID 790010 → 790040, candidate chain). Candidate reads all resolved with plausible values: posture 120/120, position (160.3, −28.9, 22.4), animation 790010 at 3.10 s, in-game time 9,763,418 ms, death count 5. None verified against behaviour yet. |
| 2026-11-18 | S02-T05 | User confirmed: the character attacked twice (stepped run and delay measurement); `--hold-guard` made the character guard; killing the Python process with `taskkill /F` released guard. Sekiro accepts the virtual pad. |
| 2026-11-18 | Stages 1–2 | Resumed. 47 tests: 45 pass, 2 skipped (need Gymnasium / Stable-Baselines3), on Windows Python 3.15 and WSL Python 3.14. Smoke test dry-run on Windows with a stubbed process. Nothing run against the game. Stopped at G02 for the user. |
| 2026-11-20 | Stage 3 | Started; paused by the user before the rematch. S03-T01 completed: Cheat Engine connection checked, allowed operations listed, HP matched the Python reader (320/320). Live, one session, no restart: player HP (0 at death), posture (counts down from 120), position and the death counter behave as expected. In-game time does not stop in the pause menu. The animation candidate was wrong; the current animation is read from a ring in the TimeAct module, which also serves as a pause check. The character collection inside WorldChrMan was mapped; the NPC ID candidate `30, 628` is wrong and character `+0x68` is used instead. Boss not yet examined. No code changed; automated tests not re-run. Details in `MEMORY_READER.md`. |
| 2026-11-25 | Stage 3 | Second launch of the game (PID 28268, same executable hash, a different save). Three rematch fights recorded read-only; no deathblow, no victory, no restart yet. Boss found in the character collection: NPC ID `71001000`, map entity ID `1110800` at character `+0x1A24`, block 2 entry 261 each time, at a new heap address each time. A dormant copy shares the NPC ID, so identity is entity ID plus NPC ID. Boss HP fell in steps of 340 and never rose; boss posture counts down. The death counter moves in the rematch. S03-T04 and S03-T06 completed. Deviations: Cheat Engine and value scans were not needed; the reader's chain table was replaced by layout constants and a status table; observation schema is now `v1.1-scalars` (posture encoded as bar fill); the loop treats a repeated frame token as an invalid step. Local checks: `python.exe -m unittest discover -s tests` 81 pass, none skipped (Windows); `python3 -m unittest discover -s tests` 81 run, 2 skipped (WSL, no Gymnasium). Live check: 2188 samples of one fight through `MemoryStateSource` with the boss found on every one. |
| 2026-11-25 | Stage 3 | Three restarts done (PIDs 29772, 17992, 6976), one fight each, all read through `MemoryStateSource`: the boss was found on every sample (5801 over four fights), at block 2 entry 261 each time. One deathblow seen: boss HP is set to 1, then refilled to the maximum 2.6 s later on the same object. The user compared player HP and the boss health bar with the screen. All field groups set to `live`. S03-T02, T03, T07 and T08 completed; S03-T05 still needs the user's notes. Local checks after the promotion: 81 tests pass on Windows with none skipped; 81 run on WSL with 2 skipped. Stopped at G03. |

| Gate | Approved by user | Note |
|---|---|---|
| G01 | 2026-11-18 | Reported and approved together with G02. |
| G02 | 2026-11-18 | Approved after the live smoke run. The 30-action list was presented and not objected to. HP was confirmed at full health only in this run. |
| G03 | — | |
| G04 | — | |
| G05 | — | |
| G06 | — | |
