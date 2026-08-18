#!/usr/bin/env python3
"""Does arbitration cost saturate in N while pairwise cost grows ~N^2?

The claim behind the ownership framing is a complexity claim, not a statistical
one:

  * pairwise coordination is O(N^2) -- every pair of agents can collide, so the
    number of colliding pairs should grow like N^2;
  * ownership arbitration is bounded by the number of *contested symbols*, and a
    task's API surface is finite, so that count should flatten as N grows.

One artefact has to be controlled for or the answer inverts.  Tasks have 2-12
features, so at N=10 on a 10-feature task the subset *is* the whole task and
`contested_symbols` necessarily runs up to its population ceiling -- which looks
like super-linear growth but is just exhaustion.  The headline fit is therefore
restricted to subsets using at most half of a task's available features; the
exhausting range is printed alongside so the artefact stays visible.

If both grow like N^2, the ownership abstraction buys nothing at scale and the
whole direction is dead.  This tests it with zero compute, from the dataset's
gold patches -- no agent runs involved, so it is a property of the *benchmark*,
not of any model.

Symbol extraction is copied verbatim from the stale-premise analysis
(`../../2026-08-17-stale-premise/scripts/staleness.py`) so that "contested
symbol" here means the same thing as "absorption" there.
"""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import sys
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent.parent / "data"
SEED = 17
MAX_SUBSETS = 400  # per (task, N); C(12,6)=924 otherwise

# --- verbatim from staleness.py, so "contested" == "absorbed" ----------------
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

# --- extended: what the verbatim pair misses ---------------------------------
# The dataset is py 199 / go 35 / ts 25 / rs 11 touched files, and several
# features define nothing but module-level names (outlines task1655 adds
# `hex_str = Regex(...)`, `uuid4 = ...`, `ipv4 = ...` and scores zero above).
# Those are ownable symbols -- two agents both adding `hex_str` is a real
# collision -- so the exponents get re-fitted under this wider net as a
# robustness check.  If both nets agree, the conclusion is not an artefact of
# symbol extraction.
MODLEVEL = re.compile(r"^([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)", re.M)
JSDECL = re.compile(r"\b(?:function|const|let|interface|enum)\s+(\w+)")
RSDECL = re.compile(r"\b(?:fn|struct|impl|trait|enum|mod)\s+(\w+)")
EXTENDED = (DECL, KWARG, MODLEVEL, JSDECL, RSDECL)


def keep(sym: str) -> bool:
    return len(sym) > 3 and sym not in STOP and not sym.startswith("__")


def patch_symbols(patch: str, extended: bool = False) -> tuple[set[str], set[str]]:
    """(symbols this feature defines, files it touches), from ADDED lines only.

    Gold patches are already test-free (verified: 0/199 touch a test path), and
    `feature.filtered.patch` exists for only 2/199 features, so `feature.patch`
    is the normal input and needs no extra filtering.
    """
    added, files = [], set()
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:].strip())
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    text = "\n".join(added)
    syms = set()
    for pattern in EXTENDED if extended else (DECL, KWARG):
        for m in pattern.finditer(text):
            if keep(m.group(1)):
                syms.add(m.group(1))
    return syms, files


def load_tasks(extended: bool = False) -> list[dict]:
    tasks = []
    for repo in sorted(DATASET.iterdir()):
        if not repo.is_dir() or repo.name == "subsets":
            continue
        for task in sorted(repo.iterdir()):
            if not task.is_dir() or not task.name.startswith("task"):
                continue
            feats = {}
            for fd in sorted(task.iterdir()):
                if not (fd.is_dir() and fd.name.startswith("feature")):
                    continue
                p = fd / "feature.filtered.patch"
                if not p.exists():
                    p = fd / "feature.patch"
                if not p.exists():
                    continue
                fid = int(fd.name.replace("feature", ""))
                feats[fid] = patch_symbols(p.read_text(errors="replace"), extended)
            if len(feats) >= 2:
                tasks.append({"repo": repo.name, "task": task.name, "features": feats})
    return tasks


def subsets_of(ids: list[int], n: int, rng: random.Random) -> list[tuple[int, ...]]:
    total = math.comb(len(ids), n)
    if total <= MAX_SUBSETS:
        return list(combinations(ids, n))
    seen = set()
    while len(seen) < MAX_SUBSETS:
        seen.add(tuple(sorted(rng.sample(ids, n))))
    return sorted(seen)


def score(subset: tuple[int, ...], feats: dict) -> dict:
    """Pairwise vs arbitration cost for one subset of features."""
    colliding_pairs = sum(
        1 for a, b in combinations(subset, 2) if feats[a][0] & feats[b][0]
    )
    co_file_pairs = sum(1 for a, b in combinations(subset, 2) if feats[a][1] & feats[b][1])
    owners: dict[str, int] = {}
    for f in subset:
        for s in feats[f][0]:
            owners[s] = owners.get(s, 0) + 1
    contested = sum(1 for c in owners.values() if c > 1)
    return {
        "pool": len(feats),
        "pairs_total": math.comb(len(subset), 2),
        "colliding_pairs": colliding_pairs,
        "co_file_pairs": co_file_pairs,
        "contested_symbols": contested,
        "symbols_total": len(owners),
    }


def loglog_slope(xs: list[float], ys: list[float]) -> float:
    """Fitted exponent k in y ~ N^k."""
    pts = [(math.log(x), math.log(y)) for x, y in zip(xs, ys) if x > 0 and y > 0]
    if len(pts) < 2:
        return float("nan")
    mx = statistics.mean(p[0] for p in pts)
    my = statistics.mean(p[1] for p in pts)
    den = sum((p[0] - mx) ** 2 for p in pts)
    return sum((p[0] - mx) * (p[1] - my) for p in pts) / den if den else float("nan")


def sweep(tasks: list[dict], rng: random.Random) -> tuple[dict, list]:
    by_n: dict[int, list[dict]] = {}
    rows = []
    for t in tasks:
        ids = sorted(t["features"])
        for n in range(2, min(len(ids), 10) + 1):
            for sub in subsets_of(ids, n, rng):
                s = score(sub, t["features"])
                s.update(n=n, repo=t["repo"], task=t["task"])
                by_n.setdefault(n, []).append(s)
                rows.append(s)
    return by_n, rows


MAX_POOL_FRACTION = 0.5  # guard against the finite-population artefact


def report(by_n: dict, title: str, only: set | None = None, max_frac: float = 1.0) -> tuple[float, float]:
    print(f"\n--- {title} ---")
    print(f"{'N':>3}{'subsets':>9}{'tasks':>7}{'pairs':>8}{'colliding':>11}{'contested':>11}{'ratio':>8}")
    print("-" * 60)
    ns, coll, cont = [], [], []
    levels = {}
    for n in sorted(by_n):
        rs = by_n[n] if only is None else [r for r in by_n[n] if (r["repo"], r["task"]) in only]
        rs = [r for r in rs if r["n"] <= max_frac * r["pool"]]
        if rs:
            levels[n] = rs
    # Balanced composition: a level contributed by fewer tasks than the widest
    # level is a different population, not a larger N.  Fitting across it
    # measures the task swap, not the scaling.
    widest = max((len({(r["repo"], r["task"]) for r in rs}) for rs in levels.values()), default=0)
    dropped = [n for n, rs in levels.items() if len({(r["repo"], r["task"]) for r in rs}) < widest]
    if dropped:
        print(f"    (dropped unbalanced N levels {dropped}: fewer than {widest} tasks contribute)")
    for n, rs in levels.items():
        if n in dropped:
            continue
        ntasks = len({(r["repo"], r["task"]) for r in rs})
        c = statistics.mean(r["colliding_pairs"] for r in rs)
        sy = statistics.mean(r["contested_symbols"] for r in rs)
        pt = statistics.mean(r["pairs_total"] for r in rs)
        ns.append(n)
        coll.append(c)
        cont.append(sy)
        print(f"{n:>3}{len(rs):>9}{ntasks:>7}{pt:>8.1f}{c:>11.2f}{sy:>11.2f}{(c / sy if sy else 0):>8.2f}")
    kc, ks = loglog_slope(ns, coll), loglog_slope(ns, cont)
    # References must be fitted over the SAME N levels: a log-log slope on a
    # short range is biased, so C(N,2) over N=2..5 is 2.52, not 2.0.
    qr = loglog_slope(ns, [float(math.comb(n, 2)) for n in ns])
    lr = loglog_slope(ns, [float(n) for n in ns])
    print(
        f"  fitted   colliding pairs k={kc:.2f} (quad ref {qr:.2f})   "
        f"contested symbols k={ks:.2f} (linear ref {lr:.2f})"
    )
    return (kc, qr), (ks, lr)


def main() -> int:
    print("Log-log slopes are biased on short N ranges, so every fit below is")
    print("printed next to C(N,2) and N fitted over the SAME levels.  Compare")
    print("against those references, not against 2.0 and 1.0.")

    results = {}
    for mode in (False, True):
        rng = random.Random(SEED)
        tasks = load_tasks(extended=mode)
        label = "EXTENDED extractor" if mode else "VERBATIM extractor (comparable to absorption)"
        empty = [t for t in tasks if not any(sy for sy, _ in t["features"].values())]
        print(f"\n{'=' * 70}\n{label}   tasks={len(tasks)}  zero-symbol tasks={len(empty)}")
        for t in empty[:6]:
            print(f"    no symbols: {t['repo']}/{t['task']}")

        by_n, rows = sweep(tasks, rng)
        deep = {(t["repo"], t["task"]) for t in tasks if len(t["features"]) >= 10}
        kc, ks = report(
            by_n,
            f"HEADLINE: fixed composition (>={len(deep)} tasks with 10+ features), "
            f"N <= {MAX_POOL_FRACTION:.0%} of pool",
            deep,
            MAX_POOL_FRACTION,
        )
        report(by_n, "same tasks, unrestricted N (pool exhaustion visible at the top)", deep)
        results["extended" if mode else "verbatim"] = {"colliding": kc, "contested": ks}
        if mode:
            OUT.mkdir(parents=True, exist_ok=True)
            json.dump(rows, open(OUT / "saturation.json", "w"))

    print(f"\n{'=' * 70}\nVERDICT (headline range only)")
    for k, v in results.items():
        (kc, qr), (ks, lr) = v["colliding"], v["contested"]
        print(
            f"  {k:<9} colliding k={kc:.2f} vs quad ref {qr:.2f} ({kc - qr:+.2f})   "
            f"contested k={ks:.2f} vs linear ref {lr:.2f} ({ks - lr:+.2f})"
        )
    print(f"\nwrote {OUT / 'saturation.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
