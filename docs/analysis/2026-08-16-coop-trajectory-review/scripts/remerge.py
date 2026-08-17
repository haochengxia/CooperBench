#!/usr/bin/env python3
"""How much of the coop->solo gap is recoverable by merging better?

The evaluator's coop path (``eval/sandbox.py:225``) runs ONE merge strategy --
``git merge agent1`` from branch ``agent2``, both branched off the pristine
base.  That is already a real 3-way recursive merge; the name "naive" is
misleading.  What is not obvious is what happens when it conflicts:

    else:
        echo "MERGE_STATUS=conflicts"
        git merge --abort            # <- the tree is thrown away

...and the caller then hardcodes

    test1_result = {"passed": False, ..., "tests_passed": 0, "tests_failed": 0}
    test2_result = {"passed": False, ..., "tests_passed": 0, "tests_failed": 0}

so a conflicted pair is scored zero WITHOUT ANY TEST BEING RUN.  The single
escape hatch requires agent1's patch *alone* to pass BOTH feature suites --
near-impossible in coop, where agent1 was only ever assigned feature 1.

This script asks the counterfactual the benchmark never asks: if you try
harder to merge, do the tests pass?  It replays each recorded pair through an
escalation ladder and runs the real held-out suites on whatever comes out.

The ladder is deliberately restricted to DETERMINISTIC, semantics-preserving
strategies -- no model is involved, nothing is hand-resolved:

    L0 naive       git merge                     (reproduces the evaluator)
    L1 patience    git merge -X diff-algorithm=patience
    L2 histogram   git merge -X diff-algorithm=histogram
    L3 apply3      git apply -3 patch1; git apply -3 patch2, sequentially

L1-L3 cannot invent a resolution for a genuine disagreement: if the two agents
really wrote incompatible code, every rung still conflicts.  A conflict that
evaporates under a different diff algorithm was never a semantic conflict at
all -- it was the default Myers diff picking unlucky hunk boundaries.

Usage:
    uv run python scripts/remerge.py            # conflicted pairs only
    uv run python scripts/remerge.py --all      # every pair (control)
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cooperbench.eval.backends import get_backend  # noqa: E402
from cooperbench.eval.sandbox import (  # noqa: E402
    _filter_test_files,
    _run_tests,
    _setup_branches,
    _write_patch,
)
from cooperbench.utils import get_image_name  # noqa: E402

DATASET = REPO_ROOT / "dataset"

# (name, shell snippet).  Each runs from a clean `agent2` checkout and must
# leave the merged tree committed on HEAD, or fail without side effects.
LADDER = [
    ("naive", "git merge agent1 --no-commit --no-ff"),
    ("patience", "git merge -X diff-algorithm=patience agent1 --no-commit --no-ff"),
    ("histogram", "git merge -X diff-algorithm=histogram agent1 --no-commit --no-ff"),
]


def _merge_rung(sb, base_sha: str, snippet: str) -> bool:
    """Try one merge rung.  Returns True and leaves merged.patch on success."""
    script = f"""
cd /workspace/repo
rm -f .git/index.lock
git merge --abort 2>/dev/null || true
git checkout --force agent2 2>&1 >/dev/null
git reset --hard agent2 2>&1 >/dev/null
if {snippet} >/dev/null 2>&1; then
    git add -A
    git commit -m 'merged' --allow-empty >/dev/null 2>&1
    git diff {base_sha} HEAD > /patches/merged.patch
    echo "RUNG_OK"
else
    git merge --abort 2>/dev/null || true
    echo "RUNG_CONFLICT"
fi
"""
    out = sb.exec("bash", "-c", script)
    return "RUNG_OK" in (out.stdout_read() + out.stderr_read())


def _apply3_rung(sb, base_sha: str) -> bool:
    """Sequential 3-way patch application from the pristine base."""
    script = f"""
cd /workspace/repo
rm -f .git/index.lock
git merge --abort 2>/dev/null || true
git checkout --force {base_sha} 2>&1 >/dev/null
git branch -D apply3 2>/dev/null || true
git checkout -b apply3 2>&1 >/dev/null
if git apply -3 /patches/patch1.patch 2>/dev/null \\
   && git apply -3 /patches/patch2.patch 2>/dev/null; then
    if grep -rIl '^<<<<<<< ' --exclude-dir=.git . | grep -q .; then
        echo "RUNG_CONFLICT"     # markers left in tree -- refuse to grade it
    else
        git add -A
        git commit -m 'merged' --allow-empty >/dev/null 2>&1
        git diff {base_sha} HEAD > /patches/merged.patch
        echo "RUNG_OK"
    fi
else
    git checkout --force . 2>/dev/null || true
    echo "RUNG_CONFLICT"
fi
"""
    out = sb.exec("bash", "-c", script)
    return "RUNG_OK" in (out.stdout_read() + out.stderr_read())


def _conflict_detail(sb, base_sha: str) -> dict:
    """Which files conflict under the default merge, and how badly."""
    script = """
cd /workspace/repo
rm -f .git/index.lock
git merge --abort 2>/dev/null || true
git checkout --force agent2 2>&1 >/dev/null
git reset --hard agent2 2>&1 >/dev/null
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
echo "---FILES---"
git diff --name-only --diff-filter=U
echo "---MARKERS---"
grep -rc '^<<<<<<< ' --exclude-dir=.git . 2>/dev/null | grep -v ':0$' || true
git merge --abort 2>/dev/null || true
"""
    out = sb.exec("bash", "-c", script)
    text = out.stdout_read() + out.stderr_read()
    files = markers = ""
    if "---FILES---" in text:
        rest = text.split("---FILES---", 1)[1]
        files, _, markers = rest.partition("---MARKERS---")
    hunks = 0
    for line in markers.splitlines():
        if ":" in line:
            try:
                hunks += int(line.rsplit(":", 1)[1])
            except ValueError:
                pass
    return {
        "conflicted_files": [f.strip() for f in files.splitlines() if f.strip()],
        "conflict_hunks": hunks,
    }


def replay(pair: dict, timeout: int = 900) -> dict:
    """Run one pair up the ladder, scoring with the real held-out suites."""
    repo, task = pair["repo"], pair["task_id"]
    f1, f2 = pair["feature1"], pair["feature2"]
    task_dir = DATASET / repo / f"task{task}"
    out: dict = {**pair, "rung": None, "feature1_passed": False, "feature2_passed": False}

    sb = None
    try:
        sb = get_backend("docker").create_sandbox(get_image_name(repo, task), timeout)
        _write_patch(sb, "patch1.patch", _filter_test_files(Path(pair["patch1"]).read_text()))
        _write_patch(sb, "patch2.patch", _filter_test_files(Path(pair["patch2"]).read_text()))
        _write_patch(sb, "tests1.patch", (task_dir / f"feature{f1}" / "tests.patch").read_text())
        _write_patch(sb, "tests2.patch", (task_dir / f"feature{f2}" / "tests.patch").read_text())

        setup = _setup_branches(sb)
        if setup.get("error"):
            out["error"] = setup["error"]
            return out
        base_sha = setup["base_sha"]
        out["apply_status"] = setup["apply_status"]

        for name, snippet in LADDER:
            if _merge_rung(sb, base_sha, snippet):
                out["rung"] = name
                break
        else:
            if _apply3_rung(sb, base_sha):
                out["rung"] = "apply3"

        if out["rung"] is None:
            # Still conflicts at every rung -- a genuine disagreement.
            out.update(_conflict_detail(sb, base_sha))
            return out

        if out["rung"] != "naive":
            out.update(_conflict_detail(sb, base_sha))

        t1 = _run_tests(sb, "tests1.patch", "merged.patch", base_sha)
        t2 = _run_tests(sb, "tests2.patch", "merged.patch", base_sha)
        out["feature1_passed"] = bool(t1["passed"])
        out["feature2_passed"] = bool(t2["passed"])
        out["tests"] = {
            "f1": [t1.get("tests_passed", 0), t1.get("tests_failed", 0)],
            "f2": [t2.get("tests_passed", 0), t2.get("tests_failed", 0)],
        }
        return out
    except Exception as exc:  # noqa: BLE001 - one bad pair must not kill the sweep
        out["error"] = f"{exc}\n{traceback.format_exc()[-600:]}"
        return out
    finally:
        out["both_passed"] = out["feature1_passed"] and out["feature2_passed"]
        if sb is not None:
            try:
                sb.terminate()
            except Exception:  # noqa: BLE001
                pass


def collect(run_dir: Path, only_conflicts: bool) -> list[dict]:
    pairs = []
    for ev in sorted(run_dir.rglob("eval.json")):
        d = json.loads(ev.read_text())
        status = (d.get("merge") or {}).get("status")
        if only_conflicts and status != "conflicts":
            continue
        res = json.loads((ev.parent / "result.json").read_text())
        agents = res["agents"]
        f1 = agents["agent1"]["feature_id"]
        f2 = agents["agent2"]["feature_id"]
        p1, p2 = ev.parent / f"agent{f1}.patch", ev.parent / f"agent{f2}.patch"
        if not (p1.exists() and p2.exists()):
            continue
        pairs.append(
            {
                "dir": str(ev.parent),
                "repo": res["repo"],
                "task_id": res["task_id"],
                "feature1": f1,
                "feature2": f2,
                "patch1": str(p1),
                "patch2": str(p2),
                "original_status": status,
                "original_both_passed": bool(d.get("both_passed")),
            }
        )
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="logs/net-coop")
    ap.add_argument("--out", default="data/remerge.json")
    ap.add_argument("--all", action="store_true", help="replay every pair, not just conflicts")
    ap.add_argument("-c", "--concurrency", type=int, default=4)
    args = ap.parse_args()

    pairs = collect(REPO_ROOT / args.run, only_conflicts=not args.all)
    print(f"replaying {len(pairs)} pairs from {args.run}\n")

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(replay, p): p for p in pairs}
        for i, fut in enumerate(as_completed(futures), 1):
            r = fut.result()
            results.append(r)
            flag = "PASS" if r["both_passed"] else "fail"
            rung = r["rung"] or "UNRESOLVED"
            err = f"  ERR {r['error'][:60]}" if r.get("error") else ""
            print(f"[{i:>3}/{len(pairs)}] {rung:<10} {flag}  {r['repo']}/{r['task_id']}{err}")

    out_path = Path(__file__).resolve().parent.parent / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=1))
    print(f"\nwrote {out_path}")

    resolved = [r for r in results if r["rung"]]
    print(f"\nresolved {len(resolved)}/{len(results)} by rung:")
    for name in ("naive", "patience", "histogram", "apply3"):
        g = [r for r in resolved if r["rung"] == name]
        if g:
            print(f"  {name:<10} {len(g):>3} merged, {sum(r['both_passed'] for r in g):>3} both-pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
