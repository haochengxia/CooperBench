#!/usr/bin/env python3
"""Fetch the published coop outcomes + a stratified trajectory sample.

Population outcomes come from index.json (all 652 pairs x 8 models, unbiased).
Trajectories are sampled outcome-stratified, so only odds ratios transfer from
them -- see README.md "Limitations".

Writes to /tmp/cb/: index.json, sample.json, traj/<model>__<task>.json.gz
"""

from __future__ import annotations

import json
import math
import random
import subprocess
from pathlib import Path

WORK = Path("/tmp/cb")
BASE = "https://raw.githubusercontent.com/cooperbench/website/main/public/static/data/coop"
SEED = 17
N_PASS, N_FAIL = 25, 35
MODELS = ("gpt5", "claude")


def sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def fetch_index() -> dict:
    WORK.mkdir(parents=True, exist_ok=True)
    dest = WORK / "index.json"
    if not dest.exists():
        sh("curl", "-sfL", f"{BASE}/index.json", "-o", str(dest))
    return json.load(open(dest))


def population_report(index: dict) -> None:
    """Conflict vs outcome across every published pair -- no sampling bias."""
    tasks, models = index["tasks"], index["models"]
    print(f"{'model':<22}{'n':>5}{'pass%':>8}{'conflict%':>11}{'p|conf':>9}{'p|clean':>9}{'OR':>7}")
    print("-" * 71)
    tot = [0, 0, 0, 0]
    for m in models:
        rs = [t["results"][m] for t in tasks if m in t["results"]]
        conf = [r for r in rs if r.get("hasConflict")]
        clean = [r for r in rs if not r.get("hasConflict")]
        a = sum(1 for r in conf if r["passed"])
        b = len(conf) - a
        c = sum(1 for r in clean if r["passed"])
        d = len(clean) - c
        for i, v in enumerate((a, b, c, d)):
            tot[i] += v
        o = ((a + 0.5) * (d + 0.5)) / ((b + 0.5) * (c + 0.5))
        print(
            f"{m:<22}{len(rs):>5}{100 * (a + c) / len(rs):>7.1f}%{100 * len(conf) / len(rs):>10.1f}%"
            f"{100 * a / max(len(conf), 1):>8.1f}%{100 * c / max(len(clean), 1):>8.1f}%{o:>7.2f}"
        )
    a, b, c, d = tot
    n = a + b + c + d
    o = ((a + 0.5) * (d + 0.5)) / ((b + 0.5) * (c + 0.5))
    se = math.sqrt(1 / (a + 0.5) + 1 / (b + 0.5) + 1 / (c + 0.5) + 1 / (d + 0.5))
    print("-" * 71)
    print(f"POOLED n={n} OR={o:.2f} [{math.exp(math.log(o) - 1.96 * se):.2f}, {math.exp(math.log(o) + 1.96 * se):.2f}]")
    print(f"failures with NO conflict: {d}/{b + d} = {100 * d / (b + d):.1f}%")
    base = (a + c) / n
    lifted = ((c / (c + d)) * (a + b) + c) / n
    print(
        f"pass rate {100 * base:.1f}% -> {100 * lifted:.1f}% if conflicts handled perfectly (+{100 * (lifted - base):.1f} pp)"
    )


def build_sample(index: dict) -> dict:
    random.seed(SEED)
    out = {}
    for m in MODELS:
        avail = [t for t in index["tasks"] if t["hasTrajectory"].get(m)]
        p = [t for t in avail if t["results"][m]["passed"]]
        f = [t for t in avail if not t["results"][m]["passed"]]
        chosen = random.sample(p, min(N_PASS, len(p))) + random.sample(f, min(N_FAIL, len(f)))
        out[m] = [
            {
                "repo": t["repo"],
                "taskId": t["taskId"],
                "features": t["features"],
                "passed": t["results"][m]["passed"],
                "hasConflict": t["results"][m]["hasConflict"],
            }
            for t in chosen
        ]
    json.dump(out, open(WORK / "sample.json", "w"))
    return out


def download(sample: dict) -> None:
    traj = WORK / "traj"
    traj.mkdir(exist_ok=True)
    pairs = []
    for m, entries in sample.items():
        for t in entries:
            fn = f"{t['repo']}_{t['taskId']}_{t['features']}.json.gz"
            dest = traj / f"{m}__{fn}"
            if not dest.exists():
                pairs.append((f"{BASE}/trajectories/{m}/{fn}", str(dest)))
    listing = WORK / "dl.txt"
    listing.write_text("".join(f"{u} {d}\n" for u, d in pairs))
    if pairs:
        subprocess.run(
            f'xargs -P 12 -n 2 sh -c \'curl -sfL "$0" -o "$1" || echo "FAIL $0"\' < {listing}',
            shell=True,
            check=False,
        )
    print(f"trajectories on disk: {len(list(traj.glob('*.json.gz')))}")


def main() -> int:
    index = fetch_index()
    population_report(index)
    print()
    download(build_sample(index))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
