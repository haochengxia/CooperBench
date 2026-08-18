#!/usr/bin/env python3
"""At what N does naive merge stop working -- even for the GOLD patches?

Found while smoke-testing the N-generic eval: `pallets_jinja/1621` features
1-5 and `pallets_click/2068` features 1,4,9 both CONFLICT when their gold
patches are merged.  Those patches are correct by construction, so this is not
an agent failure -- it is the benchmark's merge policy.

It matters because `eval/sandbox.py` scores a conflicted merge as zero without
running any tests.  So if gold conflicts at rate p(N), then p(N) is a hard
ceiling on *any* N-agent experiment: a perfect team still scores zero that
often.  Before spending agent budget at N>2, we need p(N).

Merge only -- no test runs -- so this is fast: branch setup plus a sequential
git merge per subset.
"""

from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from cooperbench.eval.backends import get_backend
from cooperbench.eval.sandbox import _merge_naive, _setup_branches, _write_patch
from cooperbench.utils import get_image_name

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent.parent / "data"
SEED = 17
PER_N = 6  # subsets sampled per (task, N)

# Tasks whose images are present locally and which have enough features.
TASKS = [
    ("pallets_jinja_task", 1621),
    ("pallets_click_task", 2068),
    ("dottxt_ai_outlines_task", 1655),
]


def merge_only(repo: str, task: int, fids: list[int]) -> dict:
    d = DATASET / repo / f"task{task}"
    patches = [(d / f"feature{f}" / "feature.patch").read_text() for f in fids]
    sb = get_backend("docker").create_sandbox(get_image_name(repo, task), 600)
    try:
        for i, c in enumerate(patches, 1):
            _write_patch(sb, f"patch{i}.patch", c)
        setup = _setup_branches(sb, len(fids))
        if setup.get("error"):
            return {"error": setup["error"]}
        applied = all(v == "applied" for v in setup["apply_status"].values())
        naive = _merge_naive(sb, setup["base_sha"], len(fids))
        return {
            "applied": applied,
            "conflict": naive["conflict"],
            "conflict_at": naive.get("conflict_at"),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 - report, don't abort the sweep
        return {"error": str(e)}
    finally:
        try:
            sb.terminate()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    rng = random.Random(SEED)
    rows = []
    for repo, task in TASKS:
        d = DATASET / repo / f"task{task}"
        fids = sorted(
            int(p.name.replace("feature", "")) for p in d.iterdir() if p.name.startswith("feature")
        )
        for n in range(2, min(len(fids), 6) + 1):
            subs = list(combinations(fids, n))
            if len(subs) > PER_N:
                subs = rng.sample(subs, PER_N)
            for sub in subs:
                r = merge_only(repo, task, list(sub))
                rows.append({"repo": repo, "task": task, "n": n, "features": list(sub), **r})
                mark = "ERR" if r.get("error") else ("CONFLICT" if r["conflict"] else "clean")
                print(f"  {repo}/{task} N={n} {list(sub)}: {mark} {r.get('conflict_at') or ''}")

    print(f"\n{'N':>3}{'subsets':>9}{'conflicted':>12}{'rate':>8}")
    print("-" * 32)
    by_n = defaultdict(list)
    for r in rows:
        if not r.get("error"):
            by_n[r["n"]].append(r)
    for n in sorted(by_n):
        rs = by_n[n]
        c = sum(1 for r in rs if r["conflict"])
        print(f"{n:>3}{len(rs):>9}{c:>12}{100 * c / len(rs):>7.0f}%")

    errs = [r for r in rows if r.get("error")]
    if errs:
        print(f"\nerrors: {len(errs)}")
        for r in errs[:3]:
            print(f"  {r['repo']}/{r['task']} {r['features']}: {r['error'][:120]}")

    print("\nGold patches are correct by construction, so every CONFLICT above is a")
    print("pair/subset that a PERFECT team would still score zero on, because")
    print("eval/sandbox.py scores a conflicted merge as zero without running tests.")

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(OUT / "gold_merge_scaling.json", "w"), indent=1)
    print(f"\nwrote {OUT / 'gold_merge_scaling.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
