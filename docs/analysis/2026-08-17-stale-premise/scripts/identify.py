#!/usr/bin/env python3
"""Is staleness a property of the TASK (coupling) or of the RUN (behaviour)?

This is the confound the absorption finding could not rule out: coupled feature
pairs would produce both symbol overlap and lower pass rates, with no causal
role for anything the agents did.

Three tests, weakest to strongest:

  1. difficulty strata  -- task difficulty estimated from the other 7 models'
                           outcomes; does staleness still predict within strata?
  2. variance split     -- is the variance in staleness mostly BETWEEN tasks
                           (a task property) or WITHIN task across models
                           (a run property)?
  3. discordant pairs   -- tasks where both gpt5 and claude ran, one went stale
                           and the other did not.  Task coupling is held exactly
                           fixed; only the run differs.  A sign test on who
                           failed is then a clean behavioural read.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "staleness_pop.json"
LABELS = Path("/tmp/cb/pop_labels.json")
INDEX = Path("/tmp/cb/index.json")


def odds_ratio(rows, pred):
    a = sum(1 for r in rows if pred(r) and r["passed"])
    b = sum(1 for r in rows if pred(r) and not r["passed"])
    c = sum(1 for r in rows if not pred(r) and r["passed"])
    d = sum(1 for r in rows if not pred(r) and not r["passed"])
    aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    o = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return a, b, c, d, o, math.exp(math.log(o) - 1.96 * se), math.exp(math.log(o) + 1.96 * se)


def show(title, rows, pred):
    if len(rows) < 10:
        print(f"  {title:<46} (n={len(rows)}, too small)")
        return
    a, b, c, d, o, lo, hi = odds_ratio(rows, pred)
    flag = "*" if (lo > 1 or hi < 1) else " "
    print(
        f"{flag} {title:<46} n={len(rows):4}  pass {100 * a / max(a + b, 1):5.1f}% vs "
        f"{100 * c / max(c + d, 1):5.1f}%  OR={o:5.2f} [{lo:.2f},{hi:.2f}]"
    )


def binom_p(k: int, n: int) -> float:
    """Two-sided exact binomial test against p=0.5."""
    if n == 0:
        return 1.0
    probs = [math.comb(n, i) * 0.5**n for i in range(n + 1)]
    return min(1.0, sum(p for p in probs if p <= probs[k] + 1e-12))


def main() -> int:
    rows = json.load(open(DATA))
    labels = json.load(open(LABELS))
    index = json.load(open(INDEX))

    # task -> {model: passed} across all 8 published models
    outcomes: dict[str, dict[str, bool]] = {}
    for t in index["tasks"]:
        key = f"{t['repo']}_{t['taskId']}_{t['features']}"
        outcomes[key] = {m: v["passed"] for m, v in t["results"].items()}

    for r in rows:
        meta = labels[r["file"]]
        r["task"] = f"{meta['repo']}_{meta['taskId']}_{meta['features']}"
        others = {m: p for m, p in outcomes.get(r["task"], {}).items() if m != r["model"]}
        r["difficulty"] = 1.0 - (sum(others.values()) / len(others) if others else 0.0)

    print(f"N={len(rows)}\n")

    # ---- 1. difficulty strata ---------------------------------------------
    print("--- 1. staleness WITHIN task-difficulty strata ---")
    print("    difficulty = fraction of the OTHER 7 models that failed this task")
    bands = [
        ("easy    (<0.6 fail)", [r for r in rows if r["difficulty"] < 0.6]),
        ("medium  (0.6-0.85)", [r for r in rows if 0.6 <= r["difficulty"] < 0.857]),
        ("hard    (>=0.857)", [r for r in rows if r["difficulty"] >= 0.857]),
    ]
    for lbl, sub in bands:
        show(f"[{lbl}] >=1 stale premise", sub, lambda r: r["n_stale"] >= 1)
    for lbl, sub in bands:
        show(f"[{lbl}] >=1 shared identifier", sub, lambda r: r["shared"] >= 1)

    print("\n    is the signal itself just task difficulty?")
    for k in ("n_stale", "shared"):
        hi = [r["difficulty"] for r in rows if r[k] >= 1]
        lo = [r["difficulty"] for r in rows if r[k] == 0]
        print(
            f"      mean task-difficulty | {k}>=1: {statistics.mean(hi):.3f}   "
            f"| {k}==0: {statistics.mean(lo):.3f}   delta {statistics.mean(hi) - statistics.mean(lo):+.3f}"
        )

    # ---- 2. variance decomposition ----------------------------------------
    print("\n--- 2. variance of staleness: between tasks vs within task ---")
    by_task: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    paired = {k: v for k, v in by_task.items() if len(v) >= 2}
    print(f"    tasks with >=2 models' trajectories: {len(paired)}")
    for k in ("n_stale", "shared", "n_crossings"):
        grand = statistics.mean([r[k] for r in rows])
        within = statistics.mean(
            [statistics.pvariance([r[k] for r in v]) for v in paired.values() if len(v) >= 2]
        )
        between = statistics.pvariance([statistics.mean([r[k] for r in v]) for v in paired.values()])
        tot = within + between
        print(
            f"      {k:<12} mean={grand:5.2f}  between-task {100 * between / tot:5.1f}%  "
            f"within-task {100 * within / tot:5.1f}%"
        )

    # ---- 3. discordant matched pairs --------------------------------------
    print("\n--- 3. DISCORDANT matched pairs (task coupling held exactly fixed) ---")
    for measure, label in (("n_stale", "staleness"), ("shared", "absorption")):
        wins = losses = ties = 0
        for v in paired.values():
            if len(v) != 2:
                continue
            a, b = v
            hi_r, lo_r = (a, b) if a[measure] > b[measure] else (b, a)
            if hi_r[measure] == lo_r[measure]:
                continue
            if hi_r["passed"] == lo_r["passed"]:
                ties += 1
            elif lo_r["passed"] and not hi_r["passed"]:
                wins += 1  # the higher-signal run failed => signal is behavioural
            else:
                losses += 1
        n = wins + losses
        p = binom_p(min(wins, losses), n) if n else 1.0
        print(
            f"    {label:<11} discordant-on-outcome n={n:3} (ties {ties:3}): "
            f"higher-{measure} run FAILED in {wins}, PASSED in {losses}  "
            f"-> {100 * wins / max(n, 1):.0f}%  p={p:.3f}"
        )

    # ---- repair, within coupled + matched ---------------------------------
    coupled = [r for r in rows if r["n_falsified"] >= 1]
    print(f"\n--- repair rate within difficulty strata (coupled only, n={len(coupled)}) ---")
    for lbl, sub in (
        ("easy", [r for r in coupled if r["difficulty"] < 0.75]),
        ("hard", [r for r in coupled if r["difficulty"] >= 0.75]),
    ):
        show(f"[{lbl}] repaired >=1 falsified premise", sub, lambda r: r["n_repaired"] >= 1)

    return 0


if __name__ == "__main__":
    sys.exit(main())
