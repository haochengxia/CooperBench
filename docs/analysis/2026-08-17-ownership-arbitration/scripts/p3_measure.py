#!/usr/bin/env python3
"""Score the P3 arms.  Outcomes in the pre-registered priority order.

1. MANIPULATION CHECK -- did the assignment take?  Count *violations*: an agent
   editing a contested resource that was assigned to somebody else.  If this is
   unchanged between arms the prompt did not land and the experiment is void,
   whatever the other numbers say.
2. PRIMARY (mediator) -- co-touched resources: resources that >1 agent's patch
   actually touches.  A count, so it has usable power at n=10 per N, unlike
   pass rate.
3. SCALING READ -- is the reduction larger at N=3 than N=2?
4. SECONDARY -- merge conflict rate, plus the union diagnostic.  Per P2.5 only
   ~31% of gold conflicts are mechanically fixable, so a *small* drop here
   alongside a large drop in (2) is the predicted outcome, not a failure.

Usage:
    python3 p3_measure.py <baseline-run> <ownership-run>
    python3 p3_measure.py net-coop            # single run, no comparison
"""

from __future__ import annotations

import json
import os
import sys
from itertools import combinations
from pathlib import Path

from cooperbench.runner.ownership import _resources, contested_resources

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
LOGS = ROOT / "logs"
DATA = Path(__file__).resolve().parent.parent / "data"
# SCAN=1 measures every subset a run contains, instead of only the P3 subsets --
# used to validate this pipeline against already-published runs.
SCAN = os.environ.get("SCAN") == "1"


def subset_dir(run: str, repo: str, task: int, features: list[int]) -> Path:
    return LOGS / run / "coop" / repo / str(task) / "_".join(f"f{f}" for f in sorted(features))


def measure(run: str, repo: str, task: int, features: list[int]) -> dict | None:
    d = subset_dir(run, repo, task, features)
    if not d.exists():
        return None
    task_dir = DATASET / repo / f"task{task}"
    owners = contested_resources(task_dir, features)

    touched: dict[int, set[str]] = {}
    missing = []
    for f in features:
        p = d / f"agent{f}.patch"
        if not p.exists() or not p.read_text().strip():
            missing.append(f)
            touched[f] = set()
        else:
            touched[f] = _resources(p.read_text())

    violations = sum(1 for f in features for r in touched[f] if owners.get(r, f) != f)
    respected = sum(1 for r, o in owners.items() if all(r not in touched[f] for f in features if f != o))

    co_touched = set()
    for a, b in combinations(features, 2):
        co_touched |= touched[a] & touched[b]

    merge_status, union = None, None
    ev = d / "eval.json"
    if ev.exists():
        try:
            e = json.loads(ev.read_text())
            m = e.get("merge") or {}
            merge_status = m.get("status")
            union = (m.get("union_diagnostic") or {}).get("all_passed")
        except Exception:
            pass

    return {
        "repo": repo,
        "task": task,
        "features": features,
        "n": len(features),
        "n_assigned": len(owners),
        "violations": violations,
        "respected": respected,
        "co_touched": len(co_touched),
        "co_touched_names": sorted(co_touched),
        "empty_patches": missing,
        "merge_status": merge_status,
        "union_all_passed": union,
    }


def summarise(rows: list[dict], label: str) -> dict:
    ok = [r for r in rows if r]
    if not ok:
        return {}
    n = len(ok)
    out = {
        "label": label,
        "subsets": n,
        "violations": sum(r["violations"] for r in ok) / n,
        "compliance": sum(r["respected"] for r in ok) / max(sum(r["n_assigned"] for r in ok), 1),
        "co_touched": sum(r["co_touched"] for r in ok) / n,
        "conflict_rate": sum(1 for r in ok if r["merge_status"] == "conflicts") / n,
        "empty": sum(1 for r in ok if r["empty_patches"]) / n,
    }
    return out


def show(s: dict) -> None:
    if not s:
        print("  (no data)")
        return
    print(
        f"  {s['label']:<22} subsets={s['subsets']:<4} violations/subset={s['violations']:5.2f}"
        f"  compliance={100 * s['compliance']:5.1f}%  co-touched={s['co_touched']:5.2f}"
        f"  conflict={100 * s['conflict_rate']:5.1f}%  empty={100 * s['empty']:4.0f}%"
    )


def main() -> int:
    runs = sys.argv[1:]
    if not runs:
        print(__doc__)
        return 2

    subs = json.load(open(DATA / "p3_subsets.json"))
    all_rows: dict[str, list[dict]] = {}
    for run in runs:
        rows = [] if SCAN else [measure(run, s["repo"], s["task"], s["features"]) for s in subs]
        found = [r for r in rows if r]
        if not found:  # SCAN=1, or the run holds none of the P3 subsets
            rows = []
            base = LOGS / run / "coop"
            if base.exists():
                for rd in sorted(base.iterdir()):
                    for td in sorted(rd.iterdir()):
                        for fd in sorted(td.iterdir()):
                            feats = [int(x[1:]) for x in fd.name.split("_") if x.startswith("f")]
                            if len(feats) >= 2:
                                rows.append(measure(run, rd.name, int(td.name), feats))
        all_rows[run] = [r for r in rows if r]

    for run, rows in all_rows.items():
        print(f"\n=== {run} ===")
        show(summarise(rows, "all"))
        for n in sorted({r["n"] for r in rows}):
            show(summarise([r for r in rows if r["n"] == n], f"N={n}"))

    if len(runs) == 2:
        base, own = runs
        print(f"\n{'=' * 78}\nPAIRED: {own} vs {base}")
        bmap = {(r["repo"], r["task"], tuple(r["features"])): r for r in all_rows[base]}
        omap = {(r["repo"], r["task"], tuple(r["features"])): r for r in all_rows[own]}
        keys = sorted(set(bmap) & set(omap))
        print(f"paired subsets: {len(keys)}")
        for n in sorted({len(k[2]) for k in keys}) + ["all"]:
            ks = keys if n == "all" else [k for k in keys if len(k[2]) == n]
            if not ks:
                continue
            dv = sum(omap[k]["violations"] - bmap[k]["violations"] for k in ks) / len(ks)
            dc = sum(omap[k]["co_touched"] - bmap[k]["co_touched"] for k in ks) / len(ks)
            bc = sum(1 for k in ks if bmap[k]["merge_status"] == "conflicts") / len(ks)
            oc = sum(1 for k in ks if omap[k]["merge_status"] == "conflicts") / len(ks)
            wins = sum(1 for k in ks if omap[k]["co_touched"] < bmap[k]["co_touched"])
            loss = sum(1 for k in ks if omap[k]["co_touched"] > bmap[k]["co_touched"])
            tag = f"N={n}" if n != "all" else "all"
            print(
                f"  {tag:<6} n={len(ks):<4} d(violations)={dv:+5.2f}  d(co-touched)={dc:+5.2f}"
                f"  [better {wins} / worse {loss} / tied {len(ks) - wins - loss}]"
                f"  conflict {100 * bc:.0f}% -> {100 * oc:.0f}%"
            )
        print("\nsign test on co-touched (better vs worse, ties dropped) is the primary read.")

    json.dump(all_rows, open(DATA / "p3_measure.json", "w"), indent=1)
    print(f"\nwrote {DATA / 'p3_measure.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
