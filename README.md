# CooperBench — dataset and reports

[![arXiv](https://img.shields.io/badge/arXiv-2601.13295-b31b1b.svg)](https://arxiv.org/abs/2601.13295)
[![Website](https://img.shields.io/badge/Website-cooperbench.com-blue.svg)](https://cooperbench.com)
[![Dataset](https://img.shields.io/badge/HuggingFace-Dataset-yellow.svg)](https://huggingface.co/datasets/CodeConflict/cooperbench-dataset)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://opensource.org/licenses/MIT)

**Can AI agents work together as teammates?** CooperBench measures how well AI
agents cooperate on tasks that can conflict with each other. The headline result
is that **agents coordinating on a split task do worse than one agent doing the
whole thing**.

> ## What is in this branch
>
> **Only the dataset and the written reports.** All harness code — the runner,
> the evaluator, the agent adapters, the tests, and the analysis scripts that
> produced the numbers — has been removed.
>
> Reports still cite source locations (`eval/sandbox.py:530`, `runner/ownership.py`,
> `scripts/p3_measure.py`, …) as **evidence for where a finding came from**.
> Those paths refer to the code branch, not to anything in this tree.
>
> The `.sh` files under `dataset/` are **not** harness code — they are part of
> each task (environment build and test invocation) and are required for the
> dataset to be usable.

## Dataset

| | |
|---|---|
| repositories | 12 |
| tasks | 30 |
| features | 199 |
| evaluable feature pairs | 652 |
| languages | Python, TypeScript, Go, Rust |

Each task is one real code change from an open-source repository, split into
features that a team of agents is meant to implement in parallel.

```
dataset/
  <repo_name>/
    task<id>/
      setup.sh          # build the task environment
      runner.sh         # apply patches and run a feature's tests
      run_tests.sh      # test runner
      combined.patch    # human-authored union of ALL features
      feature<n>/
        feature.md      # the task text handed to an agent
        feature.patch   # gold implementation
        tests.patch     # the feature's test suite
  subsets/              # named evaluation subsets: core, lite, flash, flash_10
```

Per repository: outlines 3/22, dspy 4/23, go-chi 3/13, huggingface-datasets 3/13,
llama-index 3/16, tiktoken 1/10, click 3/27, jinja 3/30, pillow 3/15,
react-hook-form 2/11, dirty-equals 1/9, typst 1/10 *(tasks/features)*.

## Reports

### [Ownership arbitration](docs/analysis/2026-08-17-ownership-arbitration/) — 2026-08-17→18

Can you cut down the work of merging by telling agents **who owns what** before
they start? 100 agent-runs, two arms paired on task and model.

Slides, written for a general audience:
**[English](docs/analysis/2026-08-17-ownership-arbitration/SLIDES.md)** ·
**[中文](docs/analysis/2026-08-17-ownership-arbitration/SLIDES.zh.md)**

- Detection-as-gating is **structurally unmeasurable** here: all 652 gold feature
  pairs edit the same file (100.0%), so there is nothing to discriminate.
- What survives is detection as *aggregation*: at 8 agents, 28 colliding pairs
  but only **3.23 contested resources** — an 8.7× compression that grows with N.
- Ownership works at generation time: compliance 41% → 94%, co-touched resources
  1.15 → 0.30 (p = 0.013), and the effect is larger at 3 agents than at 2.
- It does **not** improve correctness (pass rate flat at 25%), and it has a cost:
  agents abandoned their own feature (empty submissions 6% → 14%) and never used
  the "ask the owner" escape hatch.

### [Merge policy and the evaluation ceiling](docs/analysis/2026-08-17-ownership-arbitration/README.md#p25--what-kind-of-failure-is-a-gold-conflict-) — 2026-08-18

Gold patches conflict at 61% / 89% / 100% for N = 2 / 3 / ≥4, and a conflicted
merge scores zero without running any test. Of the conflicts that occur, only
**31% are mechanically fixable**; the other 69% need semantic reconciliation that
no deterministic merge performs.

### [Stale premises](docs/analysis/2026-08-17-stale-premise/) — 2026-08-17

Tests, and kills, the "code view / stale premise" direction: 96.8% of a partner's
contract changes are additive keyword arguments, so nothing actually goes stale.
What survives identification is **absorption** — both agents defining the same
symbol — at +21 pp over the model baseline with the task held fixed (p = 0.016).

### [Coop trajectory review](docs/analysis/2026-08-16-coop-trajectory-review/) — 2026-08-16

Reproduces the solo→coop gap on an independent stack and locates the mechanism.
Includes a [draft writeup](docs/analysis/2026-08-16-coop-trajectory-review/DRAFT-writeup.md),
[slides](docs/analysis/2026-08-16-coop-trajectory-review/SLIDES-internal.md), and a
[defect found in the scratchpad ablation](docs/analysis/2026-08-16-coop-trajectory-review/FINDINGS-scratchpad-ablation.md).

### [Benchmark results](docs/BENCHMARK_RESULTS.md)

Headline numbers, and the chronology of reruns and re-evaluations behind them.

## Key findings from the paper

1. **Agents perform worse together than alone** — GPT-5 and Claude Sonnet 4.5
   reach only 25% success with two-agent cooperation, roughly 50% below a single
   agent handling both features.
2. **Communication reduces conflicts but not failures** — agents spend up to 20%
   of their budget communicating, which lowers merge conflicts without improving
   success.
3. **Three capability gaps underlie the failures** — expectation failures (42%),
   communication failures (26%), commitment failures (32%).

## Citation

```bibtex
@article{cooperbench2026,
  title={CooperBench: Why Coding Agents Cannot be Your Teammates Yet},
  author={Khatua*, Arpandeep and Zhu*, Hao and Tran†, Peter and Prabhudesai†, Arya
          and Sadrieh†, Frederic and Lieberwirth†, Johann K. and Yu, Xinkai
          and Fu, Yicheng and Ryan, Michael J. and Pei, Jiaxin and Yang, Diyi},
  journal={arXiv preprint},
  year={2026},
  url={https://arxiv.org/abs/2601.13295},
  note={*Equal contribution (Stanford) · †Equal contribution (SAP Labs)}
}
```

## License

MIT
