#!/usr/bin/env python3
"""L5: an agentic reconciliation round on the conflicted tree.

``repair.py`` measured the cheapest possible fix -- one blind model call per
conflicted file, no tools, no build, no tests: 4/16 pairs recovered.  Six of the
twelve failures produced trees that did not build at all, which is exactly what
you would expect from editing code you cannot run.

This measures the next rung up, and the one actually worth shipping: give the
conflict to a *live agent* in a container, with the repository, a shell, and the
repo's own test suite.  This is the "extra round" a real integrator does.

Faithfulness note: in a live run this round would go to the agent that finished
LAST, keeping its context -- it already knows what it built and why.  That is not
reproducible retrospectively, because the recorded runs carry no per-agent
timestamps (``*_traj.json`` has no time fields) and the agents' containers are
destroyed when ``runner.run()`` returns.  So this runs a FRESH agent on the
conflict.  It therefore measures the value of *the round plus tools*, not the
value of carrying the finisher's context -- which should only add to it.  Read
the number as a lower bound on the proposal.

Honesty constraints:
  * The held-out grading suites live in /patches and are never mounted into the
    repo; commands touching /patches are refused outright.
  * The agent may not edit any test file.
  * It starts from the real conflicted merge -- markers in the working tree,
    exactly what a developer sees -- not from a summary of it.

Instrumented failure mode: "clobbering" -- resolving the conflict by deleting the
partner's work instead of composing it.  Every identifier each side introduced is
checked for survival in the final tree.

Usage:  uv run python scripts/reconcile.py
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
MAX_STEPS = int(os.environ.get("MAX_STEPS", "20"))

# Leave the conflict markers in the tree: the state a developer actually sees.
SETUP = """
cd /workspace/repo
git checkout --force agent2 >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
git diff --name-only --diff-filter=U
"""

SYSTEM = """You are integrating two engineers' work.

Engineer A and engineer B each implemented one feature independently, from the \
same base commit, without seeing each other's final code. Their branches have \
been merged and the working tree at /workspace/repo now contains git conflict \
markers.

Your job: resolve every conflict so that BOTH features work.

This is NOT a choice between two alternatives. Both engineers' work must \
survive. The usual shape is that both sides wrapped or replaced the same call \
site; the correct resolution composes the two changes rather than picking one. \
Deleting one side's work to make the conflict go away is a failure, even though \
it produces a clean tree.

## Feature A
{spec1}

## Feature B
{spec2}

## Conflicted files
{files}

You have a shell in the container. Reply with exactly one command per turn, in a \
single fenced block:

```bash
your command here
```

You may read and edit source, and run the repository's existing tests to check \
yourself. You may NOT edit, add or delete any test file, and you may not read \
anything under /patches. When every conflict is resolved and no markers remain, \
reply with exactly:

```bash
echo TASK_COMPLETE
```
"""

BLOCKED = re.compile(r"/patches|tests?\.patch", re.I)
TESTFILE = re.compile(r"(^|/)(tests?/|test_|conftest\.py|.*_test\.(py|rs))", re.I)


def _ask(messages: list[dict]) -> str:
    import litellm

    az = resolve_azure_config()
    kw: dict = {"messages": messages}
    if az:
        kw |= {"model": azure_litellm_model(MODEL), "api_base": az["endpoint"], "api_key": az["api_key"]}
    else:
        kw["model"] = MODEL
    return litellm.completion(**kw).choices[0].message.content or ""


def _command(text: str) -> str | None:
    m = re.findall(r"```(?:bash|sh)?\n(.*?)```", text, re.DOTALL)
    return m[0].strip() if m else None


def _added_identifiers(patch_text: str) -> set[str]:
    """Names each side introduced -- used to detect clobbering."""
    names: set[str] = set()
    for line in patch_text.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        for m in re.finditer(r"\b(?:def|fn|class|struct)\s+([A-Za-z_][A-Za-z0-9_]*)", line):
            names.add(m.group(1))
    return names


def reconcile(pair: dict, timeout: int = 1800) -> dict:
    repo, task = pair["repo"], pair["task_id"]
    f1, f2 = pair["feature1"], pair["feature2"]
    td = remerge.DATASET / repo / f"task{task}"
    out: dict = {**pair, "resolved": False, "steps": 0,
                 "feature1_passed": False, "feature2_passed": False}

    sb = None
    try:
        sb = get_backend("docker").create_sandbox(get_image_name(repo, task), timeout)
        p1 = S._filter_test_files(Path(pair["patch1"]).read_text())
        p2 = S._filter_test_files(Path(pair["patch2"]).read_text())
        S._write_patch(sb, "patch1.patch", p1)
        S._write_patch(sb, "patch2.patch", p2)
        S._write_patch(sb, "tests1.patch", (td / f"feature{f1}" / "tests.patch").read_text())
        S._write_patch(sb, "tests2.patch", (td / f"feature{f2}" / "tests.patch").read_text())

        setup = S._setup_branches(sb)
        base_sha = setup["base_sha"]
        files = [f for f in sb.exec("bash", "-c", SETUP).stdout_read().splitlines() if f.strip()]
        out["conflicted_files"] = files
        if not files:
            out["error"] = "no conflicted files"
            return out

        prompt = SYSTEM.format(
            spec1=(td / f"feature{f1}" / "feature.md").read_text(),
            spec2=(td / f"feature{f2}" / "feature.md").read_text(),
            files="\n".join(f"- {f}" for f in files),
        )
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": "Begin. Inspect the conflicts first."}]

        for step in range(MAX_STEPS):
            out["steps"] = step + 1
            reply = _ask(messages)
            messages.append({"role": "assistant", "content": reply})
            cmd = _command(reply)
            if cmd is None:
                messages.append({"role": "user", "content":
                                 "Reply with exactly one command in a ```bash block."})
                continue
            if "TASK_COMPLETE" in cmd:
                break
            if BLOCKED.search(cmd):
                messages.append({"role": "user", "content":
                                 "Refused: the grading tests are off limits. Resolve from the code."})
                continue
            res = sb.exec("bash", "-c", f"cd /workspace/repo && {cmd}")
            obs = (res.stdout_read() + res.stderr_read())[:4000] or "(no output)"
            messages.append({"role": "user", "content": f"<output rc={res.returncode}>\n{obs}\n</output>"})

        # The agent must not have touched tests, and no markers may remain.
        touched = sb.exec("bash", "-c",
                          "cd /workspace/repo && git status --porcelain").stdout_read()
        bad = [ln[3:] for ln in touched.splitlines() if TESTFILE.search(ln[3:])]
        out["touched_test_files"] = bad
        markers = sb.exec("bash", "-c",
                          "cd /workspace/repo && grep -rl '^<<<<<<< ' --exclude-dir=.git . | head -3"
                          ).stdout_read().strip()
        if markers:
            out["error"] = f"markers remain: {markers}"
            return out

        made = sb.exec("bash", "-c",
                       f"cd /workspace/repo && git add -A && git commit -m reconciled --allow-empty "
                       f">/dev/null 2>&1 && git diff {base_sha} HEAD > /patches/merged.patch && echo OK"
                       ).stdout_read()
        if "OK" not in made:
            out["error"] = "failed to build merged.patch"
            return out
        out["resolved"] = True

        # Clobber check: did each side's introduced names survive?
        final = sb.exec("bash", "-c", f"cd /workspace/repo && git diff {base_sha} HEAD").stdout_read()
        out["merged_patch"] = final

        # Composition check: did the round COMPOSE the two patches, or rewrite?
        # Any line the reconciler added that neither agent had written is "novel".
        # Resolving a conflict legitimately produces a few (the composed call
        # site); a large share means it reimplemented rather than integrated.
        def added(patch_text: str) -> set[str]:
            return {
                ln[1:].strip()
                for ln in patch_text.splitlines()
                if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()
            }

        agent_lines = added(p1) | added(p2)
        final_lines = added(final)
        novel = final_lines - agent_lines
        out["added_lines_final"] = len(final_lines)
        out["added_lines_novel"] = len(novel)
        out["novel_fraction"] = round(len(novel) / max(len(final_lines), 1), 3)
        out["dropped_agent_lines"] = len(agent_lines - final_lines)
        out["agent_lines_total"] = len(agent_lines)
        out["novel_sample"] = sorted(novel)[:8]

        for tag, patch in (("f1", p1), ("f2", p2)):
            want = _added_identifiers(patch)
            kept = {n for n in want if re.search(rf"\b{re.escape(n)}\b", final)}
            out[f"{tag}_identifiers"] = len(want)
            out[f"{tag}_identifiers_kept"] = len(kept)
            out[f"{tag}_clobbered"] = sorted(want - kept)

        t1 = S._run_tests(sb, "tests1.patch", "merged.patch", base_sha)
        t2 = S._run_tests(sb, "tests2.patch", "merged.patch", base_sha)
        out["feature1_passed"] = bool(t1["passed"])
        out["feature2_passed"] = bool(t2["passed"])
        out["tests"] = {"f1": [t1.get("tests_passed", 0), t1.get("tests_failed", 0)],
                        "f2": [t2.get("tests_passed", 0), t2.get("tests_failed", 0)]}
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
    print(f"reconciliation round on {len(pairs)} conflicted pairs "
          f"({MODEL}, <= {MAX_STEPS} steps)\n")
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(reconcile, p): p for p in pairs}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            state = ("PASS" if r["both_passed"]
                     else "partial" if r["feature1_passed"] or r["feature2_passed"] else "fail")
            clob = (r.get("f1_clobbered") or []) + (r.get("f2_clobbered") or [])
            note = f"  clobbered={clob[:3]}" if clob else ""
            err = f"  ERR {r['error'][:50]}" if r.get("error") else ""
            print(f"[{i:>3}/{len(pairs)}] {r['steps']:>2} steps  "
                  f"{'resolved' if r['resolved'] else 'NOFIX':<9}{state:<8}"
                  f"{r['repo']}/{r['task_id']} f{r['feature1']}_f{r['feature2']}{note}{err}")

    out = Path(__file__).resolve().parent.parent / "data" / (
        os.environ.get("OUT") or "reconcile.json"
    )
    out.write_text(json.dumps(results, indent=1))

    n = len(results)
    comp = [r for r in results if r.get("added_lines_final")]
    if comp:
        fr = sorted(r["novel_fraction"] for r in comp)
        print(f"\ncomposition (did it merge or rewrite?), n={len(comp)}")
        print(f"  median novel-line fraction : {fr[len(fr) // 2]:.0%}")
        print(f"  max                        : {fr[-1]:.0%}")
        print(f"  agent lines dropped        : "
              f"{sum(r['dropped_agent_lines'] for r in comp)}/{sum(r['agent_lines_total'] for r in comp)}")
    print(f"\nresolved to a clean tree : {sum(r['resolved'] for r in results)}/{n}")
    print(f"both features pass       : {sum(r['both_passed'] for r in results)}/{n}")
    print(f"at least one feature     : {sum(r['feature1_passed'] or r['feature2_passed'] for r in results)}/{n}")
    print(f"clobbered partner work   : {sum(1 for r in results if (r.get('f1_clobbered') or r.get('f2_clobbered')))}/{n}")
    print(f"edited a test file       : {sum(1 for r in results if r.get('touched_test_files'))}/{n}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
