#!/usr/bin/env python3
"""P2.5 stage 1 -- are a task's features semantically composable at all?

`gold_merge_scaling.py` showed naive merge of the GOLD patches conflicts at 61%
(N=2), 89% (N=3) and 100% (N>=4), and the evaluator scores a conflicted merge as
zero without running tests.  Before reading anything into that, we need to know
which kind of failure it is:

  A  mechanical      a different deterministic merge produces a tree that passes
  B  shared-symbol   needs semantic reconciliation of a symbol both features define
  C  incompatible    the features genuinely cannot coexist

C is settled here, cheaply and decisively.  Every task ships a `combined.patch`
-- the human-authored union of ALL its features.  If that tree passes EVERY
feature's test suite, then no two features of that task are semantically
incompatible, so C is empty for it and every gold conflict there is A or B.

Usage:
    python3 composability.py                 # all tasks with a local image
    python3 composability.py pallets_jinja_task/1621 ...
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from cooperbench.eval.backends import get_backend
from cooperbench.eval.sandbox import _filter_test_files, _run_tests, _write_patch
from cooperbench.utils import get_image_name

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent.parent / "data"


def local_images() -> set[str]:
    out = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def audit(repo: str, task: int) -> dict:
    d = DATASET / repo / f"task{task}"
    combined = (d / "combined.patch").read_text()
    fids = sorted(int(p.name.replace("feature", "")) for p in d.iterdir() if p.name.startswith("feature"))

    sb = get_backend("docker").create_sandbox(get_image_name(repo, task), 1800)
    try:
        _write_patch(sb, "combined.patch", _filter_test_files(combined))
        for f in fids:
            _write_patch(sb, f"tests{f}.patch", (d / f"feature{f}" / "tests.patch").read_text())

        base = sb.exec("bash", "-c", "cd /workspace/repo && git rev-parse HEAD")
        base_sha = base.stdout_read().strip().splitlines()[-1]

        per = {}
        for f in fids:
            r = _run_tests(sb, f"tests{f}.patch", "combined.patch", base_sha)
            per[f] = {
                "passed": r["passed"],
                "tests_passed": r["tests_passed"],
                "tests_failed": r["tests_failed"],
            }
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"    feature{f:<3} {mark}  ({r['tests_passed']}p/{r['tests_failed']}f)")
        return {
            "repo": repo,
            "task": task,
            "n_features": len(fids),
            "per_feature": per,
            "all_pass": all(v["passed"] for v in per.values()),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001
        return {"repo": repo, "task": task, "error": str(e), "all_pass": False, "per_feature": {}}
    finally:
        try:
            sb.terminate()
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    imgs = local_images()
    wanted = []
    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            repo, task = a.split("/")
            wanted.append((repo, int(task.replace("task", ""))))
    else:
        for repo_dir in sorted(DATASET.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name == "subsets":
                continue
            for td in sorted(repo_dir.iterdir()):
                if td.is_dir() and td.name.startswith("task"):
                    t = int(td.name.replace("task", ""))
                    if get_image_name(repo_dir.name, t) in imgs:
                        wanted.append((repo_dir.name, t))

    print(f"auditing {len(wanted)} tasks with local images\n")
    rows = []
    for repo, task in wanted:
        print(f"{repo}/task{task}:")
        r = audit(repo, task)
        rows.append(r)
        if r.get("error"):
            print(f"    ERROR: {r['error'][:160]}")
        else:
            n_fail = sum(1 for v in r["per_feature"].values() if not v["passed"])
            print(f"  -> {'ALL COMPOSABLE' if r['all_pass'] else f'{n_fail} feature(s) FAIL under combined.patch'}")
        OUT.mkdir(parents=True, exist_ok=True)
        json.dump(rows, open(OUT / "composability.json", "w"), indent=1)

    ok = [r for r in rows if r.get("all_pass")]
    bad = [r for r in rows if not r.get("all_pass") and not r.get("error")]
    print(f"\n{'=' * 60}")
    print(f"tasks where combined.patch passes EVERY feature suite: {len(ok)}/{len(rows) - sum(1 for r in rows if r.get('error'))}")
    for r in bad:
        fails = [f for f, v in r["per_feature"].items() if not v["passed"]]
        print(f"  NOT composable: {r['repo']}/task{r['task']} -> features {fails} fail")
    print("\nWhere combined.patch passes everything, no two features of that task are")
    print("semantically incompatible, so every gold merge conflict there is either")
    print("mechanical (A) or shared-symbol reconciliation (B) -- never (C).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
