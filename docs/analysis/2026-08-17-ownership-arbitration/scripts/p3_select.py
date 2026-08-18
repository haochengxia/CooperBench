#!/usr/bin/env python3
"""Pick the P3 subsets, deterministically, and emit the exact run commands.

Selection rule, declared up front:

1. only tasks whose image is already local (no pulls mid-experiment);
2. only subsets with at least one **contested resource** -- the oracle is
   literally a no-op otherwise (it emits no block, so the two arms would be
   byte-identical prompts), and spending runs there measures nothing;
3. at most one subset per task first, so the 10 subsets at each N span 10
   different tasks rather than 10 slices of the same repo;
4. seeded, so the same subsets come back on a re-run.

Rule 2 makes this a test of the mechanism *where the mechanism applies*, not an
estimate of a population-average effect. It has to be stated that way in the
writeup: at N=2 only ~57% of gold pairs have a contested resource at all.
"""

from __future__ import annotations

import json
import random
import subprocess
import sys
from itertools import combinations
from pathlib import Path

from cooperbench.runner.ownership import contested_resources
from cooperbench.utils import get_image_name

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent.parent / "data"
SEED = 17
PER_N = 10


def local_tasks() -> list[tuple[str, int]]:
    out = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True
    ).stdout
    imgs = {ln.strip() for ln in out.splitlines() if ln.strip()}
    found = []
    for rd in sorted(DATASET.iterdir()):
        if not rd.is_dir() or rd.name == "subsets":
            continue
        for td in sorted(rd.iterdir()):
            if td.is_dir() and td.name.startswith("task"):
                t = int(td.name.replace("task", ""))
                if get_image_name(rd.name, t) in imgs:
                    found.append((rd.name, t))
    return found


def pick(n: int, rng: random.Random) -> list[dict]:
    """One eligible subset per task, then a second pass if we still need more."""
    pools: dict[tuple[str, int], list[dict]] = {}
    for repo, task in local_tasks():
        d = DATASET / repo / f"task{task}"
        fids = sorted(int(p.name[7:]) for p in d.iterdir() if p.name.startswith("feature"))
        if len(fids) < n:
            continue
        elig = []
        for sub in combinations(fids, n):
            owners = contested_resources(d, list(sub))
            if owners:
                elig.append(
                    {
                        "repo": repo,
                        "task": task,
                        "features": list(sub),
                        "n": n,
                        "contested": sorted(owners),
                        "n_contested": len(owners),
                    }
                )
        if elig:
            rng.shuffle(elig)
            pools[(repo, task)] = elig

    chosen: list[dict] = []
    for key in sorted(pools):
        chosen.append(pools[key][0])
    rng.shuffle(chosen)
    chosen = chosen[:PER_N]
    if len(chosen) < PER_N:  # top up from tasks with more than one eligible subset
        extra = [s for key in sorted(pools) for s in pools[key][1:]]
        rng.shuffle(extra)
        chosen += extra[: PER_N - len(chosen)]
    return chosen


def main() -> int:
    rng = random.Random(SEED)
    subsets = pick(2, rng) + pick(3, rng)

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(subsets, open(OUT / "p3_subsets.json", "w"), indent=1)

    for n in (2, 3):
        got = [s for s in subsets if s["n"] == n]
        print(f"N={n}: {len(got)} subsets across {len({(s['repo'], s['task']) for s in got})} tasks")
        for s in got:
            print(f"  {s['repo'][:26]:<26}{s['task']:<7}{str(s['features']):<12}{s['n_contested']} contested {s['contested'][:4]}")
        print()

    runs = sum(s["n"] for s in subsets) * 2
    print(f"agent-runs across both arms: {runs}")
    print(f"\nwrote {OUT / 'p3_subsets.json'}")
    print("\nlaunch with:  python3 scripts/p3_run.py <model>   (set the arm env inside)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
