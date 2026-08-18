#!/usr/bin/env python3
"""Matched-pair test against an EMPIRICAL null, not against 50%.

The plain sign test in discordant.py assumes that, absent any signal, the
higher-signal run is equally likely to be the failing one.  That is false here:
claude carries more of every signal than gpt5 (staleness 52.9% vs 26.7%,
absorption 73.4% vs 50.5%) and also passes slightly less (28.4% vs 31.4%).  So
"the run with more signal" is disproportionately claude, and claude loses
head-to-head slightly more often for reasons that have nothing to do with the
signal.

Fix: build the null from the same matched design.  Among task pairs where the
two runs carry the SAME amount of the signal, how often does model X lose?  That
is the model-baseline discordance rate.  Then compare the rate among pairs where
X carries MORE.  Both sets are same-task, same-model-pair -- only the signal
differs -- so the contrast is the signal's effect, free of both task coupling
and model baseline.

Reported as a 2x2 Fisher exact test.
"""

from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "staleness_pop.json"
LABELS = Path("/tmp/cb/pop_labels.json")


def fisher_two_sided(a: int, b: int, c: int, d: int) -> float:
    """Exact test on [[a,b],[c,d]]."""
    n = a + b + c + d
    if n == 0:
        return 1.0
    r1, c1 = a + b, a + c

    def p(x):
        return math.comb(r1, x) * math.comb(n - r1, c1 - x) / math.comb(n, c1)

    lo, hi = max(0, c1 - (n - r1)), min(r1, c1)
    obs = p(a)
    return min(1.0, sum(p(x) for x in range(lo, hi + 1) if p(x) <= obs + 1e-12))


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

    print(f"matched task pairs: {len(pairs)}")
    print("\nAmong outcome-discordant pairs, how often does the run carrying MORE")
    print("of the signal lose?  Compared against the same rate when the two runs")
    print("carry EQUAL signal (the model-baseline null).\n")

    measures = [
        ("shared", "absorption (shared new ids)"),
        ("co_defined", "co-DEFINED symbols (ownership)"),
        ("co_sig", "co-RE-SIGNED symbols (ownership)"),
        ("co_any_sig", "co-defined, either re-signed"),
        ("co_files", "co-edited files"),
        ("n_stale", "stale premises"),
        ("n_sig_stale", "stale SIGNATURE changes"),
        ("n_named", "informed-but-unrevised"),
        ("n_crossings_stale", "stale view-crossings"),
        ("n_edits", "edit volume (negative control)"),
    ]

    print(f"{'measure':<32}{'model':<8}{'MORE-signal':>13}{'EQUAL (null)':>15}{'lift':>8}{'p':>8}")
    print("-" * 84)
    for key, label in measures:
        first = True
        for X in ("claude", "gpt5"):
            w = ls = nw = nl = 0
            for v in pairs:
                a, b = v
                x = a if a["model"] == X else b
                y = b if a["model"] == X else a
                if x["passed"] == y["passed"]:
                    continue  # outcome-concordant: uninformative for a sign test
                x_lost = not x["passed"]
                if x[key] > y[key]:
                    w += x_lost
                    ls += not x_lost
                elif x[key] == y[key]:
                    nw += x_lost
                    nl += not x_lost
            if w + ls < 8 or nw + nl < 8:
                continue
            obs, null = 100 * w / (w + ls), 100 * nw / (nw + nl)
            p = fisher_two_sided(w, ls, nw, nl)
            flag = "*" if p < 0.05 else " "
            print(
                f"{label if first else '':<32}{X:<8}"
                f"{w:>5}/{w + ls:<7}{100 * w / (w + ls):>6.0f}%"
                f"{nw:>5}/{nw + nl:<5}{null:>6.0f}%{obs - null:>+7.0f}{p:>8.3f}{flag}"
            )
            first = False
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
