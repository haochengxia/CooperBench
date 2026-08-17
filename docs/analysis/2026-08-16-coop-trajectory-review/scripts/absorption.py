#!/usr/bin/env python3
"""Measure the absorption failure mode: both agents introducing the same symbol.

Absorption = one agent implements its partner's interface changes inside its
own patch.  Git sees no conflict (there is no textual overlap to conflict on),
both agents report success, and the merged union is wrong.

Reports odds ratios for passing, since the trajectory sample is
outcome-stratified.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import statistics
from pathlib import Path

TRAJ = Path("/tmp/cb/traj")
SAMPLE = Path("/tmp/cb/sample.json")

# Tokens common to almost any Python/Go diff — sharing these is not absorption.
STOP = set(
    """None True False bool int str float list dict set tuple type self cls args kwargs
__init__ __call__ __str__ __repr__ __enter__ __exit__ return yield import from class def func
value values result results data item items name names key keys index count size length
error err exc exception test tests assert print format text content path file line lines
Optional Callable Union Any List Dict Set Tuple Iterator Sequence Mapping default options
config params param options kwargs opts obj node ctx context request response""".split()
)

DECL = re.compile(r"\b(?:def|func|class|type)\s+(\w+)")
KWARG = re.compile(r"\b(\w+)\s*(?::\s*[\w\[\]\.\|\s]+)?\s*=\s*(?:None|False|True|0|1\.0|1|\"\"|'')")


def odds_ratio(rows: list[dict], pred) -> tuple[int, int, int, int, float, float, float]:
    """Haldane-corrected OR for *passing*, with a 95% CI."""
    a = sum(1 for r in rows if pred(r) and r["passed"])
    b = sum(1 for r in rows if pred(r) and not r["passed"])
    c = sum(1 for r in rows if not pred(r) and r["passed"])
    d = sum(1 for r in rows if not pred(r) and not r["passed"])
    aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    o = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return a, b, c, d, o, math.exp(math.log(o) - 1.96 * se), math.exp(math.log(o) + 1.96 * se)


def introduced_symbols(events: list[dict]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """agent -> identifiers it introduced, and agent -> paths it edited."""
    added: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    for e in events:
        if e.get("action") != "edit":
            continue
        agent = e.get("agentId")
        args = e.get("args") or {}
        text = (args.get("new_str") or "") + "\n" + (args.get("file_text") or "")
        syms = added.setdefault(agent, set())
        paths.setdefault(agent, set())
        if args.get("path"):
            paths[agent].add(args["path"])
        for pattern in (DECL, KWARG):
            for m in pattern.finditer(text):
                g = m.group(1)
                if g and len(g) > 3 and g not in STOP:
                    syms.add(g)
    return added, paths


def main() -> int:
    sample = json.load(open(SAMPLE))
    label = {}
    for model, entries in sample.items():
        for t in entries:
            label[f"{model}__{t['repo']}_{t['taskId']}_{t['features']}.json.gz"] = {"model": model, **t}

    rows = []
    for p in sorted(TRAJ.glob("*.json.gz")):
        events = json.load(gzip.open(p, "rt"))
        added, paths = introduced_symbols(events)
        agents = sorted(added)
        if len(agents) < 2:
            continue
        shared = added[agents[0]] & added[agents[1]]
        co_files = paths.get(agents[0], set()) & paths.get(agents[1], set())
        meta = label.get(p.name, {})
        rows.append(
            {
                "file": p.name,
                "model": meta.get("model"),
                "passed": meta.get("passed"),
                "hasConflict": meta.get("hasConflict"),
                "shared": len(shared),
                "co_files": len(co_files),
                "sample": sorted(shared)[:6],
            }
        )

    def show(title: str, pred, subset: list[dict] | None = None) -> None:
        rs = rows if subset is None else subset
        a, b, c, d, o, lo, hi = odds_ratio(rs, pred)
        flag = "*" if (lo > 1 or hi < 1) else " "
        print(f"{flag} {title:<42} {a:3}P/{b:3}F | {c:3}P/{d:3}F  OR={o:5.2f} [{lo:.2f},{hi:.2f}]")

    print(f"N={len(rows)}   (* = 95% CI excludes 1; OR<1 => associated with FAILING)\n")
    print("--- absorption ---")
    for t in (1, 2, 3, 5):
        show(f">={t} shared new identifiers", lambda r, x=t: r["shared"] >= x)

    n = sum(1 for r in rows if r["shared"] > 0)
    print(f"\nprevalence {n}/{len(rows)} = {100 * n / len(rows):.0f}%")
    print(f"median shared per pair: {statistics.median([r['shared'] for r in rows])}")

    clean = [r for r in rows if not r["hasConflict"]]
    print(f"\n--- within clean merges only (n={len(clean)}) ---")
    for t in (1, 2, 3):
        show(f">={t} shared new identifiers", lambda r, x=t: r["shared"] >= x, clean)

    print("\n--- co-editing control ---")
    show("both agents edited >=1 common file", lambda r: r["co_files"] > 0)
    show("both agents edited >=2 common files", lambda r: r["co_files"] >= 2)

    json.dump(rows, open("/tmp/cb/absorption2.json", "w"), indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
