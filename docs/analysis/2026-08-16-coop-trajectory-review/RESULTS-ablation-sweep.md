# Ablation sweep: the prompt bug is real and it does not matter

**Date:** 2026-08-16 · **Model:** `gpt-5.6-luna` (Azure) · **Agent:** `mini_swe_agent_v2` ·
**Subset:** flash-50 · **Backend:** local Docker · **Setting:** team · single seed

## The prediction, and how it went

Stated before running: *the corrected arms land near baseline, the legacy arms reproduce
the published ~30% and ~40% drops; if so the published drops are instrumentation.*

**That prediction was wrong.** Correcting the prompt changed nothing. The scratchpad
drop never appeared at all. The task-list drop is real and survives correction.

| arm | n | pass | rate | 95% CI | vs baseline | Fisher p |
|---|---|---|---|---|---|---|
| baseline | 50 | 18 | **36.0%** | [24, 50] | — | — |
| noscratch-legacy | 50 | 17 | 34.0% | [22, 48] | −2.0 pp | 1.00 ns |
| noscratch-fixed | 50 | 17 | 34.0% | [22, 48] | −2.0 pp | 1.00 ns |
| notasklist-legacy | 50 | 6 | 12.0% | [6, 24] | −24.0 pp | **0.0091** |
| notasklist-fixed | 50 | 4 | 8.0% | [3, 19] | −28.0 pp | **0.0013** |

Legacy vs fixed, i.e. *does correcting the prompt matter?* — scratchpad 17/50 vs 17/50
(p = 1.00); task list 6/50 vs 4/50 (p = 0.74). **No detectable effect either way.**

Run integrity: every agent reached `Submitted`, **zero empty patches** in all 250 agent
runs, median patch 3.8–9.6 KB, ablation flags verified in `result.json`. The two
strict-provider bugs fixed earlier held; this is a clean sweep.

## Three findings

### 1. The prompt/runtime coupling defect has no measurable outcome effect

It is a genuine code defect — the audit and the container check both confirm it, and
disabling the scratchpad really does leave six dangling `/workspace/shared` references
and a member export command that exits 1. But agents route around it. Correcting the
prompt moved the pass rate by 0 pp (scratchpad) and −4 pp (task list, ns).

The honest reading: **the defect is worth fixing for hygiene and for the validity of
future arms, but it does not explain the published numbers.** My "the drops are
instrumentation" hypothesis is not supported.

### 2. The scratchpad drop did not reproduce

Published: 62% → 30%, a 32-point crash, the single largest effect in that table, and
the main evidence that a shared-state substrate is what makes team mode work.

Here: 36% → 34%, p = 1.00. Nothing.

This does not refute their number — different model (`gpt-5.6-luna` vs `gpt-5.5`),
different baseline (36% vs 62%), single seed on both sides. But the effect that
motivated a design program is absent in an otherwise faithful re-run, which is at
minimum a reason to re-measure it before building further on it.

### 3. The task list is genuinely load-bearing, and the mechanism is lead takeover

The one large, significant, robust effect: −24 pp legacy, −28 pp corrected, p < 0.01.
Directionally this **agrees** with the published arm (62% → 40%, −22 pp).

The mechanism is visible in the integration pattern. The lead owns feature 1, the
member owns feature 2:

| arm | both pass | **f1 only** | f2 only | neither | median lead patch | median member patch |
|---|---|---|---|---|---|---|
| baseline | 18 | 8 | 2 | 22 | 6 582 B | 7 536 B |
| noscratch-fixed | 17 | 6 | 2 | 25 | 6 622 B | 4 378 B |
| notasklist-legacy | 6 | 13 | 2 | 29 | 9 019 B | 6 505 B |
| notasklist-fixed | 4 | **17** | 1 | 28 | **9 566 B** | 4 663 B |

Remove the task list and the lead's patch grows ~45% while the member's shrinks ~38%,
and failures concentrate almost entirely in *f1-only* — the lead's own feature passes,
the member's does not. The team degenerates into **one agent doing the work and a
second agent's contribution failing to land.**

So the task list's value is not that it helps the lead plan. It is the mechanism by
which the *member's* work reaches the graded artifact. Take it away and you get
something close to solo-with-a-spectator. (f1-only 8 → 17 is p = 0.063 — suggestive at
this n, consistent with the significant headline drop, not independently established.)

## What this does and does not license

**Supported:** the coupling defect exists but is outcome-neutral here; the task list is
load-bearing via member-work delivery; the scratchpad effect did not reproduce.

**Not supported:** any claim that the published table is "wrong". Different model,
different baseline, single seed, n = 50 with ±13 pp intervals. This is a re-measurement
that disagrees on one arm and agrees on another, not a refutation.

**A confound I introduced:** the corrected prompts are *my* rewrites, not a neutral
deletion — with the scratchpad off the member→lead handoff had to be rerouted through
`coop-send`, and with the task list off coordination had to be re-described entirely.
The quality of that prose is now part of the measurement. `notasklist-fixed` scoring
below `notasklist-legacy` (8% vs 12%, ns) may simply mean my replacement prompt is
worse than the broken one it replaced. A cleaner design would ablate against several
independently-written replacement prompts.

**Power:** n = 50 resolves the ~28 pp task-list effect comfortably and would have
resolved a ~30 pp scratchpad effect had one existed. It cannot resolve anything under
roughly 15 pp, so every "ns" above means *undetected*, not *absent*.
