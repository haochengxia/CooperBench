# Ownership arbitration — detection that scales

**Date:** 2026-08-17 · **Status:** in progress (P0 done, P1–P3 pending)

Follows `../2026-08-17-stale-premise/`, which killed the code-view / stale-premise
direction (96.8% of partner contract changes are additive kwargs, so nothing goes
stale) and established **absorption** — both agents defining the same symbol — at
**+21 pp over the model baseline with task held exactly fixed, p=0.016**.

The story here is **arbitration triggering, not failure prediction**: the
contribution is the representation (a claim→claimants index) and the complexity
change it buys, with the claim *"without it, N=8 doesn't run."*

---

## P0 — Does arbitration cost actually scale better than pairwise? ✅

Zero compute: measured from the dataset's 199 gold patches, so this is a property
of the **benchmark**, not of any model. For N = 2…10 over all C(features, N)
subsets of each task (10,448 subsets, 30 tasks):

- `colliding_pairs` — pairs whose gold patches define a common symbol → what
  pairwise coordination must handle
- `contested_symbols` — symbols defined by ≥2 features → what ownership
  arbitration must handle

**Result (7 tasks with ≥10 features, balanced composition, N ≤ 50% of pool):**

| N | subsets | pairs C(N,2) | colliding | contested | ratio |
|---|---|---|---|---|---|
| 2 | 336 | 1 | 0.28 | 0.43 | 0.65 |
| 3 | 940 | 3 | 0.83 | 0.58 | 1.42 |
| 4 | 1660 | 6 | 1.66 | 0.73 | 2.27 |
| 5 | 1912 | 10 | 2.75 | 0.85 | 3.23 |

| extractor | colliding | quad ref | contested | linear ref |
|---|---|---|---|---|
| verbatim | 2.53 | 2.52 (+0.02) | 1.31 | 1.00 (+0.31) |
| extended | 2.51 | 2.52 (−0.00) | 0.76 | 1.00 (−0.24) |

**Colliding pairs is exactly quadratic under both extractors** — it tracks C(N,2)
to within 0.02, and the colliding *fraction* of all pairs is flat at ~0.19–0.24.
**Contested symbols grows far slower**, k ≈ 0.8–1.3.

**Verdict: Θ(N²) vs ≈Θ(N) — a clean one-order-of-N separation.** That is the
complexity claim the story needs. The stronger "arbitration cost *saturates*"
(sub-linear) claim is **not** established: the two extractors straddle linear
(0.76 vs 1.31), so sub-linearity is an artefact of extraction choice, not a
finding. Do not claim saturation.

### Three artefacts that had to be controlled for

Each one inverts or inflates the answer if left alone:

1. **Pool exhaustion.** Tasks have 2–12 features, so at N=10 on a 10-feature task
   the subset *is* the task and `contested` runs to its population ceiling. Over
   the unrestricted range contested fits k=1.16 (looks super-linear); restricted
   to N ≤ 50% of the pool it fits 0.76. The headline uses the restricted range
   and prints the unrestricted one alongside.
2. **Unbalanced composition.** At high N only feature-rich tasks contribute, so a
   fit across those levels measures the task swap, not scaling. Levels not
   contributed by the full task set are dropped (N=6 drops out: 1 task of 7).
3. **Short-range log-log bias.** C(N,2) fitted over N=2..5 has slope **2.52**, not
   2.0. Every fit is printed next to C(N,2) and N fitted over the *same* levels.
   Comparing against 2.0/1.0 would have made colliding pairs look super-quadratic.

### Extraction

Primary extractor is copied verbatim from `staleness.py` so "contested symbol"
means the same thing as "absorption" there. But it scores **3 of 30 tasks at zero
symbols** — `outlines/task1655` adds only module-level names (`hex_str = Regex(…)`,
`uuid4`, `ipv4`), and react-hook-form (TS) / typst (Rust) are missed entirely. An
extended extractor (module-level assignment, `function`/`const`, `fn`/`struct`)
covers all 30. Both are reported; the colliding-pairs result is identical under
both, the contested exponent is not.

Gold patches are test-free (verified 0/199 touch a test path). Note
`feature.filtered.patch` exists for only **2 of 199** features, so
`feature.patch` is the real input.

```bash
python3 scripts/saturation.py     # -> data/saturation.json
```

---

## P1 — Claim index on plan messages ⚠️ (the trigger framing fails)

Zero compute, over all 1,108 published pairs. Ground truth = symbols actually
co-defined by both agents, from their `edit` events — the same target absorption
measures. 10,047 inter-agent messages; 9% of pairs never exchange one.

*(Handled: published `agentId` is a renumbering of the true feature-derived ids
used inside message bodies, so claims are misattributed without mapping the two.
Unmapped senders after the fix: 0.9%.)*

### The two ceilings, which decide where detection should live

| | |
|---|---|
| co-defined symbols ever mentioned in **any** inter-agent message | **47.8%** |
| co-defined symbols already colliding in the two **specs at t=0** | **67.5%** |

**The specs contain more of the collision than all the chatter does, and they are
available before either agent starts.** Message-based claim extraction is
therefore strictly the worse place to detect — lower ceiling *and* later. That
kills the "claim index from plan messages" version of the idea.

### But the pair-level trigger does not work at all

| detector | fires on | precision | vs base rate | symbol precision |
|---|---|---|---|---|
| messages, strict claims | 46.0% | 65.3% | **+5.8** | 12.3% |
| messages, loose claims | 66.6% | 63.7% | **+4.2** | 12.3% |
| specs, all mentions | 100.0% | 59.5% | **+0.0** | 7.2% |
| specs, backticked only | 99.7% | 59.5% | +0.1 | 8.3% |
| specs, change-verb clauses | 96.5% | 59.6% | +0.1 | 11.0% |
| specs, change-verb + backticked | 89.0% | 59.3% | −0.1 | 11.8% |

The pair-level base rate is **59.5%** — that is what a trigger must beat, not
50%. Spec-based detection beats it by nothing. Message-based claims beat it by
~5 pp, which is not a trigger.

This is the same disease as every near-universal signal this project has found
(line-number refs 97%, view-crossings 98.6%): the two features come from the same
region of one repo, so *of course* their specs overlap. **"Does this pair need
arbitration" is not a discriminative question — the answer is essentially always
yes.**

Selectivity trades recall for precision without ever getting useful:
symbol-level precision climbs 7.2% → 11.8% while recall collapses 67.5% → 21.9%
(best F1 = 16.1, at change-verb).

### What this actually implies — and it is not fatal

If every pair needs arbitration, **you do not need a trigger; you need
arbitration to be cheap.** That is exactly what P0 measured: arbitration cost is
Θ(N) while pairwise cost is Θ(N²). So the contribution moves off "detect when to
arbitrate" and onto "always arbitrate, at linear cost."

That also changes which metric matters. A false-positive ownership assignment
costs one line in a prompt; a false negative costs a co-definition failure. With
that cost asymmetry **recall is the objective, not precision** — and plain
all-mentions already reaches 67.5% recall with no cleverness at all.

Which makes P3 the load-bearing experiment: the oracle has ~100% recall by
construction, so if ownership assignment does not help there, no spec-based
approximation of it will.

```bash
python3 scripts/claims.py       # -> data/claims.json      (messages, ceilings)
python3 scripts/spec_detect.py  # -> data/spec_detect.json (spec extractors)
```

## P2 — Generalize eval from 2 agents to N ✅ (and it found something)

`runner/coop.py`, the coop prompt, and Redis messaging were already N-generic;
only `eval/` was pair-bound. Changed:

- `eval/sandbox.py` — `_setup_branches(sb, n)` emits one branch per agent off
  `BASE_SHA`; `_merge_naive(sb, base_sha, n)` merges `agent1 ← agent2 ← … ← agentN`
  sequentially (order fixed by sorted feature id) and records `conflict_at`,
  the first step that failed; `test_merged` takes `feature_ids=[…], patches=[…]`
  and returns `features` + `all_passed`.
- `eval/evaluate.py` — coop path builds the patch list from `features`.

Back-compat: the `feature1_id/feature2_id/patch1/patch2` calling convention still
works, and at N==2 the legacy `feature1`/`feature2`/`both_passed` keys are still
emitted. Verified by re-evaluating `net-coop/pillow_task/68/f1_f5` with `--force`:
**every legacy key is unchanged**; the only diffs in `test_output` are the wheel
sha256, the pip temp-dir name and the pytest duration. Two new keys
(`feature_results`, `all_passed`) are added — additive, so `.get()`-based readers
are unaffected, but it is not literally byte-identical.

Smoke-tested end to end on gold patches: `pallets_jinja/1621` features `[1,2,3]`
→ 3 branches, all applied, clean sequential merge, **all three suites pass**
(47/44/49). Conflict path verified too: `[1,2,3,4,5]` → `conflict_at=agent5`.

`pytest tests/ -q`: 491 passed, 2 failed — both failures pre-exist on a clean
tree (`claude_code` adapter env test, `gh_shim` PR test), confirmed by stashing.
One test needed updating: `TestIdenticalPatchesShortCircuit` grepped the source
for the literal `patch1_content == patch2_content`. It now checks the generalized
condition **and** that the short-circuit precedes `_merge_naive`, which is the
invariant that actually matters.

### ⚠️ The finding: naive merge cannot reach N=4, even for gold

Smoke-testing surfaced that the **gold** patches conflict with each other. Gold
is correct by construction, so this is the benchmark's merge policy, not an agent
failure — and `eval/sandbox.py` scores a conflicted merge as **zero without
running any tests**.

| N | subsets | conflicted | rate |
|---|---|---|---|
| 2 | 18 | 11 | 61% |
| 3 | 18 | 16 | **89%** |
| 4 | 18 | 18 | **100%** |
| 5 | 18 | 18 | **100%** |
| 6 | 18 | 18 | **100%** |

*(3 tasks with local images, merge-only, no test runs. The N=2 figure is
conservative: the repo's own `dataset/gold_conflict_report.json` already records
**76.5%** — 499/652 — across all gold pairs, and 71.8% on these same 3 tasks.)*

**A perfect team scores zero on ~76% of pairs at N=2 and on ~100% at N≥4.** So
pass rate is not a measurable outcome beyond N=2 on this benchmark as it stands;
at N=3 the ceiling is ~11%.

Note the direction of the gap: published *agent* patches conflict at only 39.5%,
well below gold's 76.5% — **agents conflict less than gold because they change
less.**

Two consequences:

1. **P3's design survives, because the primary outcome is the mediator**
   (co-defined symbol count, measured from patches) and not pass rate. Choosing
   the mediator was already forced by the compute budget; it now also sidesteps
   the merge ceiling. The secondary pass-rate read is dead at N≥3 and should be
   dropped rather than reported.
2. **The scaling claim gets sharper and changes its reason.** "Without it, N=8
   doesn't run" is literally true — but because nobody owns the merge, not
   because coordination degrades. That lands exactly on the prior finding that
   team mode's advantage is *someone owning the merge*.

```bash
python3 scripts/gold_merge_scaling.py   # -> data/gold_merge_scaling.json
```

## P2.5 — What *kind* of failure is a gold conflict? ✅

P2 said gold patches conflict at 61%/89%/100% for N=2/3/≥4. That number is only
actionable once we know which of three things it is:

| | | |
|---|---|---|
| **A** mechanical | a different deterministic merge yields a tree that passes | evaluator bug |
| **B** shared-symbol | needs semantic reconciliation of a symbol both features touch | needs an integrator |
| **C** incompatible | the features genuinely cannot coexist | benchmark is ill-posed |

### Stage 1 — is C real? (`composability.py`, 20 tasks)

Every task ships `combined.patch`, the human-authored union of *all* its
features. If that tree passes every feature's suite, no two features of that
task are incompatible.

**18/20 tasks: ALL COMPOSABLE.** The 2 exceptions (`openai_tiktoken/0`,
`pallets_jinja/1559`) each have exactly **one** feature failing under
`combined.patch` — and both of those features **pass under their own gold patch
alone**, so this is genuine C, not a broken suite. C is real but rare and
confined: ~10% of tasks, one feature each.

### Stage 2 — A or B? (`merge_ladder.py`, 120 subsets, 20 tasks, N=2/3)

Seven deterministic rungs, each scored on *tests*, not on git exit status — a
clean tree that fails suites is not a merge, it is data loss.

| category | subsets | share |
|---|---|---|
| clean-already (naive works) | 12 | 10% |
| **A** mechanical | 34 | 28% |
| **B** shared-symbol | 74 | 62% |

Restricted to the 108 subsets that actually conflict: **31% mechanical, 69%
need semantic reconciliation**, and this is flat in N (33% at N=2, 30% at N=3).

**Only one rung ever rescued anything.** In the 7-rung pilot (30 subsets),
`patience`, `histogram`, `apply-plain`, `apply-3way` and `apply-rev` fixed
**0**; `union` fixed **12/12**. Across the broad sweep `union` fixed 34/34.
That is why the broad sweep runs `FAST=1` (naive + union only).

`union` is the sharp instrument here: it *never* conflicts, it just keeps both
sides. So it cleanly separates the two cases — if the features were editing one
region for unrelated reasons, keeping both sides works; if they were both
redefining the same thing, keeping both sides yields duplicate definitions and
the suites fail.

### Do contested symbols identify B?

Directionally yes, but **not well enough to be a trigger**:

| | contested ≥ 1 | share |
|---|---|---|
| **B** shared-symbol | 35/74 | 47% |
| **A** mechanical | 8/34 | 24% |

Fisher exact **p = 0.021** — real, and not an artefact of task identity (11/20
tasks contain both categories). But as a detector that is **81% precision
(35/43) against a 69% base rate, at 47% recall.** It misses more than half of
the subsets that need reconciliation.

This is the same verdict P1 reached from the message side, from independent
data: the pair-level *trigger* framing does not hold up, and the objective
should be recall, not precision — "always arbitrate, cheaply."

### What this means for the evaluator

The fix is **not** a smarter merge algorithm. Adding `union` as a fallback rung
is worth doing — it converts 31% of conflicts from "scored zero without running
any test" into "actually scored", deterministically and for free — but **69% of
conflicts survive every deterministic strategy**. Restoring the ceiling requires
an integration step that can *reconcile*, i.e. an integrator with a model in the
loop, which is precisely the "someone owns the merge" finding from team mode.

For P3 this reinforces the existing design rather than changing it: the primary
outcome stays the mediator (co-defined symbol count), which is computed from
patches and is therefore untouched by merge policy. Pass rate stays dropped.

```bash
python3 scripts/composability.py                                    # -> data/composability.json
python3 scripts/merge_ladder.py 2 3                                 # 7 rungs, pilot -> data/merge_ladder.json
ALL=1 FAST=1 PER_N=3 OUTFILE=merge_ladder_broad.json \
    python3 scripts/merge_ladder.py 2 3                             # 20 tasks -> data/merge_ladder_broad.json
```

## P2.75 — Does detection have any value? ✅ (it changes meaning)

P2.5 left one detector alive — contested definitions, 81% precision but 47%
recall. Before spending anything on P3 we asked whether that 47% is the *method*
or the *problem*. The incumbent detector only sees symbols being **introduced**;
two features editing the same existing function body are invisible to it. So
`detect.py` adds a strictly better t=0 representation — the enclosing scope,
which `git diff` writes into every hunk header for free, in every language —
and scores all of them against the P2.5 labels.

| detector | recall(B) | FPR(A) | prec | fires on | fisher p |
|---|---|---|---|---|---|
| co_file | 100% | 100% | 69% | 100% | 1.0 |
| hunk_near (±20 lines) | 100% | 100% | 69% | 100% | 1.0 |
| **contested defs** | 47% | 24% | **81%** | 40% | 0.021 |
| co_scope | 99% | 76% | 74% | 92% | 0.0004 |
| scope_or_def | 99% | 82% | 72% | 94% | 0.004 |

The better representation *does* reach 99% recall — and is useless anyway,
because it fires on 92% of everything. And it does not localise either: on the B
subsets it fires on, it flags **2.88 of the 3.19 touched scopes — 90% of the
surface**. There is nothing to narrow down.

### Why: the benchmark has no coupling variance

**All 652 gold feature pairs, across all 30 tasks, share a file — 100.0%, no
exceptions.** 80.1% share an enclosing scope. The median feature touches 1 file;
the median *task* touches 1 file in total.

That single structural fact retro-explains four independent nulls: P1's spec
detectors firing on 89–100% of pairs and sitting at base rate, view-crossings
landing on the negative control in the staleness analysis, and `co_file` /
`hunk_near` here. **Detection-as-gating is not weak on CooperBench, it is
unmeasurable** — every pair is coupled by construction, so the correct gate is
always "yes" and there is nothing to discriminate.

### What survives: detection as *aggregation*, not as a trigger

Coupling is universal, but the *number of contested resources* is not. Over the
10 tasks with ≥8 features:

Counted with the **resource** unit that P3 actually assigns and measures —
enclosing scopes ∪ newly declared symbols (`ownership._resources`), not the
scopes-only unit the detector table above uses:

| N | pairs | coupled pairs | contested resources |
|---|---|---|---|
| 2 | 1.0 | 1.0 | 1.69 |
| 4 | 6.0 | 6.0 | 2.55 |
| 6 | 15.0 | 15.0 | 2.92 |
| 8 | 28.0 | 28.0 | **3.23** |

Pairs grow exactly N(N−1)/2 and *every one of them is coupled*, so pairwise
coordination is Θ(N²) and — crucially — **irreducible by detection**. Contested
resources go 1.69 → 3.23 over the same range: an **8.7× compression at N=8, and
the compression ratio grows with N** (it is 0.6× at N=2 — i.e. at two agents
there is nothing to gain; the whole benefit is a scaling effect).

So the scaling argument does not rest on picking out which pairs need
coordination. All of them do. It rests on **changing the unit of coordination
from the pair to the resource**, because only the latter stays bounded. That is
a stronger and more honest claim than the one we started with.

```bash
python3 scripts/detect.py    # -> data/detect.json
```

## P3 — Oracle-ownership gate ⏳

### Pre-registered interpretation

Written **before** the runs, so the prediction is not fitted after the fact.

The two-phase reading this analysis now supports is: *arbitrate ownership before
coding, reconcile integration after coding.* Ownership is a generation-phase
mechanism — it can remove the **preventable** overlap — and P2.5 already proved
it cannot remove integration, since 69% of gold conflicts survive every
deterministic merge. So the expected signature of a **successful** P3 is:

1. ownership compliance improves substantially (manipulation check passes),
2. co-defined symbol count drops substantially (the primary mediator), and
3. **merge conflict rate drops only slightly.**

That combination is the *predicted* outcome, not a disappointment. It says
ownership solved the class of problem it addresses while integration remains the
larger, separate problem — which is exactly what the 47%-recall result and the
69% B-share already imply. A result where a single prompt fixed everything would
be *less* credible, not more.

The failure signatures that would actually count against the direction are:
compliance unchanged (the prompt did not take — experiment void), or compliance
up with co-definition unchanged (ownership is the wrong resource abstraction).

### Built and verified (runs still pending)

| piece | where |
|---|---|
| union diagnostic (never scores) | `eval/sandbox.py::_union_diagnostic`, `COOPERBENCH_UNION_DIAGNOSTIC=1` |
| oracle ownership assignment | `runner/ownership.py`, `COOPERBENCH_OWNERSHIP=1` |
| injection into every framework | `runner/coop.py::_spawn_agent` |
| tests | `tests/runner/test_ownership.py` (7) |
| subset selection | `scripts/p3_select.py` → `data/p3_subsets.json` |
| launcher (resumable, paired) | `scripts/p3_run.py` |
| scoring | `scripts/p3_measure.py` |

The resource unit is the **enclosing scope**, not the newly-defined symbol —
forced by the data, since contested definitions average only 0.32 per pair at
N=2 and the oracle would be a no-op on most pairs.

**Selection rule (declared up front):** local image, and ≥1 contested resource —
otherwise the two arms are byte-identical prompts and the run measures nothing.
This makes P3 a test of the mechanism *where the mechanism applies*, not a
population-average effect — though the restriction is mild: **550/652 = 84.4%**
of gold pairs have at least one contested resource, so only 15.6% are excluded
by construction. Selected: 10 subsets at N=2 and 10 at N=3, spanning 10 distinct
tasks each, **exactly 100 agent-runs** across both arms.

**Baseline headroom, measured on the existing 50 `net-coop` coop runs** (so the
manipulation check is known not to be capped before spending anything):

| | |
|---|---|
| violations of the would-be assignment | **0.84 per pair** |
| contested resources left to their owner | **45.5%** |
| co-touched resources | 0.66 per pair |
| naive merge conflict rate | 32% |

Agents already overlap *less* than gold (0.66 co-touched vs gold's 1.69
contested resources at N=2, same unit) — the "agents change less than gold"
pattern from P2.

**Credentials:** the gitignored `./.env` holds Azure OpenAI keys and the
deployment is `gpt-5.6-luna` — the same model `logs/net-coop` used. `cli.py`
loads it via `dotenv`, so it is invisible to a plain shell env check.

```bash
python3 scripts/p3_select.py                       # -> data/p3_subsets.json
python3 scripts/p3_run.py <model> --dry-run        # inspect the 40 subset-runs
python3 scripts/p3_run.py <model>                  # both arms, resumable
python3 scripts/p3_measure.py p3-base p3-own       # -> data/p3_measure.json
```

### Result ✅ (100 agent-runs, `gpt-5.6-luna`, Azure, docker)

40 subset-runs, 0 failures, ~$2. Both arms paired on task, subset and model;
only `COOPERBENCH_OWNERSHIP` differs.

| | baseline | ownership |
|---|---|---|
| **1. compliance** (contested resources left to owner) | 41.2% | **94.1%** |
| violations per subset | 1.30 | **0.10** |
| **2. co-touched resources** (primary mediator) | 1.15 | **0.30** |
| **4. merge conflict rate** | 50% | 10% |
| pass rate (`all_passed`) | 25% | 25% |
| per-feature pass | 32% | 42% |
| agent-runs producing an empty patch | 6% | **14%** |

**1. Manipulation check: passed decisively.** The prompt landed — verified
directly in the trajectories, both the owner variant ("You own these") and the
non-owner variant ("Owned by agent1 … do NOT edit their bodies").

**2. Primary outcome: confirmed.** Co-touched resources fall 1.15 → 0.30.
Sign test 12 better / 2 worse / 6 tied, **p = 0.013**. Excluding every subset
where any agent produced an empty patch in either arm (n=14): 1.14 → 0.36,
8 better / 1 worse, **p = 0.039**. The effect is not an artefact of agents
doing less.

**3. Scaling read: in the predicted direction.** The reduction is larger at
N=3 (−1.10) than at N=2 (−0.60), and the compliance gain is larger too
(31.2% → 93.8% at N=3 vs 50.0% → 94.4% at N=2).

**4. Conflicts fell more than pre-registered** — 50% → 10%, or 50% → 14%
excluding empty patches. This is *not* in tension with P2.5. P2.5 measured
whether an existing conflict can be **repaired** deterministically (69% cannot);
this measures conflicts being **prevented** at generation time. Prevention and
repair are different operations, which is exactly the two-phase claim.

**And integration remains unsolved, as predicted.** Pass rate is flat at 25%.
Ownership removed the preventable overlap and bought no pass rate — the residue
needs an integrator, which is P4.

### The cost: ownership makes agents abandon their own feature

Empty patches more than doubled, 6% → 14% of agent-runs. This is a real
mechanism cost, not noise — two of the abandoning agents said so outright:

> "Since `AudioBlock` is binding-owned and agent1 has completed its patch, I
> will submit without changes rather than violate ownership or introduce a
> merge conflict."

> "Since `InternationalizationExtension` is exclusively owned by agent1 and
> their completed patch is handled by the coordinated merge, I made no
> conflicting edits in my workspace."

Both over-applied the constraint: they read "you do not own X" as "do not
implement your feature", and both wrongly assumed the owner — who is
implementing a *different* feature — would cover their work.

**The escape hatch went completely unused.** The block ends with "message its
owner and agree on the change before making it". Both agents sent **0**
inter-agent messages, in subsets where 12–13 messages were exchanged overall.

So the fix is a design one, and it is specific: a binding assignment needs a
**mandatory** negotiation step, not an optional one. An agent that cannot
proceed without touching a resource it does not own must be required to
request it, and must not be allowed to submit empty. That is the same
"cooperation needs a first-class review/negotiation phase" conclusion the rest
of this project keeps arriving at.
