#!/usr/bin/env python3
"""Fetch the FULL published trajectory population for the OpenHands-format models.

The 2026-08-16 review used an outcome-stratified sample of 120, so only odds
ratios transferred.  Downloading every published pair for a model removes that
bias and gives ~10x the power for the repair-rate contrast, which conditions on
falsification and so burns most of the sample.

Writes to /tmp/cb/pop/<model>__<repo>_<taskId>_<features>.json.gz
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

WORK = Path("/tmp/cb")
POP = WORK / "pop"
BASE = "https://raw.githubusercontent.com/cooperbench/website/main/public/static/data/coop"

# OpenHands-format models only; the *_miniswe ones use a {metadata, steps} schema.
MODELS = ("gpt5", "claude")


def main() -> int:
    models = sys.argv[1:] or list(MODELS)
    index = json.load(open(WORK / "index.json"))
    POP.mkdir(parents=True, exist_ok=True)

    labels, pending = {}, []
    for t in index["tasks"]:
        for m in models:
            if not t["hasTrajectory"].get(m) or m not in t["results"]:
                continue
            fn = f"{t['repo']}_{t['taskId']}_{t['features']}.json.gz"
            dest = POP / f"{m}__{fn}"
            labels[dest.name] = {
                "model": m,
                "repo": t["repo"],
                "taskId": t["taskId"],
                "features": t["features"],
                "passed": t["results"][m]["passed"],
                "hasConflict": t["results"][m].get("hasConflict"),
            }
            if not dest.exists():
                pending.append((f"{BASE}/trajectories/{m}/{fn}", str(dest)))

    json.dump(labels, open(WORK / "pop_labels.json", "w"))
    print(f"{len(labels)} pairs indexed, {len(pending)} to download")

    if pending:
        listing = WORK / "dl_pop.txt"
        listing.write_text("".join(f"{u} {d}\n" for u, d in pending))
        subprocess.run(
            f"xargs -P 16 -n 2 sh -c 'curl -sfL \"$0\" -o \"$1\" || rm -f \"$1\"' < {listing}",
            shell=True,
            check=False,
        )

    print(f"on disk: {len(list(POP.glob('*.json.gz')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
