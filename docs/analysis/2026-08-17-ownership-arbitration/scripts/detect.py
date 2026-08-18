#!/usr/bin/env python3
"""Is 47% recall the method, or the problem?

P2.5 labelled 108 conflicting gold subsets as either A (a deterministic merge
fixes it) or B (needs semantic reconciliation).  The only detector tried so far
-- *contested definitions*, i.e. two features defining the same new symbol --
hit B at 81% precision but only **47% recall**.

That detector can only see symbols being *introduced*.  Two features editing the
same existing function body, without either declaring anything new, are
completely invisible to it -- and that is the textbook shared-symbol clash.  So
before concluding anything about detection, we ask whether a better *t=0*
representation closes the gap.  Every predictor here is computed from the gold
patches alone: no tests, no agents, no execution.

  co_file       both features touch a common file
  hunk_near     their hunks in a common file come within GAP lines (pre-image)
  co_scope      both have a hunk whose diff header names the same enclosing
                function/class -- git puts it there for free, in every language
  contested     both DEFINE a common new symbol (the incumbent detector)
  scope_or_def  co_scope OR contested

Scored against the P2.5 label.  A detector is only interesting if it separates
B from A -- predicting "conflict" is worthless here because 90% of subsets
conflict anyway.
"""

from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from itertools import combinations
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
DATA = Path(__file__).resolve().parent.parent / "data"
GAP = int(os.environ.get("GAP", "20"))

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$")
# enclosing-scope name out of a diff header's context, across languages
SCOPE = re.compile(
    r"\b(?:def|class)\s+(\w+)"  # python
    r"|\bfunc\s+(?:\([^)]*\)\s*)?(\w+)"  # go
    r"|\b(?:fn|struct|impl|trait)\s+(\w+)"  # rust
    r"|\b(?:function|const|let|class|interface)\s+(\w+)"  # js/ts
)

DECL = re.compile(r"\b(?:def|func|class|type)\s+(\w+)")
KWARG = re.compile(r"\b(\w+)\s*(?::\s*[\w\[\]\.\|\s]+)?\s*=\s*(?:None|False|True|0|1\.0|1|\"\"|'')")
MODLEVEL = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)", re.M)
STOP = set(
    """None True False bool int str float list dict set tuple type self cls args kwargs
return yield import from class def func value values result results data item items name
names key keys index count size length error err exc exception test tests assert print
format text content path file line lines Optional Callable Union Any List Dict Set Tuple
config params param options kwargs opts obj node ctx context request response""".split()
)


def parse(patch: str) -> dict:
    """-> {files, ranges: {file: [(start,end)]}, scopes: {(file,scope)}, defs: {sym}}"""
    files: set[str] = set()
    ranges: dict[str, list[tuple[int, int]]] = defaultdict(list)
    scopes: set[tuple[str, str]] = set()
    cur = ""
    added: list[str] = []
    for line in patch.splitlines():
        if line.startswith("--- a/") or line.startswith("--- /dev/null"):
            continue
        if line.startswith("+++ "):
            cur = line.split("\t")[0][6:].strip()
            if cur and cur != "ev/null":
                files.add(cur)
            continue
        m = HUNK.match(line)
        if m and cur:
            start = int(m.group(1))
            length = int(m.group(2) or 1)
            if length:  # length 0 == pure insertion point
                ranges[cur].append((start, start + length))
            ctx = m.group(3) or ""
            sm = SCOPE.search(ctx)
            if sm:
                scopes.add((cur, next(g for g in sm.groups() if g)))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    body = "\n".join(added)
    defs = set()
    for pat in (DECL, KWARG, MODLEVEL):
        for m2 in pat.finditer(body):
            s = m2.group(1)
            if len(s) > 3 and s not in STOP and not s.startswith("__"):
                defs.add(s)
    return {"files": files, "ranges": dict(ranges), "scopes": scopes, "defs": defs}


def near(a: dict, b: dict, gap: int) -> bool:
    for f in a["files"] & b["files"]:
        for s1, e1 in a["ranges"].get(f, []):
            for s2, e2 in b["ranges"].get(f, []):
                if s1 - gap <= e2 and s2 - gap <= e1:
                    return True
    return False


def signals(parsed: list[dict], gap: int) -> dict:
    co_file = co_scope = hunk_near = contested = 0
    scope_names: set[str] = set()
    def_names: set[str] = set()
    for a, b in combinations(parsed, 2):
        if a["files"] & b["files"]:
            co_file += 1
        sh = a["scopes"] & b["scopes"]
        if sh:
            co_scope += 1
            scope_names |= {s for _, s in sh}
        if near(a, b, gap):
            hunk_near += 1
        cd = a["defs"] & b["defs"]
        if cd:
            contested += 1
            def_names |= cd
    return {
        "co_file": co_file,
        "co_scope": co_scope,
        "hunk_near": hunk_near,
        "contested": contested,
        "scope_names": sorted(scope_names),
        "def_names": sorted(def_names),
        "n_touched_scopes": len({s for p in parsed for s in p["scopes"]}),
        "n_touched_files": len({f for p in parsed for f in p["files"]}),
    }


def fisher(a: int, b: int, c: int, d: int) -> float:
    n = a + b + c + d
    obs = comb(a + b, a) * comb(c + d, c) / comb(n, a + c)
    p = 0.0
    for i in range(0, min(a + b, a + c) + 1):
        j, k = a + b - i, a + c - i
        ll = d - (a - i)
        if j < 0 or k < 0 or ll < 0 or k > c + d:
            continue
        pr = comb(a + b, i) * comb(c + d, k) / comb(n, a + c)
        if pr <= obs + 1e-12:
            p += pr
    return min(p, 1.0)


def main() -> int:
    rows = json.load(open(DATA / "merge_ladder_broad.json"))
    rows = [r for r in rows if not r.get("error") and r.get("category")]

    cache: dict[tuple[str, int, int], dict] = {}

    def get(repo: str, task: int, f: int) -> dict:
        k = (repo, task, f)
        if k not in cache:
            cache[k] = parse((DATASET / repo / f"task{task}" / f"feature{f}" / "feature.patch").read_text())
        return cache[k]

    out = []
    for r in rows:
        parsed = [get(r["repo"], r["task"], f) for f in r["features"]]
        out.append({**{k: r[k] for k in ("repo", "task", "n", "features", "category")}, **signals(parsed, GAP)})

    conf = [r for r in out if r["category"] != "clean-already"]
    B = [r for r in conf if r["category"] == "B-shared-symbol"]
    A = [r for r in conf if r["category"] == "A-mechanical"]
    print(f"conflicting subsets: {len(conf)}   B={len(B)}  A={len(A)}   (base rate B = {100 * len(B) / len(conf):.0f}%)\n")

    print(f"{'detector':<14}{'recall(B)':>11}{'FPR(A)':>9}{'prec':>7}{'fires':>8}{'fisher p':>11}")
    print("-" * 60)
    best = []
    for name in ("co_file", "hunk_near", "contested", "co_scope", "scope_or_def"):
        def hit(r: dict, nm: str = name) -> bool:
            if nm == "scope_or_def":
                return r["co_scope"] > 0 or r["contested"] > 0
            return r[nm] > 0

        tp = sum(1 for r in B if hit(r))
        fp = sum(1 for r in A if hit(r))
        fires = (tp + fp) / len(conf)
        prec = tp / max(tp + fp, 1)
        p = fisher(tp, len(B) - tp, fp, len(A) - fp)
        print(
            f"{name:<14}{100 * tp / len(B):>10.0f}%{100 * fp / len(A):>8.0f}%"
            f"{100 * prec:>6.0f}%{100 * fires:>7.0f}%{p:>11.4f}"
        )
        best.append((name, tp / len(B), prec, p))

    print("\n--- localisation: how much surface does a detector hand the integrator? ---")
    fired = [r for r in B if r["co_scope"] > 0 or r["contested"] > 0]
    if fired:
        ns = sum(len(r["scope_names"]) + len(r["def_names"]) for r in fired) / len(fired)
        nt = sum(r["n_touched_scopes"] for r in fired) / len(fired)
        nf = sum(r["n_touched_files"] for r in fired) / len(fired)
        print(f"  on the {len(fired)} B subsets it fires on:")
        print(f"    flagged scopes/symbols   {ns:5.2f}")
        print(f"    scopes touched in total  {nt:5.2f}   -> narrows to {100 * ns / max(nt, 1e-9):.0f}% of the scope surface")
        print(f"    files touched in total   {nf:5.2f}")

    print("\n--- does it scale with N? ---")
    for n in sorted({r["n"] for r in conf}):
        sub = [r for r in conf if r["n"] == n]
        bs = [r for r in sub if r["category"] == "B-shared-symbol"]
        tp = sum(1 for r in bs if r["co_scope"] > 0 or r["contested"] > 0)
        ns = sum(len(r["scope_names"]) + len(r["def_names"]) for r in bs) / max(len(bs), 1)
        nt = sum(r["n_touched_scopes"] for r in bs) / max(len(bs), 1)
        print(
            f"  N={n}: recall {100 * tp / max(len(bs), 1):3.0f}%   flagged {ns:5.2f} of {nt:5.2f} touched scopes"
            f"  ({100 * ns / max(nt, 1e-9):.0f}%)"
        )

    print("\n--- the residual: B subsets NO detector fires on ---")
    miss = [r for r in B if not (r["co_scope"] > 0 or r["contested"] > 0)]
    print(f"  {len(miss)}/{len(B)} = {100 * len(miss) / len(B):.0f}%")
    by = defaultdict(int)
    for r in miss:
        by[(r["repo"], r["task"])] += 1
    for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:8]:
        print(f"    {k[0][:28]:<28}{k[1]:<7}{v}")

    json.dump(out, open(DATA / "detect.json", "w"), indent=1)
    print(f"\nwrote {DATA / 'detect.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
