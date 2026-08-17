---
marp: true
paginate: true
---

# Coop lacks an integration owner

### What the solo→coop gap actually is, on our stack

**Internal only — not a public claim yet**

`gpt-5.6-luna` · `mini_swe_agent_v2` · flash-50 · local Docker · single seed · n = 50

> Enough to change **how we build the harness**.
> Not yet enough to change **what the benchmark claims**.

---

## The problem

Two agents splitting two features do worse than one agent doing both.

| arm | pass | rate |
|---|---|---|
| solo | 24/50 | **48.0%** |
| team | 23/50 | 46.0% |
| coop + git | 19/46 | 41.3% |
| coop | 17/50 | **34.0%** |

- Gap = 14 pp, **p = 0.22** — n = 50 resolves ~15 pp
- Team did **not** reproduce (published 62%), and landed *below* solo
- coop+git is /46 because 4 pairs never ran (infrastructure)

**Read this table as ordering, not as a result.**

---

## Insight 1 — coupling is a dataset property, and solo is blind to it

Measured from the dataset's **gold patches**. No agent involved.
All 50 pairs share ≥1 file → coupling is universal here by construction.

Split at median gold line-overlap (18 lines):

| | high overlap | low overlap | p |
|---|---|---|---|
| run produced a conflict | 48% | 19% | **0.036** |
| **solo** pass rate | **48%** | **48%** | **1.00** |
| coop pass rate | 26% | 41% | 0.37 |

→ A **candidate moderator benchmarks should report** — not "it sets the gap."

---

## Insight 2 — the conflicts are real, and tiny

Replayed all 16 conflicted pairs through `git merge`, `patience`, `histogram`,
`git apply -3`:

# 0 / 16 resolved

Not diff-boundary luck. **Merge-algorithm fixes are dead.**

But: 35 hunks · **median largest side 3 lines** · 34% single-line

---

## An entire conflict

```
<<<<<<< HEAD
        self.disk_cache[key] = self._serialize_disk_value(value)
=======
        self._set_disk_cache(key, value)
>>>>>>> agent1
```

- Feature 5 (disk compression) added `_serialize_disk_value`
- Feature 3 (per-entry TTL) added `_set_disk_cache`
- Both replaced **the same line** with their own indirection
- Both helpers are present in the merged tree

**Neither engineer was wrong. Nobody was tasked with combining them.**

---

## Insight 3 — the agents already communicated

| | |
|---|---|
| conflicted pairs with **zero** messages | **0/16** |
| median messages exchanged | 11 (range 4–23) |
| **named the exact colliding symbol in chat first** | **13/16** |

> *"...introduce `_set_disk_cache` helper so put's disk block can call helper —
> **please adapt compression there**."* — agent1

Agent2 then wrote `self._serialize_disk_value(value)` at that same line.

**The failure is at read time, not send time.**
→ The fix is a **gate**, not a protocol. More talk doesn't help agents who talked.

---

## Insight 4 — the code fact

When the merge conflicts, `sandbox.py:530` aborts it. The caller then hardcodes:

```python
test1_result = {"passed": False, "tests_passed": 0, "tests_failed": 0, ...}
test2_result = {"passed": False, "tests_passed": 0, "tests_failed": 0, ...}
```

Only escape: agent1's patch *alone* passes **both** suites → fires **1/16**.

### This is not a bug

There is no merged tree to test. **Producing one is the intervention.**

It's a **construct choice**:
"did these two patches combine as written" **vs** "did two agents + a bounded
integrator produce a working result"

---

## The intervention

Give a live agent the merged tree: container, shell, repo, its own tests.

**Constraints:** never reads held-out suites · may not edit test files · ≤ 20 steps

| round applied to | rescued | median novel-line fraction |
|---|---|---|
| coop, merge **conflicted** (n=16) | **10/16 = 62%** | **0%** |
| coop, merge **clean** but failed (n=18) | 6/18 = 33% | 9% |
| **coop, all failures (n=34)** | **16/34 = 47%** | — |
| **solo, all failures (n=26)** ← control | **6/26 = 23%** | **17%** (max 100%) |

Rescue rate coop vs solo: **p = 0.065**

---

## Arm level

| arm | rate | 95% CI |
|---|---|---|
| solo | 48.0% | [35, 61] |
| coop | 34.0% | [22, 48] |
| solo + round | 60.0% | [46, 72] |
| **coop + round** | **64.0%** | [50, 76] |

coop+round vs solo+round: **p = 0.84**

**The deficit is gone. Neither arm wins.**

---

## The number we'd defend hardest

### Novel-line fraction = added lines appearing in *neither* original patch

| | median | |
|---|---|---|
| coop, conflicted | **0%** | pure **integration** — no line neither agent wrote |
| coop, clean failed | 9% | |
| solo, failed | **17%** (max 100%) | **new feature work** — a retry |

**The round is doing qualitatively different work in each arm.**

That gradient — not the pass rates — is what makes coop's deficit specifically an
*integration* deficit.

---

## Robustness of the round

- Conflict cohort run **twice**: 10/16 both times, **16/16 per-pair agreement**
- Temperature **not** pinned — **15/16 pairs took different trajectories**
  (mean |Δsteps| 3.2, max 11; one went 20 → 9 steps)
  → **robustness, not determinism**
- Guards: **0** test files edited · **0** clobbered partner work
  · **92.5%** of original agent lines preserved

---

## Four independent lines of evidence

1. **Code fact** — conflicts are zeroed; nobody is tasked with the union
2. **The round lifts clean pairs too** (6/18) — so it is *not* a conflict-scoring artifact
3. **Solo control** — the same pass helps solo *less* (23% vs 47%)
4. **Novel-line gradient** — coop's rescues are recombination, solo's are new work

**Four kinds of evidence pointing one way is why we trust this more than the
p-values alone suggest.**

---

## What would give us pause

The round rescues **solo** failures at 23%, with 17% novel lines.

→ That is a **retry effect**: a fresh 20-step attempt lifts solo by 12 pp.

**Some part of coop's 47% is that same retry effect.** The novel-line gradient says
the rest is integration — but that gradient is a difference in **medians on 16, 18
and 26 pairs**.

### So the claim we hold is:

> Coop lacks an integrator **more acutely**, and the round is doing **integration on
> the coop side, retry on the solo side**.

We expect that split to move on a second model.

---

## FINAL ROUND — "make finish a real event" is already shipped

`agents/default.py:466` `_announce_departed_peers()` already tells the surviving
agent, in git mode:

> *"[agent1 has completed their work and exited] ... Their submitted patch is on
> branch origin/agent1. If your changes overlap theirs, **inspect and reconcile
> before you submit**."*

### And the agents comply

| | |
|---|---|
| told the peer exited + given the branch | **43/43** |
| **inspected** the peer branch (`git fetch` / `diff`) | **43/43 = 100%** |
| **merged** the peer branch in-run | **31/43 = 72%** |

**coop+git = 41%. Still below solo's 48%.**

→ Compliance is not the problem. **Notification is not the missing piece.**

---

## So we ran the round on coop+git's failures

These agents had **already merged** the partner's code. If the round still rescues
them, what was missing was never *access*.

| round applied to failures of | rescued | median novel-line |
|---|---|---|
| **coop** — no sharing at all | **16/34 = 47%** | 3% |
| **coop+git** — branch shared *and merged* | **9/27 = 33%** | 4% |
| **solo** — one agent, one tree (control) | **6/26 = 23%** | 17% |

Both coop arms rescue by **recombination** (3–4% novel). Solo rescues by **new
work** (17%).

*Caveat: only coop-vs-solo approaches significance (p = 0.065). The middle step is
p = 0.31.*

---

## The refined conclusion

# It is not access to the partner's code. It is accountability for the partner's feature.

coop+git gives access. 72% of agents use it. A third of those failures are **still**
recoverable by pure recombination.

A coop+git agent merges the partner's branch — then goes back to optimising for
**its own** feature. Nobody's success criterion is ever *"both features work."*

---

## Arm level: the spread collapses

| arm | before | after the same round | Δ |
|---|---|---|---|
| solo | 48% | 60% | +12 |
| coop+git | 41% | 61% | +20 |
| coop | 34% | **64%** | **+30** |

### spread 14 pp → 4 pp

Every arm converges once someone is made accountable for the union.
(coop+round vs solo+round: p = 0.84)

---

## The design conclusion — and it's shippable

The notification exists. **The gate and the mandate do not.**

The last agent **must**, before writing `patch.txt`: merge the partner's branch,
and **run both features' visible tests on the merged tree**.

A mechanical precondition on submit — not a prompt suggestion. The prompt
suggestion is exactly what's deployed today, and it yields 41%.

- **Merge, not rebase** — rebase destroys the line attribution the novel-line analysis needs
- **Gate must be unconditional**, not conflict-triggered → clean pairs need it too
- **Compute-neutral**: step limit is **100**, agents used **8–29** (median ~16)
  → fits existing headroom; coop stays at two budgets

---

## Prediction on record

A natural alternative: **broadcast diffs at every commit** (since Insight 3 shows a
read-time failure).

### We predict this adds ≤ 2 pairs over exit-time integration.

- Broadcasting is an **in-loop coordination seam** — the family that repeatedly washes
- Gates are the family that worked
- The agents already received a correct, specific, actionable message and didn't act on it
- It also inflates both contexts with partner churn

Worth running — **third**.

---

## Loose end: team mode may be a solo arm

| | |
|---|---|
| team pairs that conflicted | **35/50** (vs coop's 16) |
| of those, passed via `solo-agent1` fallback | **18** |

**Team's 23 passes = 5 clean merges + 18 lead-did-everything.**

On coupled pairs, team looks like solo with extra overhead.

If it holds, this reframes the whole team-vs-coop story — but it is **one stack**.
Want the same fallback-share number on a second model before saying it out loud.

---

## Where the bar is

| | status |
|---|---|
| **Internal insight — act on it** | ✅ met (four independent lines) |
| **Public claim** | ❌ not yet |

**Why not yet:**
- Arm-level is **p = 0.84**; rescue-rate difference is **p = 0.065**
- One model, one scaffold, **one seed**
- The round was **designed against these failures** — reproduction is a robustness
  check on the same pairs, **not a held-out test**

---

## Three checks — status after the final round

1. ~~**Agent-side integration matches the harness round**~~
   **Partly answered, and it changed the claim.** The agent-side *notification*
   ships today with 100% inspect / 72% merge compliance and still yields 41%.
   The untested piece is now narrower: the **submit gate + both-features mandate**.
2. **A second seed** — the whole thing is 50 pairs on one draw
3. **One more model** — both the fix *and* the diagnosis were built on this stack

**Still to build:** the mechanical gate. What we now know is that the notification
half of it is not what's load-bearing.

---

## Claims we retracted getting here

1. "Naive merge writes conflict markers, tree stops parsing" → merge is **aborted**; zeros are hardcoded
2. "Replace naive merge with 3-way" → **it already is one**; 0/16
3. "Fixing merges closes the whole gap" → blind repair reaches only 40%
4. "Coupled pairs are easy for solo; coop matches solo on separable work"
   → **outcome-conditioned selection**; withdrawn
5. "The gap is not agents failing to cooperate" → **overstated**; contradicted by our own 0/16 ladder

**Kept visible on purpose.** A case arguing for rigor should show its own corrections.

---

# Bottom line

**Nobody in the coop pipeline is ever accountable for both features at once.**

- The conflicts are real, tiny, and were **named in chat before they happened**
- Agents already get the partner's code and **already merge it (72%)** — and it isn't enough
- Coop's rescues are **recombination** (3–4% novel); solo's are **new work** (17%)
- Give any arm an integration pass and the **14 pp spread collapses to 4 pp**

### The missing piece is a success criterion, not a channel.

### Enough to change how we build the harness.
### Not yet enough to change what the benchmark claims.
