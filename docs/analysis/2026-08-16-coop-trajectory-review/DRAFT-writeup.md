# DRAFT — internal only, not for publication

> Status: local discussion draft. Nothing posted anywhere. Every number is one
> stack: `gpt-5.6-luna` / `mini_swe_agent_v2` / flash-50 / local Docker / single
> seed, n = 50 pairs. Enough to establish mechanism; nowhere near enough to restate
> anyone's headline.
>
> **Not yet held out.** The integration round was designed while looking at these
> 50 pairs and evaluated on them. Before this is argued rather than discussed it
> needs another seed, a slice of the other ~600 pairs, and a second model.

---

# Coop lacks an integration owner

Two agents splitting two features do worse than one agent doing both. We
reproduced that on our own stack, found the mechanism, and closed it. The claim,
stated as narrowly as the data supports:

**Nobody in the coop pipeline ever holds both specs, both test signals, and one
tree. Adding a bounded integration pass rescues 47% of coop's failures against 23%
of solo's, and removes coop's deficit. Two agents editing the same call site blind
to each other is a real coordination failure — the pipeline just never gives anyone
the job of resolving it.**

## What we measured

| arm | pass | rate |
|---|---|---|
| solo | 24/50 | **48.0%** |
| team | 23/50 | 46.0% |
| coop + git | 19/46 | 41.3% |
| coop | 17/50 | **34.0%** |

No pairwise difference is significant — n = 50 resolves ~15 pp, the gap is 14 pp
(p = 0.22). **Everything load-bearing below is mechanism, which is where the
significant results are.**

Two caveats on this table before it gets quoted. **Team did not reproduce**: it is
16 pp below the published 62% on this subset, and it lands *below* solo, so we
should not say "the ordering reproduces" — it partly didn't. Our solo matching the
published solo to the digit is on a different model, so treat that as coincidence,
not validation. And **coop+git is 19/46 because four pairs never ran at all** (no
log directory — infrastructure), so its rate is over completed pairs.

## 1. Coupling is a dataset property, and solo is blind to it

Measure the cause from the dataset's own gold patches, with no agent involved.
First: **all 50 pairs share at least one file.** Coupling is universal here by
construction — so "separable" is a misnomer; everything below is *low versus high*
coupling. Splitting at median gold line-overlap (18 lines):

| | high overlap (n=23) | low overlap (n=27) | p |
|---|---|---|---|
| run produced a conflict | 48% | 19% | **0.036** |
| **solo** pass rate | **48%** | **48%** | **1.00** |
| coop pass rate | 26% | 41% | 0.37 |

Gold-patch overlap predicts conflicts, and **a single agent is completely
insensitive to it.** Two agents are not.

This is a **candidate moderator that benchmarks should report**, not an
explanation of the gap: it rests on one median split, and the coop difference
itself is p = 0.37.

> **Retracted from an earlier version of this draft.** We previously split pairs by
> *whether the run conflicted* and reported solo 75% on "coupled" pairs vs 35% on
> "separable" ones, concluding coupled pairs are trivial for solo and that coop
> beats solo on separable work. That split conditions on an outcome: conflicts
> happen when both agents localized the edit correctly, which enriches for tractable
> pairs and manufactures solo's 75% on its own. The dataset-level split above is the
> honest version, and it says solo is 48% either way. **The "coop is not worse on
> separable work" claim is withdrawn** — 16/34 vs 12/34 on an outcome-selected set
> does not support it.

## 2. What the harness does with a conflict

When the merge conflicts, `eval/sandbox.py:530` aborts it, and the caller
hardcodes the outcome:

```python
# sandbox.py:262
test1_result = {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "output": ""}
test2_result = {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "output": ""}
```

The one escape requires agent1's patch *alone* to pass **both** suites. In coop it
fires 1/16.

**This is not a bug, and calling it one would be wrong.** A conflicted pair has no
merged tree, so there is nothing to test; producing one *is* the intervention. What
the code does is make a choice of construct explicit: the benchmark currently
measures **"did these two patches combine as written."** The alternative construct
is **"did two agents plus a bounded integrator produce a working result."**

We think the second is the better target — real teams always have an integration
step, and the harness already half-concedes this with a fallback that tries the
lead's patch alone. But that is an argument to be made, not a defect to be fixed,
and the rest of this document is the evidence for it.

One measurement note: partial credit is currently unrepresentable after a conflict
(0/16, vs 12/34 among clean pairs). We'd report it as a **secondary** metric —
"one feature passed in isolation" is a different construct from "both work
together."

## 3. The conflicts are real, and tiny

All 16 conflicted pairs replayed through `git merge`, `-X diff-algorithm=patience`,
`histogram`, and sequential `git apply -3`:

**0 of 16 resolved.** Genuine overlapping edits, not diff-boundary luck. Any fix
based on swapping merge strategies is dead. (`_merge_naive` is already a real 3-way
merge; only the name misleads.)

But they are small: 35 hunks, **median largest side 3 lines**, 34% single-line. An
entire conflict:

```
<<<<<<< HEAD
                self.disk_cache[key] = self._serialize_disk_value(value)
=======
                self._set_disk_cache(key, value)
>>>>>>> agent1
```

Feature 5 (disk compression) added `_serialize_disk_value`; feature 3 (TTL) added
`_set_disk_cache`. Both replaced the same line with their own indirection. Both
helpers are in the merged tree. Neither engineer was wrong.

## 4. The agents already communicated. They still collided.

| | |
|---|---|
| conflicted pairs with **zero** messages | **0/16** |
| median messages exchanged | 11 (range 4–23) |
| pairs that **named the exact colliding symbol in chat first** | **13/16** |

On the dspy pair, agent1 messaged:

> *"I'll add TTL to Cache signature/state ... and introduce `_set_disk_cache` helper
> so put's disk block can call helper (**please adapt compression there**)."*

Agent2 then wrote `self._serialize_disk_value(value)` at that same call site.

**The failure is at read time, not send time.** Nothing ever made either agent
reconcile against the partner's *actual final code*. This is why the fix is a gate,
not a protocol: more upfront talk does not help agents who already talked.

## 5. The intervention, and the control

Give a live agent the merged tree — container, shell, repository, its own test
suite — and ask it to make both features work. It never reads the held-out suites,
may not edit test files, ≤ 20 steps. Run on every failing pair in both arms.

| round applied to | rescued | median novel-line fraction |
|---|---|---|
| coop, merge conflicted (n=16) | **10/16 = 62%** | **0%** |
| coop, merge clean but failed (n=18) | 6/18 = 33% | 9% |
| **coop, all failures (n=34)** | **16/34 = 47%** | — |
| **solo, all failures (n=26)** | **6/26 = 23%** | **17%** (max 100%) |

Rescue rate, coop vs solo: **p = 0.065** — marginal, and it should be reported that
way.

| arm | rate | 95% CI |
|---|---|---|
| solo | 48.0% | [35, 61] |
| coop | 34.0% | [22, 48] |
| solo + round | 60.0% | [46, 72] |
| **coop + round** | **64.0%** | [50, 76] |

coop+round vs solo+round: p = 0.84.

**Three things follow, and the third is the important one.**

**(a) The round is not a conflict fix.** It lifts clean-merge pairs too (6/18). A
gate that fires only on conflict would miss them. So the finding is "coop has no
final union-owner pass", not "conflicts are scored unfairly."

**(b) A final pass helps solo too**, by 12 pp. So coop does not uniquely lack an
integrator — it lacks one *more acutely*. Any claim that ignores the solo control
overstates by roughly half.

**(c) The novel-line gradient shows the round is doing different work in each arm.**
Counting added lines appearing in *neither* original patch: **0% median on coop
conflicts** — the reconciled patch contains no line neither agent wrote, pure
integration — rising to 9% on clean coop pairs and **17% (max 100%) on solo**. On
solo failures the round is writing new features, i.e. it is a second attempt, not
an integration pass. That gradient is the evidence that coop's deficit specifically
is an integration deficit, and it is the number we would defend hardest.

Guards across all three cohorts: **0 edited a test file**, 0 clobbered partner work
on the conflict cohort, all identifiers from both sides preserved (92.5% of agent
lines).

**Reproduction.** The conflict cohort was run twice: 10/16 both times, with 16/16
per-pair agreement. Temperature is not pinned, and 15/16 pairs took different
trajectories (mean |Δsteps| 3.2, max 11 — one went 20→9 steps), so this is
robustness rather than determinism.

**Compute.** coop+round is two agent budgets plus a round, against solo's one plus a
round. The benchmark's headline metric does not compute-match either, so we are
within its rules, but the comparison is not compute-matched and we should say so.
The agent-side design below removes this objection entirely.

## 6. Where this should go next: move it agent-side

The version that cannot be argued to change the construct puts integration inside
the agents' own work.

Today an agent's exit is silent — the partner learns nothing, and the harness merges
two patches each written against a stale image of the other. Instead: **on finish,
an agent publishes its branch and diff to the channel. The last agent must fetch and
merge that branch, and run both features' visible tests on the merged tree, before
it may write `patch.txt`.** A mechanical precondition on submit, not a prompt
suggestion.

Three design points we hold with some confidence:

- **Merge, not rebase.** Rebase replays the second agent's commits onto the first's
  branch, destroying line-level attribution — which is exactly what the novel-line
  analysis in §5(c) depends on.
- **The gate must be unconditional**, not conflict-triggered. §5(a): 6/18 clean
  pairs needed it too.
- **This is compute-neutral.** The step limit is 100 and agents used 8–29
  (median ~16), so integration fits in existing headroom. Coop stays at two agent
  budgets with no extra round — which answers the compute objection above.

We would *not* keep the finished agent alive as a command-taker in the first
experiment: the last agent already has the context in the diff, and it muddies
budget accounting for a benefit the diff should already provide.

**One prediction we'd like to be tested, because we may be wrong.** A natural
proposal is to broadcast diffs at every commit, on the theory that §4 shows a
read-time failure. We predict this adds **≤ 2 pairs** over exit-time integration on
the 16 conflicts. Broadcasting is an in-loop coordination seam, the family that has
repeatedly washed out; gates are the family that worked. And the agents already
received a specific, correct, actionable message and did not act on it — more to
read does not obviously fix not-incorporating. It also inflates both agents'
contexts with partner churn.

The arms to run, on the same 50 pairs: (1) post-merge harness round, as measured
here; (2) exit-time agent-side integration; (3) commit-time broadcasting plus
exit-time integration. Compare per-pair pass and how much of the last agent's
budget went to integration.

The open risk is **who finishes last**: if that is the weaker agent, integration
lands on the agent least able to do it. We cannot check this retrospectively —
`*_traj.json` has no timestamps and containers are destroyed on submit — but a live
runner knows it trivially, and the gate at least makes a weak integrator fail loudly
instead of silently emitting a conflicting patch.

## 7. Team mode needs its own look

Team is not a milder coop. Of 50 team pairs, **35 conflicted** (vs coop's 16), and
**18 of those passed via the `solo-agent1` fallback** — the lead's patch alone
passing both suites. So team's 23 passes are 5 clean merges plus 18 cases where the
lead effectively did both features itself.

That reframes team mode, and it means the published team-vs-coop gap may be partly
the same artifact this document is about. Whether an integration round lifts team
too is an obvious next measurement.

Separately, the `scratchpad` and `task_list` ablation flags leave dead references in
the prompt when disabled, so those two arms measure a broken handoff rather than a
removed affordance. Worth fixing before they are cited again.

## What we are not claiming

- **We did not reproduce anyone's headline and cannot refute it.** Different model,
  different scaffold, n = 50, single seed. Team in particular did not reproduce.
- **The conflicts are real.** 0/16 yielded to better merge algorithms.
- **Our arm-level numbers are underpowered** (the gap itself is p = 0.22). Only the
  mechanism results carry weight, and the headline rescue comparison is p = 0.065.
- **Solo benefits from an integration pass too.** Coop's deficit is a matter of
  degree, not kind.

### Claims we retracted along the way

Kept deliberately, because a document arguing for rigor should show its own
corrections:

1. "The naive merge writes conflict markers and the tree stops parsing" — wrong. The
   merge is aborted; the zeros are hardcoded.
2. "Replace the naive merge with a 3-way merge" — it already is one. 0/16.
3. "Fixing merges closes the whole gap" — blind repair reaches only 40%.
4. "Coupled pairs are easy for solo; coop matches solo on separable work" —
   outcome-conditioned selection. Withdrawn in §1.
5. "The gap is not agents failing to cooperate" — overstated; contradicted by our
   own 0/16 ladder result. Two agents colliding on one call site is a genuine
   coordination failure.

## Known defect in the analysis code

Known script defect: `integrate.py` uses `grep --exclude-dir`, which busybox rejects
in the `go_chi` image — 1/18 clean-cohort items falsely reported "markers remain."
