#!/usr/bin/env python3
"""Matched discordant-pair test, stratified by which model carried the signal.

identify.py found staleness exactly null (51%, p=0.91) in the matched design
while absorption survived (61%, p=0.044).  But the two models differ in both
staleness prevalence and pass rate, so "the run with more staleness" is
disproportionately one model -- which would bias the sign test toward null.

Stratifying by which model is the high-signal one removes that: if both strata
sit at ~50%, the null is real; if they point opposite ways and cancel, the
unstratified test was confounded.

Also runs the design on the sharper variants (stale signature changes, informed
non-repair), which are the ones the pooled analysis liked best.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "staleness_pop.json"
LABELS = Path("/tmp/cb/pop_labels.json")


def binom_p(k: int, n: int) -> float:
    if n == 0:
        return 1.0
    probs = [math.comb(n, i) * 0.5**n for i in range(n + 1)]
    return min(1.0, sum(p for p in probs if p <= probs[k] + 1e-12))


def sign_test(pairs, measure, restrict_high=None):
    """pairs: list of 2-element lists. Returns (wins, losses, ties, pct, p)."""
    wins = losses = ties = 0
    for v in pairs:
        a, b = v
        if a[measure] == b[measure]:
            continue
        hi, lo = (a, b) if a[measure] > b[measure] else (b, a)
        if restrict_high and hi["model"] != restrict_high:
            continue
        if hi["passed"] == lo["passed"]:
            ties += 1
        elif lo["passed"] and not hi["passed"]:
            wins += 1  # higher-signal run failed -> signal tracks behaviour
        else:
            losses += 1
    n = wins + losses
    return wins, losses, ties, 100 * wins / max(n, 1), binom_p(min(wins, losses), n) if n else 1.0


def main() -> int:
    rows = json.load(open(DATA))
    labels = json.load(open(LABELS))
    for r in rows:
        m = labels[r["file"]]
        r["task"] = f"{m['repo']}_{m['taskId']}_{m['features']}"

    by_task = defaultdict(list)
    for r in rows:
        by_task[r["task"]].append(r)
    pairs = [v for v in by_task.values() if len(v) == 2 and v[0]["model"] != v[1]["model"]]
    print(f"matched task pairs (gpt5 vs claude, same task): {len(pairs)}\n")

    for m in sorted({r["model"] for r in rows}):
        sub = [r for r in rows if r["model"] == m]
        pr = 100 * sum(1 for r in sub if r["passed"]) / len(sub)
        st = 100 * sum(1 for r in sub if r["n_stale"] >= 1) / len(sub)
        ab = 100 * sum(1 for r in sub if r["shared"] >= 1) / len(sub)
        print(f"  {m:<8} n={len(sub):4}  pass {pr:5.1f}%   stale>=1 {st:5.1f}%   absorb>=1 {ab:5.1f}%")

    print("\n(win = the run with MORE of the signal FAILED; 50% = no signal)\n")
    measures = [
        ("shared", "absorption (shared new ids)"),
        ("n_stale", "stale premises"),
        ("n_sig_stale", "stale SIGNATURE changes"),
        ("n_named", "informed-but-unrevised (named)"),
        ("n_no_chance", "no-chance premises"),
        ("n_crossings_stale", "stale view-crossings"),
        ("n_edits", "edit volume (negative control)"),
    ]
    print(f"{'measure':<34}{'stratum':<10}{'W':>4}{'L':>4}{'T':>5}{'pct':>7}{'p':>8}")
    print("-" * 72)
    for key, label in measures:
        for stratum in (None, "claude", "gpt5"):
            w, ls, t, pct, p = sign_test(pairs, key, stratum)
            if w + ls < 8:
                continue
            flag = "*" if p < 0.05 else " "
            name = label if stratum is None else ""
            print(
                f"{name:<34}{stratum or 'pooled':<10}{w:>4}{ls:>4}{t:>5}{pct:>6.0f}%{p:>8.3f}{flag}"
            )
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
