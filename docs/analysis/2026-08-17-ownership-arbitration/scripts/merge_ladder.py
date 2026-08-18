#!/usr/bin/env python3
"""P2.5 stage 2 -- is a gold conflict mechanical, or a real shared-symbol clash?

Stage 1 (`composability.py`) ruled out semantic incompatibility: `combined.patch`
passes every feature suite, so the features of a task DO coexist.  Whatever the
naive merge is choking on is therefore recoverable in principle.  This stage asks
how much of it a *deterministic* strategy recovers, with no model in the loop:

  naive        git merge                        (what the evaluator does today)
  patience     git merge -X patience
  histogram    git merge -X histogram
  apply-plain  git apply each patch in turn onto one branch
  apply-3way   git apply --3way each patch in turn
  apply-rev    apply-3way in reverse feature order
  union        git merge with `* merge=union`   (keeps BOTH sides of every hunk)

`union` is the interesting rung.  It never reports a conflict -- it concatenates
the two sides -- so it is a pure test of the mechanical hypothesis: if the two
features really were editing the same region for unrelated reasons, keeping both
sides yields a working tree; if they were reconciling the *same* symbol, keeping
both sides yields duplicate/contradictory definitions and the suites fail.  That
is why every rung is scored on tests, not on exit status.

A strategy only counts as a fix if the resulting tree passes EVERY feature's test
suite -- a clean tree that fails tests is not a merge, it is data loss.  So:

  A mechanical     some deterministic strategy yields a tree passing all suites
  B shared-symbol  none does; the clash needs semantic reconciliation

and we check whether B correlates with *contested symbols* (a symbol defined by
more than one feature's gold patch), which is what an ownership index would flag.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

from cooperbench.eval.backends import get_backend
from cooperbench.eval.sandbox import _filter_test_files, _run_tests, _setup_branches, _write_patch
from cooperbench.utils import get_image_name

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent.parent / "data"
SEED = 17
PER_N = int(os.environ.get("PER_N", "5"))

# The 3-task pilot ran all 7 rungs on 30 subsets: `union` rescued 12/12 of the
# recoverable conflicts and patience/histogram/apply-* rescued 0.  FAST=1 keeps
# only the two rungs that ever mattered, which is what makes a 20-task sweep
# affordable.
FAST = os.environ.get("FAST") == "1"
OUTFILE = os.environ.get("OUTFILE", "merge_ladder.json")

PILOT_TASKS = [
    ("pallets_jinja_task", 1621),
    ("pallets_click_task", 2068),
    ("dottxt_ai_outlines_task", 1655),
]


def local_tasks() -> list[tuple[str, int]]:
    """Every dataset task whose image is already pulled -- category turned out to be
    almost a function of the task, so breadth across tasks matters more than depth."""
    out = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], capture_output=True, text=True
    ).stdout
    imgs = {ln.strip() for ln in out.splitlines() if ln.strip()}
    found = []
    for repo_dir in sorted(DATASET.iterdir()):
        if not repo_dir.is_dir() or repo_dir.name == "subsets":
            continue
        for td in sorted(repo_dir.iterdir()):
            if td.is_dir() and td.name.startswith("task"):
                t = int(td.name.replace("task", ""))
                if get_image_name(repo_dir.name, t) in imgs:
                    found.append((repo_dir.name, t))
    return found

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


def defined_symbols(patch: str) -> set[str]:
    added = "\n".join(
        line[1:] for line in patch.splitlines() if line.startswith("+") and not line.startswith("+++")
    )
    out = set()
    for pat in (DECL, KWARG, MODLEVEL):
        for m in pat.finditer(added):
            s = m.group(1)
            if len(s) > 3 and s not in STOP and not s.startswith("__"):
                out.add(s)
    return out


MERGE_STRATEGIES = {
    "naive": "",
    "patience": "-X patience",
    "histogram": "-X histogram",
}


# Every rung must start from an identical tree.  Merging while HEAD is on `agent1`
# would commit onto that branch and hand the next rung a polluted starting point,
# so each rung works on a disposable `trial` branch re-cut from agent1.
_RESET = """
git merge --abort >/dev/null 2>&1 || true
git reset --hard >/dev/null 2>&1
git clean -fdq >/dev/null 2>&1
git checkout -f -B trial agent1 >/dev/null 2>&1
"""


def _merge_script(n: int, opt: str) -> str:
    steps = "\n".join(
        f"""
if [ "$FAILED" = "" ]; then
    if git merge {opt} agent{i} --no-commit --no-ff >/dev/null 2>&1; then
        git commit -m m{i} >/dev/null 2>&1 || true
    else
        git diff --name-only --diff-filter=U | sed 's/^/CONFLICT_FILE=/'
        git merge --abort 2>/dev/null || true
        FAILED=agent{i}
    fi
fi"""
        for i in range(2, n + 1)
    )
    return f"""
cd /workspace/repo
{_RESET}
FAILED=""
{steps}
if [ "$FAILED" = "" ]; then echo RESULT=clean; else echo RESULT=conflict; echo AT=$FAILED; fi
"""


def _union_script(n: int) -> str:
    """Sequential merge with a repo-wide union driver -- keeps both sides, never conflicts."""
    steps = "\n".join(
        f"""
if [ "$FAILED" = "" ]; then
    if git merge agent{i} --no-commit --no-ff >/dev/null 2>&1; then
        git commit -m u{i} >/dev/null 2>&1 || true
    else
        git diff --name-only --diff-filter=U | sed 's/^/CONFLICT_FILE=/'
        git merge --abort 2>/dev/null || true
        FAILED=agent{i}
    fi
fi"""
        for i in range(2, n + 1)
    )
    return f"""
cd /workspace/repo
{_RESET}
echo "* merge=union" >> .gitattributes
git add .gitattributes && git commit -m attrs >/dev/null 2>&1
FAILED=""
{steps}
rm -f .gitattributes
git add -A && git commit -m unattrs >/dev/null 2>&1 || true
if [ "$FAILED" = "" ]; then echo RESULT=clean; else echo RESULT=conflict; echo AT=$FAILED; fi
"""


def _apply_script(order: list[int], base_sha: str, three_way: bool) -> str:
    flag = "--3way" if three_way else ""
    steps = "\n".join(
        f"""
if [ "$FAILED" = "" ]; then
    if ! git apply {flag} /patches/patch{i}.patch >/dev/null 2>&1; then
        echo "CONFLICT_FILE=patch{i}"
        FAILED=agent{i}
    else
        git add -A && git commit -m a{i} --allow-empty >/dev/null 2>&1
    fi
fi"""
        for i in order
    )
    return f"""
cd /workspace/repo
git checkout --force {base_sha} >/dev/null 2>&1
git checkout -B seqapply >/dev/null 2>&1
git reset --hard {base_sha} >/dev/null 2>&1
git clean -fd >/dev/null 2>&1
FAILED=""
{steps}
if [ "$FAILED" = "" ]; then
    git add -A && git commit -m seq --allow-empty >/dev/null 2>&1
    git diff {base_sha} HEAD > /patches/merged.patch
    echo RESULT=clean
else
    echo RESULT=conflict; echo AT=$FAILED
fi
"""


def audit_subset(repo: str, task: int, fids: list[int]) -> dict:
    d = DATASET / repo / f"task{task}"
    raw = {f: (d / f"feature{f}" / "feature.patch").read_text() for f in fids}
    patches = [_filter_test_files(raw[f]) for f in fids]
    syms = {f: defined_symbols(raw[f]) for f in fids}
    owners: dict[str, int] = defaultdict(int)
    for f in fids:
        for s in syms[f]:
            owners[s] += 1
    contested = sorted(s for s, c in owners.items() if c > 1)

    n = len(fids)
    sb = get_backend("docker").create_sandbox(get_image_name(repo, task), 1800)
    row: dict = {
        "repo": repo,
        "task": task,
        "features": fids,
        "n": n,
        "contested": contested,
        "n_contested": len(contested),
        "strategies": {},
    }
    try:
        for i, c in enumerate(patches, 1):
            _write_patch(sb, f"patch{i}.patch", c)
        for i, f in enumerate(fids, 1):
            _write_patch(sb, f"tests{i}.patch", (d / f"feature{f}" / "tests.patch").read_text())

        setup = _setup_branches(sb, n)
        if setup.get("error"):
            row["error"] = setup["error"]
            return row
        base_sha = setup["base_sha"]
        row["applied"] = all(v == "applied" for v in setup["apply_status"].values())

        if FAST:
            plans = [("naive", _merge_script(n, "")), ("union", _union_script(n))]
        else:
            plans = [(name, _merge_script(n, opt)) for name, opt in MERGE_STRATEGIES.items()]
            plans.append(("apply-plain", _apply_script(list(range(1, n + 1)), base_sha, False)))
            plans.append(("apply-3way", _apply_script(list(range(1, n + 1)), base_sha, True)))
            plans.append(("apply-rev", _apply_script(list(range(n, 0, -1)), base_sha, True)))
            plans.append(("union", _union_script(n)))  # last: most destructive rung

        for name, script in plans:
            res = sb.exec("bash", "-c", script)
            out = res.stdout_read() + res.stderr_read()
            clean = "RESULT=clean" in out
            entry: dict = {
                "clean": clean,
                "conflict_files": sorted(
                    {m.split("=", 1)[1].strip() for m in re.findall(r"CONFLICT_FILE=\S+", out)}
                ),
            }
            if clean:
                # the apply-* rungs write merged.patch themselves; merge rungs need it taken here
                if not name.startswith("apply-"):
                    sb.exec(
                        "bash",
                        "-c",
                        f"cd /workspace/repo && git diff {base_sha} HEAD > /patches/merged.patch",
                    )
                results = [
                    _run_tests(sb, f"tests{i}.patch", "merged.patch", base_sha) for i in range(1, n + 1)
                ]
                entry["all_pass"] = all(r["passed"] for r in results)
                entry["per_feature"] = [r["passed"] for r in results]
            row["strategies"][name] = entry
            if entry.get("all_pass"):
                break  # first strategy that fully works is enough
        fixers = [k for k, v in row["strategies"].items() if v.get("all_pass")]
        row["fixed_by"] = fixers
        row["category"] = "A-mechanical" if fixers else "B-shared-symbol"
        if row["strategies"].get("naive", {}).get("all_pass"):
            row["category"] = "clean-already"
        return row
    except Exception as e:  # noqa: BLE001
        row["error"] = str(e)
        return row
    finally:
        try:
            sb.terminate()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    rng = random.Random(SEED)
    ns = [int(a) for a in sys.argv[1:]] or [2, 3]
    rows = []
    tasks = local_tasks() if os.environ.get("ALL") == "1" else PILOT_TASKS
    for repo, task in tasks:
        d = DATASET / repo / f"task{task}"
        fids = sorted(int(p.name.replace("feature", "")) for p in d.iterdir() if p.name.startswith("feature"))
        for n in ns:
            subs = list(combinations(fids, n))
            subs = rng.sample(subs, min(PER_N, len(subs)))
            for sub in subs:
                r = audit_subset(repo, task, list(sub))
                rows.append(r)
                print(
                    f"  {repo}/{task} N={n} {list(sub)}: {r.get('category', 'ERR')}"
                    f"  fixed_by={r.get('fixed_by')}  contested={r.get('n_contested')}",
                    flush=True,
                )
                OUT.mkdir(parents=True, exist_ok=True)
                json.dump(rows, open(OUT / OUTFILE, "w"), indent=1)

    ok = [r for r in rows if not r.get("error")]
    print(f"\n{'=' * 68}\nN={ns}  audited={len(ok)}")
    cats = defaultdict(int)
    for r in ok:
        cats[r["category"]] += 1
    for k, v in sorted(cats.items()):
        print(f"  {k:<18}{v:>4}  {100 * v / max(len(ok), 1):>5.0f}%")

    print("\nwhich strategy rescued a conflict:")
    won = defaultdict(int)
    for r in ok:
        if r["category"] == "A-mechanical":
            won[r["fixed_by"][0]] += 1
    for k, v in sorted(won.items(), key=lambda kv: -kv[1]):
        print(f"  {k:<14}{v:>4}")

    print("\ncontested symbols vs category:")
    for cat in sorted(cats):
        rs = [r for r in ok if r["category"] == cat]
        if not rs:
            continue
        avg = sum(r["n_contested"] for r in rs) / len(rs)
        withc = sum(1 for r in rs if r["n_contested"] > 0)
        print(f"  {cat:<18} mean contested={avg:5.2f}   have>=1: {withc}/{len(rs)} = {100 * withc / len(rs):.0f}%")

    json.dump(rows, open(OUT / OUTFILE, "w"), indent=1)
    print(f"\nwrote {OUT / OUTFILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
