#!/usr/bin/env python3
"""Launch both P3 arms over the subsets chosen by `p3_select.py`.

Arms are paired: identical task, identical feature subset, identical model.  The
only difference is ``COOPERBENCH_OWNERSHIP``, which appends the binding
assignment block to each agent's task text (see
``src/cooperbench/runner/ownership.py``).

``COOPERBENCH_UNION_DIAGNOSTIC=1`` is set for both arms so every conflicted
evaluation also records whether a union merge *would* have passed -- diagnostic
only, it never feeds the score.

Usage:
    python3 p3_run.py <model> [--arm base|own] [--dry-run]

Resumable: subsets whose log directory already holds a result.json are skipped,
so re-running after an interruption only fills the gaps.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3].parent
DATA = Path(__file__).resolve().parent.parent / "data"
LOGS = ROOT / "logs"

ARMS = {"base": "p3-base", "own": "p3-own"}
AGENT = "mini_swe_agent_v2"


def done(run: str, repo: str, task: int, features: list[int]) -> bool:
    d = LOGS / run / "coop" / repo / str(task) / "_".join(f"f{f}" for f in sorted(features))
    return (d / "result.json").exists()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--arm", choices=["base", "own", "both"], default="both")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--concurrency", default="2")
    args = ap.parse_args()

    subs = json.load(open(DATA / "p3_subsets.json"))
    arms = ["base", "own"] if args.arm == "both" else [args.arm]

    planned, skipped = 0, 0
    for arm in arms:
        run = ARMS[arm]
        env = dict(os.environ)
        env["COOPERBENCH_UNION_DIAGNOSTIC"] = "1"
        if arm == "own":
            env["COOPERBENCH_OWNERSHIP"] = "1"
        else:
            env.pop("COOPERBENCH_OWNERSHIP", None)

        for s in subs:
            if done(run, s["repo"], s["task"], s["features"]):
                skipped += 1
                continue
            cmd = [
                "cooperbench", "run",
                "-n", run,
                "-r", s["repo"],
                "-t", str(s["task"]),
                "-f", ",".join(str(f) for f in s["features"]),
                "-m", args.model,
                "-a", AGENT,
                "--setting", "coop",
                "--backend", "docker",
                "-c", args.concurrency,
            ]  # fmt: skip
            planned += 1
            tag = f"[{arm}] {s['repo']}/{s['task']} {s['features']}"
            if args.dry_run:
                own = "COOPERBENCH_OWNERSHIP=1 " if arm == "own" else ""
                print(f"{tag}\n    {own}{' '.join(cmd)}")
                continue
            print(f"--- {tag}", flush=True)
            r = subprocess.run(cmd, env=env, cwd=ROOT)
            if r.returncode != 0:
                print(f"    FAILED rc={r.returncode}", flush=True)

    print(f"\nplanned {planned} subset-runs, skipped {skipped} already done")
    if args.dry_run:
        print("\ndry run -- nothing executed")
    else:
        print(f"\nscore with:  python3 scripts/p3_measure.py {ARMS['base']} {ARMS['own']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
