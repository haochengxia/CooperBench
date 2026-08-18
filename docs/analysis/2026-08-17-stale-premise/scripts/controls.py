#!/usr/bin/env python3
"""Controls for the staleness result: is it just a proxy for how much got written?

More edits mechanically produce more premises and more chances to falsify one,
and long trajectories may simply be harder tasks.  If staleness only predicts
failure because it tracks edit volume, it is not a coordination signal at all.

Stratifies the staleness and absorption effects by edit count, and reports
within-stratum odds ratios.  Also samples the "message names the symbol"
classification for manual validation, since that is a substring proxy.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "staleness_pop.json"
POP = Path("/tmp/cb/pop")


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
        print(f"  {title:<44} (n={len(rows)}, too small)")
        return
    a, b, c, d, o, lo, hi = odds_ratio(rows, pred)
    flag = "*" if (lo > 1 or hi < 1) else " "
    print(
        f"{flag} {title:<44} n={len(rows):4}  {100 * (a + b) / len(rows):5.1f}%  "
        f"pass {100 * a / max(a + b, 1):5.1f}% vs {100 * c / max(c + d, 1):5.1f}%  "
        f"OR={o:5.2f} [{lo:.2f},{hi:.2f}]"
    )


def pearson(xs, ys):
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys))
    return num / den if den else 0.0


def main() -> int:
    rows = json.load(open(DATA))
    print(f"N={len(rows)}\n")

    # ---- is edit volume itself the signal? ---------------------------------
    print("--- correlations ---")
    for k in ("n_stale", "shared", "n_falsified", "n_crossings", "n_msgs"):
        print(
            f"  r(n_edits, {k:<13}) = {pearson([r['n_edits'] for r in rows], [r[k] for r in rows]):+.3f}"
        )
    print(
        f"  r(n_edits, passed)        = "
        f"{pearson([r['n_edits'] for r in rows], [1.0 if r['passed'] else 0.0 for r in rows]):+.3f}"
    )
    print(
        f"  r(n_stale, shared)        = "
        f"{pearson([r['n_stale'] for r in rows], [r['shared'] for r in rows]):+.3f}"
    )

    print("\n--- does edit volume alone predict failure? ---")
    med = statistics.median([r["n_edits"] for r in rows])
    show(f"n_edits > median ({med:.0f})", rows, lambda r: r["n_edits"] > med)

    # ---- stratify by edit volume ------------------------------------------
    qs = statistics.quantiles([r["n_edits"] for r in rows], n=4)
    strata = [
        (f"edits <= {qs[0]:.0f}", [r for r in rows if r["n_edits"] <= qs[0]]),
        (f"edits {qs[0]:.0f}-{qs[1]:.0f}", [r for r in rows if qs[0] < r["n_edits"] <= qs[1]]),
        (f"edits {qs[1]:.0f}-{qs[2]:.0f}", [r for r in rows if qs[1] < r["n_edits"] <= qs[2]]),
        (f"edits > {qs[2]:.0f}", [r for r in rows if r["n_edits"] > qs[2]]),
    ]

    print("\n--- V2 staleness, WITHIN edit-volume strata ---")
    for lbl, sub in strata:
        show(f"[{lbl}] >=1 stale premise", sub, lambda r: r["n_stale"] >= 1)
    print("\n--- V2 stale SIGNATURE change, WITHIN edit-volume strata ---")
    for lbl, sub in strata:
        show(f"[{lbl}] >=1 stale sig falsification", sub, lambda r: r["n_sig_stale"] >= 1)
    print("\n--- V3 absorption, WITHIN edit-volume strata ---")
    for lbl, sub in strata:
        show(f"[{lbl}] >=1 shared identifier", sub, lambda r: r["shared"] >= 1)

    # ---- normalised (rate, not count) -------------------------------------
    print("\n--- rate-normalised measures (removes volume by construction) ---")
    for r in rows:
        r["stale_per_edit"] = r["n_stale"] / max(r["n_edits"], 1)
        r["shared_per_edit"] = r["shared"] / max(r["n_edits"], 1)
    m1 = statistics.median([r["stale_per_edit"] for r in rows])
    m2 = statistics.median([r["shared_per_edit"] for r in rows])
    show(f"stale-per-edit > median ({m1:.3f})", rows, lambda r: r["stale_per_edit"] > m1)
    show(f"shared-per-edit > median ({m2:.3f})", rows, lambda r: r["shared_per_edit"] > m2)

    # ---- validate the "named" classifier ----------------------------------
    print("\n--- sample of premises classified informed/named (manual check) ---")
    shown = 0
    for r in sorted(rows, key=lambda r: -r["n_named"])[:40]:
        if shown >= 5:
            break
        p = POP / r["file"]
        if not p.exists():
            continue
        try:
            events = json.load(gzip.open(p, "rt"))
        except Exception:
            continue
        syms = [s.split(":", 1)[1] for s in r["stale_syms"][:3]]
        msgs = [
            (e.get("message") or "")
            for e in events
            if e.get("action") == "message" and "[Inter-agent message]" in (e.get("message") or "")
        ]
        hits = [(s, m) for s in syms for m in msgs if s in m]
        if not hits:
            continue
        shown += 1
        s, m = hits[0]
        body = " ".join(m.split())
        i = body.find(s)
        print(f"\n  {r['file'][:64]}  passed={r['passed']}  named={r['n_named']}")
        print(f"    symbol: {s}")
        print(f"    ...{body[max(0, i - 110):i + 130]}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
