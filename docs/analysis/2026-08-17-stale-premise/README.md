# Stale premises — testing the code-view / staleness theory

**Date:** 2026-08-17 · **Data:** all 1,046 published coop pairs with trajectories
(gpt5 + claude, unstratified) · **Status:** exploratory, single-seed source data

The proposal was to track each agent's *code view* — what it reads and writes —
and treat a crossing between two agents' views as a coordination checkpoint,
so that merge risk could be detected from the trajectory instead of from tests.
This is the attempt to falsify it before building anything.

**Headline: the staleness family does not work, and we now know why. 96.8% of
the "contract changes" it keys on are purely additive keyword arguments, which
do not break callers.** There is almost nothing to go stale. What survives is
the *ownership* family — both agents defining the same symbol — which is the
existing absorption measure. As a side effect, this analysis closes the main
open question the 2026-08-16 review left about absorption.

---

## What was measured

Three families, on the same 1,046 pairs, from `read`/`edit`/`message` events
with a shared clock and per-agent attribution:

| | measure | shape |
|---|---|---|
| **V1** | view-crossing | agent A reads lines `[lo,hi]` of file F; B later writes into that range | R∩W, line-level, timed |
| **V2** | stale premise | A's patch references symbol S; B later changes S's definition; A never revisits the dependent file | R∩W, symbol-level, timed, directed |
| **V3** | absorption *(baseline)* | both agents introduce the same new identifier | W∩W, untimed, undirected |
| **V4** | ownership *(new)* | both agents **define** the same symbol / both re-sign it | W∩W, stricter |

Prior work framing: V1/V2 are optimistic-concurrency-control read/write sets;
V3/V4 are write/write. The bet was that R∩W is where the value is, because git
already catches W∩W. That bet lost, for a reason specific to this setting.

## The identification problem, and how it was solved

The 2026-08-16 review flagged the confound it could not rule out:

> Absorption is **correlational**. It could be a marker of coupled tasks rather
> than a cause — coupled features would produce both symbol overlap and lower
> pass rates. Distinguishing these needs the coupled/separable split.

It can be settled on existing data. 455 tasks have trajectories from *both*
gpt5 and claude. Take the pairs where the two runs disagree on outcome, and
compare how often the run carrying **more** of a signal is the one that failed.
Task coupling is held exactly fixed — same repo, same task, same two features.

One correction is essential: claude carries more of every signal (staleness
52.9% vs 26.7%, absorption 73.4% vs 50.5%) *and* passes slightly less (28.4% vs
31.4%), so "the run with more signal" is disproportionately claude and a plain
50% null is biased. The null is instead built from the same design: among task
pairs where both runs carry the **same** amount of signal, how often does claude
lose? That is the model baseline. Everything below is reported as lift over it.

`n_edits` is carried through as a negative control.

## Results — conditional matched-pair test (claude stratum)

| measure | more-signal loses | null | lift | p |
|---|---|---|---|---|
| **absorption** (shared new ids) | 56/90 = 62% | 41% | **+21** | **0.016** |
| co-re-signed symbols | 16/23 = 70% | 51% | +19 | 0.115 |
| co-defined symbols | 44/71 = 62% | 47% | +15 | 0.070 |
| co-edited files | 15/23 = 65% | 50% | +15 | 0.255 |
| co-defined, either re-signed | 15/23 = 65% | 52% | +13 | 0.265 |
| — | | | | |
| stale premises (V2) | 39/70 = 56% | 49% | +6 | 0.499 |
| informed-but-unrevised | 10/18 = 56% | 53% | +3 | 1.000 |
| stale signature changes | 14/25 = 56% | 55% | +1 | 1.000 |
| stale view-crossings (V1) | 61/117 = 52% | 54% | −2 | 1.000 |
| *`n_edits` (negative control)* | *41/87 = 47%* | *50%* | *−3* | *1.000* |

The split is by **family**, not by individual measure. Every W∩W ownership
measure lands at +13 to +21. Every R∩W staleness measure lands at −2 to +6 —
inside the noise band defined by the negative control.

### Three things this settles

**1. Absorption is causal-ish, not just a coupling marker.** +21 pp over the
model baseline with task held fixed, p=0.016. This is the answer to the open
question above, and it needed no new runs.

**2. The literal view-crossing proposal cannot be a trigger.** 98.6% of pairs
have ≥1 read-range later overwritten by the partner. Like line-number
coordination (H1: 97%, null), it is the house style, not the fault. A signal
present in 98.6% of pairs cannot discriminate 30% passing. Even ≥5 crossings,
at 73.7% prevalence, gives only OR 0.63 pooled — and −2 pp once identified.

**3. Staleness fails because there is nothing to go stale.** Of 309 signature
falsifications:

```
additive (params only added, callers keep working)   299   96.8%
breaking (a param disappeared or was renamed)         10    3.2%
```

Agents extend APIs; they almost never break them. So "B changed S after A came
to depend on it" is, 97% of the time, B adding `foo=None` to a signature — which
leaves A's call sites working. The OCC read-write-conflict analogy is simply
the wrong model for this setting. Serializability is not what is at stake.

## What the failure actually is

Not *A depended on S and B changed it*. It is *A and B both decided to build S*.
That is a duplication of ownership, and it is fixed at **plan** time, not at
integration time. The 2026-08-16 review saw the same thing from the trajectory
side and noted that communication **causes** it — a conscientious agent, told
what its partner is building, implements the partner's interface too.

Which reframes the negative results the project keeps hitting. Four families of
coordination mechanism wash at ~50%, and all four work by exchanging
information. If the dominant failure is duplicated ownership, and information
exchange is what produces duplicated ownership, then more channel should not
help — and does not.

## Descriptive: why premises stay stale

Kept for completeness, but note it describes a phenomenon that turned out not to
predict outcomes, so it should not drive design on its own. Of 1,131 falsified
premises:

| | | |
|---|---|---|
| repaired (owner revisited the file) | 131 | 11.6% |
| no_chance (owner had already stopped) | 392 | 34.7% |
| uninformed (active, no partner msg after) | 213 | 18.8% |
| informed (active, partner msg after) | 395 | 34.9% |
| ↳ …and the msg names the symbol in a code context | 179 | 15.8% |

Only ~19% of stale premises are attributable to missing information. Repair rate
is ~12%, and whether a pair repaired does **not** predict passing
(OR 0.87 [0.44, 1.71]).

## What I would do instead

The detectable event is early convergence of *intent*, not late arrival of an
invalidation — and it is visible in the planning messages, before either agent
has written much code. That is cheaper than any view tracker and it fires at the
only point where the fix (deciding who owns what) is still free.

## Two defects found and fixed during the analysis

Recorded because both would have inflated the result:

1. **Task prompts counted as inter-agent messages.** The ~19k-char task prompt
   mentions the `[Inter-agent message]` format, so an unanchored match treated
   it as partner traffic. Anchoring to the start of the body fixed it.
2. **"Message names the symbol" matched prose.** A feature titled *"Add Editor
   Environment Isolation"* was scored as telling the partner that `Editor`'s
   contract had changed. Requiring a code context (backticks, a call, an
   attribute access, a declaration) cut the `named` rate 27.1% → 15.8%.

## Limitations

- Single-seed source data; the published reports carry a ±7 pp noise band.
- The matched design is effectively **claude-only**: gpt5 is the higher-signal
  run in only 9 outcome-discordant pairs, far too few. "Absorption survives
  identification" is demonstrated for one model.
- ~10 measures tested. Only absorption clears p<0.05; the rest of the ownership
  family is directionally consistent but individually underpowered. Treat the
  family-level split as the result, not any single row.
- Symbol extraction is regex-based and Python/Go-biased (inherited from
  `absorption.py`, deliberately, so V3 stays comparable).
- The additive/breaking split compares parameter *names* between old and new
  signatures; a same-arity rename would be scored breaking, a reordering not.
- Coop only — no solo arm is published, so the difficulty control is built from
  the other 7 models' outcomes rather than from a true single-agent baseline.
