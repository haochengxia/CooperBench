#!/usr/bin/env python3
"""L4: can a model resolve the conflicts the deterministic ladder cannot?

``remerge.py`` shows a 0/16 recovery from better merge algorithms -- the 35
conflict hunks are genuine overlapping edits.  ``conflicts.py`` shows they are
also *small*: median largest side 3 lines, 34% single-line, and the recurring
shape is two agents independently wrapping the same call site.

This measures the obvious repair: hand the conflicted file to a model, tell it
what both features are supposed to do, and ask it to produce a version that
preserves both.  Then score it with the real held-out suites.

Deliberate constraints, so the number means something:

  * The repairer NEVER sees the held-out tests.  It resolves from the two
    feature specs and the code alone.  Otherwise this measures test-fitting.
  * SINGLE SHOT.  No retry loop, no test feedback.  A repair loop with the
    graded suite in it would be an oracle, and a loop with the agents' own
    tests is a different (larger) experiment.
  * It may only touch files that actually conflicted, and the result must
    contain no conflict markers.

So this is a lower bound on what merge repair is worth.

Usage:  uv run python scripts/repair.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remerge  # noqa: E402

import cooperbench.eval.sandbox as S  # noqa: E402
from cooperbench.agents._azure import azure_litellm_model, resolve_azure_config  # noqa: E402
from cooperbench.eval.backends import get_backend  # noqa: E402
from cooperbench.utils import get_image_name  # noqa: E402

MODEL = os.environ.get("MODEL", "gpt-5.6-luna")

PROMPT = """Two engineers worked on the same repository at the same time, each \
implementing one feature independently. Their branches have now been merged and \
one or more files contain git conflict markers.

Your job is to resolve every conflict so that BOTH features work. This is not a \
choice between two alternatives -- both engineers' work must survive. The usual \
shape is that both sides wrapped or replaced the same call site; the correct \
resolution normally composes the two changes rather than picking one.

## Feature A (implemented by engineer A)
{spec1}

## Feature B (implemented by engineer B)
{spec2}

## Conflicted file: {path}
```
{content}
```

Return the COMPLETE resolved contents of `{path}`, and nothing else, inside a \
single fenced code block. No conflict markers may remain. Do not add, remove or \
rename anything unrelated to resolving the conflicts."""

DUMP_CONFLICTED = r"""
cd /workspace/repo
git checkout --force agent2 >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
git diff --name-only --diff-filter=U
"""


def _extract_code(text: str) -> str | None:
    blocks = re.findall(r"```(?:[a-zA-Z0-9_+-]*)\n(.*?)```", text, re.DOTALL)
    if not blocks:
        return None
    body = max(blocks, key=len)
    return body if "<<<<<<<" not in body else None


def _ask(prompt: str) -> str:
    import litellm

    az = resolve_azure_config()
    kwargs: dict = {"messages": [{"role": "user", "content": prompt}]}
    if az:
        kwargs |= {
            "model": azure_litellm_model(MODEL),
            "api_base": az["endpoint"],
            "api_key": az["api_key"],
        }
    else:
        kwargs["model"] = MODEL
    return litellm.completion(**kwargs).choices[0].message.content or ""


def repair(pair: dict, timeout: int = 900) -> dict:
    repo, task = pair["repo"], pair["task_id"]
    f1, f2 = pair["feature1"], pair["feature2"]
    task_dir = remerge.DATASET / repo / f"task{task}"
    out: dict = {**pair, "repaired": False, "feature1_passed": False, "feature2_passed": False}

    sb = None
    try:
        sb = get_backend("docker").create_sandbox(get_image_name(repo, task), timeout)
        S._write_patch(sb, "patch1.patch", S._filter_test_files(Path(pair["patch1"]).read_text()))
        S._write_patch(sb, "patch2.patch", S._filter_test_files(Path(pair["patch2"]).read_text()))
        S._write_patch(sb, "tests1.patch", (task_dir / f"feature{f1}" / "tests.patch").read_text())
        S._write_patch(sb, "tests2.patch", (task_dir / f"feature{f2}" / "tests.patch").read_text())

        setup = S._setup_branches(sb)
        base_sha = setup["base_sha"]

        files = [f for f in sb.exec("bash", "-c", DUMP_CONFLICTED).stdout_read().splitlines() if f.strip()]
        out["conflicted_files"] = files
        if not files:
            out["error"] = "no conflicted files"
            return out

        spec1 = (task_dir / f"feature{f1}" / "feature.md").read_text()
        spec2 = (task_dir / f"feature{f2}" / "feature.md").read_text()

        for path in files:
            content = sb.exec("cat", f"/workspace/repo/{path}").stdout_read()
            # agent1 is the "theirs" side of the merge, i.e. feature1.
            reply = _ask(
                PROMPT.format(spec1=spec1, spec2=spec2, path=path, content=content)
            )
            resolved = _extract_code(reply)
            if resolved is None:
                out["error"] = f"no clean code block for {path}"
                return out
            S._write_patch(sb, "resolved.txt", resolved)
            sb.exec("bash", "-c", f"cp /patches/resolved.txt /workspace/repo/{path}")

        check = sb.exec(
            "bash",
            "-c",
            "cd /workspace/repo && grep -rl '^<<<<<<< ' --exclude-dir=.git . | head -1",
        ).stdout_read()
        if check.strip():
            out["error"] = f"markers remain in {check.strip()}"
            return out

        made = sb.exec(
            "bash",
            "-c",
            f"cd /workspace/repo && git add -A && git commit -m repaired --allow-empty >/dev/null 2>&1 "
            f"&& git diff {base_sha} HEAD > /patches/merged.patch && echo OK",
        ).stdout_read()
        if "OK" not in made:
            out["error"] = "failed to build merged.patch"
            return out
        out["repaired"] = True

        t1 = S._run_tests(sb, "tests1.patch", "merged.patch", base_sha)
        t2 = S._run_tests(sb, "tests2.patch", "merged.patch", base_sha)
        out["feature1_passed"] = bool(t1["passed"])
        out["feature2_passed"] = bool(t2["passed"])
        out["tests"] = {
            "f1": [t1.get("tests_passed", 0), t1.get("tests_failed", 0)],
            "f2": [t2.get("tests_passed", 0), t2.get("tests_failed", 0)],
        }
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:300]
        return out
    finally:
        out["both_passed"] = out["feature1_passed"] and out["feature2_passed"]
        if sb is not None:
            try:
                sb.terminate()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    pairs = remerge.collect(remerge.REPO_ROOT / "logs/net-coop", only_conflicts=True)
    print(f"repairing {len(pairs)} conflicted pairs with {MODEL}\n")
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(repair, p): p for p in pairs}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            state = "PASS" if r["both_passed"] else ("partial" if r["feature1_passed"] or r["feature2_passed"] else "fail")
            err = f"  ERR {r['error'][:60]}" if r.get("error") else ""
            print(f"[{i:>3}/{len(pairs)}] {'repaired' if r['repaired'] else 'NOFIX':<9} {state:<8} "
                  f"{r['repo']}/{r['task_id']} f{r['feature1']}_f{r['feature2']}{err}")

    out = Path(__file__).resolve().parent.parent / "data" / "repair.json"
    out.write_text(json.dumps(results, indent=1))

    n = len(results)
    rep = sum(r["repaired"] for r in results)
    both = sum(r["both_passed"] for r in results)
    any_f = sum(r["feature1_passed"] or r["feature2_passed"] for r in results)
    print(f"\nresolved to a clean tree : {rep}/{n}")
    print(f"both features pass       : {both}/{n}")
    print(f"at least one feature     : {any_f}/{n}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
