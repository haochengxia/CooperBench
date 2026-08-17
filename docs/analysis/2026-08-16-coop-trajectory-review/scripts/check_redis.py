#!/usr/bin/env python3
"""Did the agents' coordination primitives actually work?

A Redis the containers cannot reach does not fail a run.  Every `coop-send`,
`coop-recv` and `coop-task-*` call returns a ConnectionRefused traceback, the
agent shrugs and carries on, and the run completes with plausible patches and a
plausible pass rate — describing a team that could not talk.

That is exactly what happened to the first ablation sweep (256/273 coordination
calls refused, because Redis was bound to loopback only).  Run this against any
run directory before believing a number that depends on coordination.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COOP_MARKERS = ("coop-send", "coop-recv", "coop-broadcast", "coop-peek", "coop-task")


def audit(run_dir: Path) -> dict:
    calls = refused = 0
    for traj in run_dir.rglob("*_traj.json"):
        try:
            messages = json.loads(traj.read_text()).get("messages", [])
        except (OSError, json.JSONDecodeError):
            continue
        for msg in messages:
            if msg.get("role") != "tool":
                continue
            content = str(msg.get("content", ""))
            if not any(m in content for m in COOP_MARKERS) and "redis" not in content.lower():
                continue
            calls += 1
            if "ConnectionRefused" in content or ("Traceback" in content and "redis" in content.lower()):
                refused += 1
    return {"run": run_dir.name, "coordination_calls": calls, "refused": refused}


def main() -> int:
    roots = [Path("logs") / a for a in sys.argv[1:]] or sorted(p for p in Path("logs").iterdir() if p.is_dir())
    print(f"{'run':<28}{'coord calls':>12}{'refused':>9}{'':>4}verdict")
    print("-" * 72)
    bad = 0
    for root in roots:
        if not root.is_dir():
            continue
        r = audit(root)
        if r["coordination_calls"] == 0:
            verdict = "no coordination calls (solo?)"
        elif r["refused"] == 0:
            verdict = "OK"
        else:
            pct = 100 * r["refused"] / r["coordination_calls"]
            verdict = f"BROKEN — {pct:.0f}% refused"
            bad += 1
        print(f"{r['run']:<28}{r['coordination_calls']:>12}{r['refused']:>9}{'':>4}{verdict}")
    if bad:
        print(f"\n{bad} run(s) had unreachable Redis — their coordination-dependent numbers are void.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
