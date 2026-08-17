# The coop→solo gap is merge conflicts, and almost nothing else

> **SUPERSEDED IN PART — see [RESULTS-gap-recovery.md](RESULTS-gap-recovery.md).**
> Three claims below were tested directly by replaying every pair, and did not hold:
>
> 1. *"The naive merge writes conflict markers into source, the tree stops parsing."*
>    **Wrong.** The merge is *aborted* (`sandbox.py:530`); no markers reach the tree.
>    The zero test counts are **hardcoded by the evaluator** (`sandbox.py:264`), which
>    scores conflicted pairs zero without running any test. Worse problem, different one.
> 2. *"Replace the `naive` merge with a 3-way merge"* (recommendation 2). **Already done** —
>    `_merge_naive` is a real 3-way `git merge`. Better diff algorithms recover **0 of 16**.
> 3. *"Fix conflicts → coop 47%, the gap closes completely."* **Optimistic.** Actual
>    single-shot merge repair gives 40%, recovering **43%** of the gap, not all of it.
>
> The core finding — the gap is concentrated in conflicted pairs — survives, and the
> mechanism turns out to be *feature-pair coupling*: gold-patch overlap predicts
> conflicts (p = 0.036) while solo is blind to it (48% vs 48%).

**Date:** 2026-08-16 · **Model:** `gpt-5.6-luna` (Azure) · **Agent:** `mini_swe_agent_v2` ·
**Subset:** flash-50 · **Backend:** local Docker · single seed ·
**Coordination verified working** (`scripts/check_redis.py`: 0 refused calls)

## The triangle, measured on one stack

Solo, coop, coop+git and team on the same model, harness and evaluator — rather
than assembled from different papers' baselines.

| arm | n | both pass | rate | 95% CI | vs solo | Fisher p |
|---|---|---|---|---|---|---|
| **solo** | 50 | 24 | **48.0%** | [35, 61] | — | — |
| team | 50 | 23 | 46.0% | [33, 60] | −2.0 pp | 1.00 ns |
| coop+git | 46 | 19 | 41.3% | [28, 56] | −6.7 pp | 0.54 ns |
| coop | 50 | 17 | 34.0% | [22, 48] | −14.0 pp | 0.22 ns |

The ordering reproduces the published qualitative pattern (solo ≥ team > coop+git >
coop) and our solo lands exactly on the published solo (48%). **No pairwise difference
is significant at n = 50** — the design resolves ~15 pp, and the gap is 14 pp. The
mechanism below is significant; the headline gap on its own is not.

## The finding

Split every coop pair by whether the evaluator's merge came out clean:

| merge | n | both pass | rate |
|---|---|---|---|
| clean | 34 | 16 | **47%** |
| conflicts | 16 | 1 | **6%** |

Fisher p = **0.0045**.

**On clean merges, coop performs exactly like solo — 47% vs 48%.** The entire deficit
lives in the conflicted 32%, where the pass rate collapses to 6%.

Counterfactual: if conflicted pairs behaved like clean ones, coop would score **47%**
against solo's 48%. The 14-point gap closes essentially completely.

## The failure is mechanical, not cognitive

What actually happens on those conflicted pairs:

| merge | n | both pass | **0 tests ran** | tests ran & failed |
|---|---|---|---|---|
| clean | 34 | 16 | 2 | 16 |
| conflicts | 16 | 1 | **15** | 0 |

**15 of 16 conflicted pairs run zero tests.**

> **Corrected:** the reason is not what this section originally said. The merge is
> aborted on conflict and the evaluator *hardcodes* `tests_passed: 0, tests_failed: 0`
> (`sandbox.py:264`) — the suites are never invoked at all. The tree never stops
> parsing because the tree is discarded. See
> [RESULTS-gap-recovery.md](RESULTS-gap-recovery.md).

Clean pairs show the normal distribution of real outcomes (16 pass / 16 genuinely
fail). Conflicted pairs show a distribution with no information in it at all.

So the coop→solo gap is not agents misunderstanding each other. It is **two competent
patches destroyed by textual merge.**

## What git buys, and what team does instead

- **coop+git**: conflicted-pair pass rate rises 6% → 25%, clean 47% → 54%. Sharing a
  remote lets agents reconcile *before* the evaluator's naive merge sees their work.
  It mitigates the same failure rather than avoiding it.
- **team**: the relationship *inverts* — conflicts 51% vs clean 33% (ns, n = 15). With
  a lead integrating, an eval-level "conflict" mostly signals that both agents produced
  substantial work that the lead combined; a "clean" merge often means one agent
  contributed little. The lead is doing the job the naive merge does badly.

That reframes team mode's advantage: not better communication, but **someone owning
the merge**.

## Two hypotheses this kills

**Line-number coordination (H1) is not a coop property — it is a property of the
published runs.** In the published gpt-5/claude trajectories, 89% of cross-agent
messages carried line references. Here: **2–5%**. Same benchmark, different
model/harness, and the behaviour nearly vanishes. It was an artifact of that
model-and-scaffold combination, not something intrinsic to cooperation.

**Messaging volume still predicts nothing.** Within every arm, above-median messaging
vs at-or-below: coop 33% vs 35% (p = 1.00), coop+git 33% vs 46% (p = 0.54), team 48%
vs 45% (p = 1.00). Consistent with the published-trajectory analysis. Notably team
sends *fewer* agent messages (4.3/pair) than coop (7.4/pair) while scoring higher — its
coordination is carried by 37.4 host-injected task-list updates per pair, not by
conversation.

## Where this disagrees with my earlier analysis

The published-data analysis in [README.md](README.md) put a **+2.5 pp** ceiling on
perfect conflict handling across 5,216 outcomes. Here conflict handling is worth
**+13 pp** — the whole gap. Both can't be describing the same quantity.

The likeliest explanation is that the published `hasConflict` field is not comparable
across harnesses; it already showed wild heterogeneity there (minimax OR 2.97 —
conflict *associated with passing* — versus gemini_flash_sdk 0.22). Our number comes
from one harness where the field's meaning is fixed and checkable, and where the
mechanism (15/16 pairs running zero tests) is directly observable. I trust this one
more, at much smaller n.

## Independent convergence

CooperAgents' optimization program found that value came from *"merge repair,
Best-of-N with unit-test selection, and the coordinator"* — merge repair first, after
coordination-prompt seams, in-loop preambles, and selection all washed at n≈20. They
arrived at merge repair by search; this arrives at it by mechanism. Their
`repair_integrator` (repair only on demonstrated breakage) and `focused_repair`
(harness supplies the damage evidence, model fixes it) are the natural implementations.

## What to do about it

Ordered by expected value:

1. **Never grade a tree with conflict markers.** Detect them and run a bounded repair
   pass. 15/16 of these pairs are currently scored on a tree that cannot parse — the
   cheapest possible win, and deterministic.
2. ~~**Replace the `naive` merge strategy.** A 3-way merge against the pristine base
   would resolve a large share of these mechanically, before any model is involved.~~
   **Falsified.** It already *is* a 3-way merge, and patience/histogram/`git apply -3`
   resolve **0 of 16**. Use model merge repair instead: 4/16 flip to full pass.
3. **Prefer git sharing to messaging.** Measured 6% → 25% on exactly the failing subset.
   Messaging volume, meanwhile, predicts nothing anywhere.
4. **Give someone the merge.** Team's inverted conflict relationship suggests an owner
   beats a merge algorithm. This is the "nobody owns the union" thesis, confirmed — but
   the union that goes unowned is *textual*, not semantic.

## Caveats

- Single seed, n = 50, one model, one agent scaffold. The mechanism is significant
  (p = 0.0045); the arm-level gaps are not.
- coop+git completed 46/50 pairs (4 lost to infrastructure); rates are over completed
  pairs.
- The team conflict inversion rests on 15 clean pairs — suggestive only.
- This measures the *evaluator's* merge. Some of the effect is a property of how
  CooperBench grades coop, not of cooperation itself — which is precisely why fixing it
  is cheap, and also why an ideal fix belongs partly in the harness rather than the agents.

## Reproducing

```bash
docker run -d --name cb-redis -p 127.0.0.1:6379:6379 -p 172.31.255.1:6379:6379 redis:7
bash scripts/sweep_settings.sh
uv run python scripts/check_redis.py    # MUST show 0 refused before trusting anything
```
