# Coop trajectory review — testing the coordination-failure theory

**Date:** 2026-08-16 · **Data:** 120 published coop trajectories (gpt5, claude) + all
5,216 published pair-outcomes · **Status:** exploratory, single-seed source data

We had a theory for why coop underperforms solo. This is the attempt to
falsify it against the published trajectories before building anything.

**Headline: the theory is descriptively right and causally wrong.** The
behaviours it names are real and near-universal — which is exactly why they
cannot explain the pass/fail variance. One mechanism we had *not* named does
discriminate, and it reframes what a reviewer would need to be for.

---

## The three hypotheses, as tested

| # | Hypothesis | Prevalence | Predicts failure? |
|---|---|---|---|
| H1 | Agents coordinate by line number; refs go stale | **97%** of runs | **No** — OR 1.71 [0.24, 12.0] |
| H2 | Agents never re-check partner state after partner finishes | 43% of agent-instances | **No** — OR 2.04 [0.88, 4.69], suggestive only |
| H3 | Nobody owns the union / nobody runs both features' tests | 46% of runs ran no unscoped suite | **No** — OR 0.99 [0.48, 2.03] |

`OR` = odds ratio for *passing*. `OR > 1` = associated with passing, `< 1` =
associated with failing. Odds ratios because the trajectory sample is
outcome-stratified (case-control), where relative risk would be biased.

### H1 is true and useless

Line-number coordination is not a tendency, it is the house style. 89% of all
1,399 cross-agent messages contain a line reference; 5,814 individual
references across 120 runs. Agents volunteer this unprompted — the coop prompt
explicitly suggests "file names, function names, and one-sentence intents."
One agent's closing summary describes its own method as *"coordinating
line-by-line to prevent merge conflicts."*

We can also confirm the refs really do go stale: **317 cases** where an agent
pinned a file by line number in a message and then edited that same file
itself afterwards, invalidating what it had just told its partner.

But a behaviour present in 97% of runs cannot explain why 28% of them pass.
Heavy line-referencing (>50 refs) if anything trends *worse* (OR 0.71) but the
interval spans 1. **H1 describes the medium, not the fault.**

### H2 is directionally supported but underpowered

43% of agent-instances never inspect a partner-touched file after the partner
stops editing it. Only 4% never look at all. The sharpest cut — *both* agents
re-check after their partner is done — is the strongest behavioural signal in
the theory's favour (OR 2.04) but its CI includes 1. Worth re-testing at n≈400.

### H3 is true as description, null as predictor — and the reason matters

46% of runs, neither agent ever ran an unscoped test suite. But running tests
does not predict passing (OR 0.99; and the sign flips between models: gpt5
0.52, claude 1.80).

The reason is structural, and it constrains every design that follows:

> **The grading tests are held out from the agent's tree by construction.**
> `tests.patch` is applied at eval time inside the sandbox
> (`src/cooperbench/eval/sandbox.py:39-71`), from the dataset directory. The
> agent never sees it.

So an agent that diligently runs every test it can see is running the repo's
*pre-existing* suite. That catches regressions and build breaks. It cannot
verify feature correctness, because the tests that define correctness are not
there. This is why test-running has no predictive power, and it is the single
most important constraint on the "harness gate" design.

---

## What actually predicts failure

| Signal | OR (pass) | 95% CI | Sig |
|---|---|---|---|
| **Symbol collision** — both agents introduce the same new identifier | **0.37** | [0.17, 0.81] | ✅ |
| ↳ same, restricted to **clean-merge** runs | **0.39** | [0.16, 0.93] | ✅ |
| **Both agents edit ≥2 common files** | **0.20** | [0.05, 0.83] | ✅ |
| Merge conflict reported by eval (full population, n=5,216) | 0.65 | [0.56, 0.75] | ✅ |
| *(everything from the theory above)* | — | ns | ❌ |

### The absorption mechanism

The significant signal is one we had not named. In 60% of pairs, **both agents
introduce the same new identifier**. Reading the trajectories shows why:
one agent, having "coordinated" with its partner, implements *both* features'
interface changes in its own patch.

`pillow_task290 [f2,f5]` — both agents modify `Image.quantize`'s signature:
- agent_1: *"Updated `Image.Image.quantize` signature to include `max_colors`"*
- agent_2: *"Modified `Image.Image.quantize` signature to add trailing parameters: `max_colors=None, lightness_factor=1.0`"*

agent_2 absorbed agent_1's parameter into its own patch. `click_task2800`
is the same shape: agent_1 adds `on_exit`, agent_2 extends `Context.__init__`
with *both* `on_exit` and `retry_cleanup`.

Both agents report success. Neither is wrong about its own file. **Git reports
no conflict, because there is no textual conflict — there is a semantic
one.** This is the over-sync/identical-patch collapse from the coordination
report, in its general form: not byte-identical patches, but one agent
silently taking ownership of the other's API surface.

Notably, communication *causes* this. The agents in these runs are
communicating well — they exchange plans, confirm non-overlap, acknowledge
each other. Absorption is what a conscientious agent does after being told
what its partner is building.

### Merge conflicts are statistically real and practically small

On the full published population (652 pairs × 8 models = 5,216 outcomes):

- conflict rate **39.5%**, pooled pass rate **18.1%**
- pass given conflict **14.3%**; pass given clean merge **20.5%**
- pooled OR **0.65** [0.56, 0.75] — significant

But the effect size is the point:

- **58.7% of all failures involve no merge conflict at all.**
- If every conflicted pair passed at the clean-merge rate, the pooled pass
  rate would go 18.1% → 20.5%. **Ceiling on perfect conflict handling: +2.5 pp.**

The per-model heterogeneity is also severe (minimax OR 2.97 — conflict
associated with *passing*; gemini_flash_sdk 0.22), which suggests
`hasConflict` is not measuring the same thing across harnesses.

---

## What this means for the design

The earlier plan was: fix identical-patch mechanics → harness gate (merge +
union tests) → A/B review handoff. The data revises the ordering and the
rationale.

**1. The mechanical merge layer is not where the mass is.** +2.5 pp ceiling,
and most failures merge cleanly. Deterministic conflict handling is still
worth doing because it is cheap and cannot collude — but it is not the lever.

**2. A union-test gate cannot verify correctness, only non-regression.** The
grading tests are held out. This is a hard constraint, not an implementation
detail. A gate that merges and runs visible tests is still valuable (it
catches build breaks and regressions across the union, which nobody currently
owns) but it will not catch the failures that dominate.

**3. The reviewer case gets *stronger*, and its job description changes.**
The dominant failure — clean merge, both agents confident, semantically wrong
union — is invisible to git and invisible to the tests the agent can run. It
is only visible to something that reads the spec and the diff together. That
is precisely the "reviewer is mandatory" instinct, and this is the sharpest
argument for it: **not that review is a nice second opinion, but that it is
the only instrument that can see the failure mode that actually dominates.**

**4. But the reviewer's mandate should be absorption/ownership, not general
critique.** The concrete question a reviewer must answer is: *"you and your
partner both introduce `max_colors` on `quantize` — which of you owns it, and
does the merged signature match what each spec asked for?"* That is a
checkable question. "Review this diff" is not.

**5. H1 still justifies a cheap fix, on different grounds.** Line-number
coordination doesn't predict failure, so fixing it won't move the pass rate.
But 317 self-invalidated references is real waste, and diff-based messages
(`coop-share-patch`) are strictly cheaper than prose about line ranges. Do it
for efficiency, not accuracy — and don't count it as a coordination win.

---

## Two claims we withdrew during the analysis

Recorded because both would have made it into a writeup:

1. **"91% of runs show agent identity confusion."** False. The published
   `agentId` is a *renumbering* (`agent_1`/`agent_2`) of the true in-run,
   feature-derived ids (`agent_1`/`agent_4`). The agents label themselves
   correctly; the viewer relabels them. After mapping to true ids and
   restricting to explicit first-person self-labels, the real rate is
   **4.6% of self-labelling messages, 11/120 trajectories** — minor.

2. **"20.9% of messages are self-mislabelled."** Also inflated — that regex
   counted messages that *address the partner by name* ("Agent 6, I've
   reviewed the file") as confusion. 4.6% is the defensible number.

---

## Limitations

- Source data is single-seed. The published reports carry a ±7 pp noise band;
  our n=120 trajectory sample is weaker still.
- Trajectory sample is **outcome-stratified** (50 pass / 70 fail), so absolute
  rates from it are not population rates. Only the odds ratios transfer. The
  population figures (conflict, pass rates) come from `index.json` and are
  unbiased.
- Two models only (gpt5, claude) for trajectory analysis; all 8 for outcomes.
- The index gives **pair-level** pass only, so the fail-split analysis
  (one feature passes, one fails) proposed in discussion is **not possible**
  from published data. It needs a local run writing per-feature `eval.json`.
- Identifier extraction is regex-based and Python/Go-biased; the stoplist
  removes common tokens but the measure is a proxy for absorption, not a
  parse.
- Absorption is **correlational**. It could be a marker of coupled tasks
  rather than a cause — coupled features would produce both symbol overlap and
  lower pass rates. Distinguishing these needs the coupled/separable split
  CooperAgents' Round 6 proposes.

## Reproducing

```bash
python3 scripts/analyze.py        # writes data/results.json
```

Inputs are fetched from `cooperbench/website` under
`public/static/data/coop/` (`index.json` + `trajectories/<model>/*.json.gz`).
`data/sample.json` pins the exact stratified sample (seed 17).

See [FINDINGS-scratchpad-ablation.md](FINDINGS-scratchpad-ablation.md) for a
separate, unrelated defect found while checking the ablation numbers.
