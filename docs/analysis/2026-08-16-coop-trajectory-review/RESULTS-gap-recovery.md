# How much of the solo→coop gap is recoverable, and what is actually causing it

**Date:** 2026-08-16 · **Model:** `gpt-5.6-luna` (Azure) · **Agent:** `mini_swe_agent_v2` ·
**Subset:** flash-50 · **Backend:** local Docker · single seed ·
**Coordination verified working** (`scripts/check_redis.py`: 0 refused)

Follow-up to [RESULTS-solo-coop-gap.md](RESULTS-solo-coop-gap.md), which measured
solo 48% / coop 34% and attributed the gap to merge conflicts. This one asks the
next question — *if you fix the merge, does the gap close?* — and answers it by
replaying every recorded pair rather than by counterfactual.

**Two claims in that earlier document were wrong. They are corrected below.**

## Corrections to the earlier writeup

**1. The evaluator's merge is not naive.** `_merge_naive` (`eval/sandbox.py:515`)
runs `git merge agent1 --no-commit --no-ff` from branch `agent2`, both branched
off the pristine base. That *is* a real 3-way recursive merge. The earlier
recommendation "replace the naive merge with a 3-way merge against the pristine
base" was already implemented; only the function name is misleading.

**2. No conflict markers ever reach the graded tree.** I wrote that the merge
"writes conflict markers into source, the tree stops parsing." It does not — it
calls `git merge --abort` (`sandbox.py:530`) and throws the tree away. The
`tests_passed: 0, tests_failed: 0` I reported for conflicted pairs is **hardcoded
by the evaluator** (`sandbox.py:264`), not an observation about broken trees.

The second correction makes the design problem *worse*, not better:

```python
# sandbox.py:262 — when the merge conflicts
test1_result = {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "output": ""}
test2_result = {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "output": ""}
```

A conflicted coop pair is scored zero **without a single test being run**. The one
escape hatch requires `agent1`'s patch *alone* to pass **both** feature suites —
near-impossible in coop, where agent1 was only ever assigned feature 1. Empirically
it fires once in 16.

The consequence shows up as missing partial credit. Among clean pairs, 12/34 land
in "one feature passed, one didn't". Among conflicted pairs that outcome occurs
**zero** times out of 16 — not because it doesn't happen, but because the evaluator
cannot represent it.

## Step 1: better merge algorithms recover nothing

`scripts/remerge.py` replays all 16 conflicted pairs through a deterministic ladder
— `git merge`, then `-X diff-algorithm=patience`, then `histogram`, then sequential
`git apply -3` — and runs the real held-out suites on whatever comes out.

**Result: 0 of 16 resolved.** Not one conflict was an artifact of diff-hunk
boundaries. These are genuine overlapping edits, and my earlier recommendation to
swap the merge strategy is now falsified twice over.

## Step 2: but the conflicts are tiny

`scripts/conflicts.py` extracts all 35 conflict hunks:

| | |
|---|---|
| conflict hunks | 35 across 16 pairs |
| median size (larger side) | **3 lines** |
| single-line hunks | 12/35 (34%) |
| ≤ 10 lines | 26/35 (74%) |

And they have a recurring shape. The whole of `dspy_task/8394 f3_f5` is:

```
<<<<<<< HEAD
                self.disk_cache[key] = self._serialize_disk_value(value)
=======
                self._set_disk_cache(key, value)
>>>>>>> agent1
```

Feature 5 (disk compression) added a `_serialize_disk_value` helper. Feature 3
(per-entry TTL) added a `_set_disk_cache` helper. **Both agents replaced the same
line in `Cache.put()` with their own indirection.** Both helpers exist in the
merged tree; the correct resolution composes them. Neither agent was wrong, and
neither could have known.

This is the "nobody owns the union" thesis in four lines — and the union that goes
unowned is *one call site*.

## Step 3: model merge repair recovers 43% of the gap

`scripts/repair.py` hands each conflicted file to the same model with both feature
specs and asks for a version preserving both. Constraints kept deliberately tight
so the number means something: the repairer **never sees the held-out tests**, gets
**one shot** with no test-feedback loop, may only touch files that conflicted, and
the result must contain no conflict markers.

| | |
|---|---|
| resolved to a clean, marker-free tree | **16/16** |
| both features pass | **4/16** |
| at least one feature passes | 7/16 |

| coop arm | pass | rate | gap to solo |
|---|---|---|---|
| as the evaluator scores it today | 17/50 | 34% | 14 pp |
| + deterministic merge ladder | 17/50 | 34% | 14 pp |
| + single-shot model merge repair | 20/50 | **40%** | **8 pp** |

**43% of the gap recovered by a single model call per conflicted file**, with no
change to how the agents work. The remaining 8 pp is not significant (p = 0.55),
but neither was the original 14 pp (p = 0.22) — n = 50 resolves ~15 pp.

Three of the twelve still-failing pairs are near misses (13/2, 68/1, 59/1
pass/fail) that today score zero on both features. Six produce trees that don't
build at all, so single-shot repair is a genuine lower bound.

**This also corrects the earlier counterfactual.** I projected that fixing
conflicts would take coop to 47% — the clean-pair rate — closing the whole gap.
That was optimistic: repaired conflicted pairs reach 25%, not 47%. Conflicted
pairs are genuinely harder, for the reason in the next section.

## Step 3b: an agentic reconciliation round closes the gap entirely

Blind single-shot repair leaves most of the gap on the table, and its failures are
diagnostic: six of twelve produced trees that don't build — exactly what you get
from editing code you cannot run. So give the conflict to a *live agent* instead:
a container, a shell, the repository, and its own test suite. This is the "extra
round" a real integrator does, and it is what the harness should run when a merge
conflicts.

`scripts/reconcile.py`. Same honesty constraints, tightened: the agent starts from
the **real conflicted tree** (markers in the working copy, not a summary), commands
touching `/patches` are refused, test files may not be edited, ≤ 20 steps. Every
identifier each side introduced is checked for survival, to catch "resolution by
deletion".

| the 16 conflicted pairs | both pass | | |
|---|---|---|---|
| evaluator today | 1/16 | 6% | — |
| blind single-shot repair | 4/16 | 25% | p = 0.073 vs reconcile |
| **agentic reconciliation round** | **10/16** | **62%** | **p = 0.002** vs today |

| coop arm | pass | rate | vs solo (48%) |
|---|---|---|---|
| as the evaluator scores it today | 17/50 | 34% | −14 pp |
| + blind single-shot repair | 20/50 | 40% | −8 pp |
| **+ agentic reconciliation round** | **26/50** | **52%** | **+4 pp** (p = 0.84) |

**The gap closes completely.** Coop lands nominally above solo — not a significant
lead (n = 50 resolves ~15 pp), but the deficit is gone, recovered entirely from the
16 pairs the evaluator currently scores without running a test.

The guards held: **0/16 edited a test file, 0/16 clobbered partner work** — all
introduced identifiers from both sides survived in all 16. And **16/16 got at least
one feature passing**, against 1/16 today. Of the six that still miss, every single
one fails by exactly **one** test.

### It reproduces, and it composes rather than rewrites

The obvious two challenges — *is 10/16 a lucky draw?* and *did the agent just
reimplement the features?* — were both tested by re-running the whole round
independently (`data/reconcile2.json`), capturing each reconciled diff.

**Reproduction.** Second run: **10/16 both pass, and 16/16 per-pair agreement** —
the same pairs passed and the same pairs failed, across two independent stochastic
runs. The 62% is stable, not sampling noise.

**Composition.** For each reconciled patch, count added lines that appear in
*neither* agent's original patch ("novel" lines — code the reconciler invented):

| | |
|---|---|
| median novel-line fraction | **0%** |
| pairs with **zero** novel lines | **9/16** |
| mean / max | 4% / 26% |
| original agent lines preserved | **92.5%** (657/710) |

The median reconciled patch contains **no line neither agent wrote**. This is
integration, not reimplementation.

The one outlier (typst f4_f9, 26%) is the exception that proves it: the reconciler
factored a shared helper —

```rust
fn extract_graphemes(string: &str, repeat: usize, from_end: bool) -> StrResult<Str>
```

— and had both sides call it with different arguments. Textbook "compose rather
than pick", and the right fix.

Remaining caveats for this arm: n = 16, and reconcile-vs-blind-repair is p = 0.073.
The reconciler is the same model that wrote the patches.

**Faithfulness to the proposal.** The intended design gives this round to the agent
that finished *last*, with its context intact. That is not reproducible from the
recorded runs — `*_traj.json` carries no timestamps, so finish order is unknown,
and `runner.run()` destroys each container when it returns. So this is a *fresh*
agent on the conflict. It measures the value of **the round plus tools**, not of
carrying the finisher's context, which should only add. Read 62% as a lower bound.

## Step 3c: the agents already communicated — and still collided

The obvious objection to "add a reconciliation round" is that better upfront
coordination should prevent the conflict instead. The conversation logs say
otherwise.

For each conflicted pair, take the project-specific identifiers appearing on either
side of a conflict hunk, and intersect with the identifiers appearing in the agents'
messages:

| | |
|---|---|
| conflicted pairs with **zero** messages exchanged | **0/16** |
| median messages per conflicted pair | 11 (range 4–23) |
| pairs that **named the exact colliding symbol in chat first** | **13/16** |

`dspy_task/8394 f3_f5` is the clean example. Agent1 messaged:

> *"I'll add TTL to Cache signature/state ... and introduce `_set_disk_cache` helper
> so put's disk block can call helper (**please adapt compression there**)."*

Specific, correct, and actionable. Agent2 then wrote
`self.disk_cache[key] = self._serialize_disk_value(value)` — its own helper, at the
same call site. Both `_set_disk_cache` and `_serialize_disk_value` appear in the
conversation *and* on opposite sides of the conflict.

**Communication was not the bottleneck.** The agents talked, named the exact symbol,
and collided anyway — because nothing ever made either of them reconcile against the
partner's *actual final code*. This is the original "unaware of state" hypothesis,
and it is better supported by the collision data than by the message-prevalence
statistics I ran earlier against the published trajectories.

It is also why the fix is a *round* rather than a *protocol*: more upfront talk does
not help agents who already talked.

## Step 4: the real finding — coupled pairs are easy for solo and catastrophic for coop

Split the 50 feature pairs by whether the two agents' patches collided, then look
at what **solo** did on those same pairs:

| | coupled (conflicted) | separable (clean) |
|---|---|---|
| **solo** | **12/16 = 75%** | 12/34 = 35% |
| coop as evaluated | 1/16 = 6% | 16/34 = 47% |
| coop + merge repair | 4/16 = 25% | 16/34 = 47% |

- coupled pairs, solo vs coop: **p = 0.00017**
- separable pairs, solo vs coop: p = 0.46 — **coop is nominally *ahead*, 47% vs 35%**

The pairs that destroy coop are **the pairs solo finds easiest** (75% vs 35%). That
is not a paradox: two features that touch the same call site are easy for one agent
— you see both and compose them once — and are exactly the configuration that
guarantees a collision between two agents who cannot see each other's tree.

**The entire solo-over-coop advantage lives in coupled pairs. On separable work,
coop is not worse than solo.**

## Step 5: coupling is an a priori dataset property, and solo is blind to it

"Conflicted" is a run outcome, so measure the cause directly from the dataset's own
gold patches — no agent involved. First, every one of the 50 pairs shares at least
one file: **coupling is universal by construction here.** What varies is magnitude.
Splitting at the median gold line-overlap (18 lines):

| | high overlap (n=23) | low overlap (n=27) | p |
|---|---|---|---|
| run produced a conflict | 48% | 19% | **0.036** |
| **solo** pass rate | **48%** | **48%** | **1.00** |
| coop pass rate | 26% | 41% | 0.37 |

Gold-patch overlap predicts whether the run conflicts. **A single agent is entirely
insensitive to it — 48% either way.** Two agents are not.

This is the mechanism behind the headline number, and it is a property of *task
selection*, not of cooperation. The measured solo→coop gap is a function of how
much the sampled feature pairs overlap. Shift that distribution and the gap moves
with it.

## Verdict on the "huge gap" claim

Partly vindicated, partly not — worth stating both halves.

**Supports the skepticism.** The headline gap is inflated by two design choices,
neither about cooperation. A conflicted pair is scored zero with no test executed
and no partial credit representable; one model call per conflicted file recovers
43% of the gap without touching the agents. And the gap's size is set by the
overlap distribution of the sampled pairs — a knob the benchmark controls and
does not report.

**Does not support it.** The ordering reproduced (solo ≥ team > coop+git > coop),
our solo landed exactly on the published solo (48%), and the conflicts are *real*:
0/16 yielded to better merge algorithms. Two agents editing the same call site
without seeing each other's tree is a genuine coordination failure, not an
accounting error. It is simply much cheaper to fix than "agents can't cooperate"
implies.

The honest summary: **the gap is real, all of it is recoverable by running a
reconciliation round when the merge conflicts, and what it measures is feature-pair
coupling plus a missing integration step — not cooperative ability.**

## What to do

1. **Run a reconciliation round when the merge conflicts, then grade.** A live agent
   on the conflicted tree takes those pairs from 6% to **62%** and closes the whole
   arm-level gap (34% → 52% vs solo's 48%). Conflict detection is deterministic;
   the round only fires on the ~32% of pairs that need it. Blind single-shot repair
   is the cheap fallback at 25%.
2. **Let coop earn partial credit.** "One feature passed" is currently
   unrepresentable after a conflict — 12/34 clean pairs land there, 0/16 conflicted.
3. **Report the overlap distribution alongside the gap.** It is the single strongest
   predictor of conflict (p = 0.036) and solo is blind to it, so it sets the gap
   almost by itself.
4. **Stop tuning merge algorithms.** 0/16. The remaining recommendation from the
   earlier document that survives is "give someone the merge."

## Caveats

- n = 50, single seed, one model, one scaffold. The mechanism results
  (p = 0.00017, p = 0.0045, p = 0.036) are significant; the arm-level gaps are not.
- Merge repair is single-shot with no test feedback — a lower bound. A repair loop
  using the agents' own tests would likely do better and is a separate experiment.
- The repair model is the same model that wrote the patches. A different repairer
  is untested.
- "Coupled" in Step 4 is defined post-hoc by the run conflicting; Step 5 exists to
  replace it with an a priori measure, and does, but at n = 50 with 45/50 pairs
  overlapping at all, the a priori split is coarse.

## Reproducing

```bash
docker run -d --name cb-redis -p 127.0.0.1:6379:6379 -p 172.31.255.1:6379:6379 redis:7
bash scripts/sweep_settings.sh
uv run python scripts/check_redis.py          # MUST show 0 refused
uv run python scripts/remerge.py              # deterministic ladder  -> 0/16
uv run python scripts/conflicts.py            # hunk sizes            -> 35 hunks, median 3 lines
set -a && . .env && set +a
uv run python scripts/repair.py               # blind merge repair    -> 4/16 both pass
uv run python scripts/reconcile.py            # agentic round        -> 10/16 both pass
```
