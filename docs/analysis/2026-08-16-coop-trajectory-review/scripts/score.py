#!/usr/bin/env python3
"""Score the ablation sweep: pass rate per arm with a binomial CI.

A pair passes only if BOTH features' held-out suites pass — the same criterion
the published ablation used, so the numbers are directly comparable.

Also separates genuine misses from infrastructure failures (empty patch / agent
error), because conflating them is what made the original arms unreadable.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

ARMS = [
    ("baseline", "full harness"),
    ("noscratch-legacy", "scratchpad off, prompt still describes it"),
    ("noscratch-fixed", "scratchpad off, prompt corrected"),
    ("notasklist-legacy", "task list off, prompt still documents CLI"),
    ("notasklist-fixed", "task list off, prompt corrected"),
]


def wilson(k: int, n: int) -> tuple[float, float]:
    """Wilson score interval — behaves at the small n and extreme rates a
    50-pair arm produces, where the normal approximation does not."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def score_arm(run_name: str, log_root: Path) -> dict:
    root = log_root / run_name
    passed = total = empty = 0
    for eval_path in sorted(root.rglob("eval.json")):
        try:
            data = json.loads(eval_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        total += 1
        if data.get("both_passed"):
            passed += 1
        patches = [p for p in eval_path.parent.glob("*.patch")]
        if patches and all(p.stat().st_size == 0 for p in patches):
            empty += 1
    return {"run": run_name, "passed": passed, "total": total, "empty_patches": empty}


def main() -> int:
    log_root = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs")
    prefix = sys.argv[2] if len(sys.argv) > 2 else "ablation"

    rows = [(label, score_arm(f"{prefix}-{arm}", log_root)) for arm, label in ARMS]
    print(f"{'arm':<20}{'n':>5}{'pass':>6}{'rate':>8}{'95% CI':>16}{'empty':>7}  note")
    print("-" * 96)
    for label, r in rows:
        n, k = r["total"], r["passed"]
        if n == 0:
            print(f"{r['run'].split('-', 1)[1]:<20}{'-':>5}{'-':>6}{'not run':>8}{'':>16}{'':>7}  {label}")
            continue
        lo, hi = wilson(k, n)
        name = r["run"].split("-", 1)[1]
        print(f"{name:<20}{n:>5}{k:>6}{100 * k / n:>7.1f}%{f'[{100 * lo:.0f},{100 * hi:.0f}]':>16}{r['empty_patches']:>7}  {label}")

    print("-" * 96)
    done = {r["run"].split("-", 1)[1]: r for _, r in rows if r["total"]}
    base = done.get("baseline")
    if base and base["total"]:
        b = base["passed"] / base["total"]
        for name in ("noscratch-legacy", "noscratch-fixed", "notasklist-legacy", "notasklist-fixed"):
            r = done.get(name)
            if r and r["total"]:
                print(f"  {name:<20} {100 * (r['passed'] / r['total'] - b):+.1f} pp vs baseline")
    if any(r["empty_patches"] for _, r in rows):
        print("\n  NOTE: empty patches present — check for infrastructure failure before reading rates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
