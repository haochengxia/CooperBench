#!/usr/bin/env python3
"""Is the missing piece conflict resolution, or an integration owner in general?

``reconcile.py`` ran a bounded round on the 16 coop pairs whose merge CONFLICTED
and took them from 6% to 62%.  That result is open to a much broader reading, and
the two readings imply different fixes:

  A. "Conflicted pairs are graded without a merged tree."  Narrow.  The round is
     the missing step only where a conflict occurred.
  B. "Coop has no final union-owner pass."  Broad.  Nobody in the coop pipeline
     ever holds both specs, both test signals and one tree -- conflict or not.

These are distinguished by running the SAME round on coop pairs that merged
CLEANLY and still failed.  If it lifts those too, reading B is right, the framing
"conflicts are scored unfairly" is wrong, and a compute-matched solo control
becomes essential rather than optional.

So this also runs the control: solo's own failures, same round, same budget, on
solo's single patch.  Coop+round is two agent budgets plus a repair budget against
solo's one; the benchmark's headline metric does not compute-match, but the
comparison is only honest if solo gets the same last pass.

Cohorts:
    coop-clean-failed   coop pairs, merge was clean, did NOT both-pass   (n=18)
    coop-conflicted     coop pairs whose merge conflicted                (n=16)
    solo-failed         solo runs that did NOT both-pass                 (n=26)

Same guards as reconcile.py: never reads the held-out suites, may not edit test
files, <= 20 steps, and every identifier either side introduced is checked for
survival.  Test output is captured for the failures so near-misses can be read.

Usage:
    uv run python scripts/integrate.py --cohort coop-clean-failed
    uv run python scripts/integrate.py --cohort solo-failed
"""

from __future__ import annotations

import argparse
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

BLOCKED = re.compile(r"/patches|tests?\.patch", re.I)
TESTFILE = re.compile(r"(^|/)(tests?/|test_|conftest\.py|.*_test\.(py|rs))", re.I)

HEAD = """You are the integrator for a codebase that must support TWO features at once.

## Feature A
{spec1}

## Feature B
{spec2}

"""

BODY_CONFLICT = """Two engineers implemented these independently from the same base \
commit, without seeing each other's final code. Their branches have been merged and \
the working tree at /workspace/repo contains git conflict markers in:
{files}

Resolve every conflict so that BOTH features work. This is NOT a choice between two \
alternatives -- both engineers' work must survive. The usual shape is that both sides \
wrapped or replaced the same call site; the correct resolution composes the two \
changes rather than picking one. Deleting one side's work to make the conflict go \
away is a failure, even though it produces a clean tree.
"""

BODY_CLEAN = """Two engineers implemented these independently from the same base \
commit, without seeing each other's final code. Their branches merged without any \
git conflict, so the tree at /workspace/repo already contains both changes -- but a \
textually clean merge does not mean the two features actually work together.

Your job is to make BOTH features work. Check that each feature's behaviour is \
really present and correct in the combined tree, and fix whatever is broken or \
missing. Do not delete either engineer's work.
"""

BODY_SOLO = """One engineer implemented BOTH features in a single pass, and the tree \
at /workspace/repo contains that work.

Your job is to make BOTH features work. Check that each feature's behaviour is really \
present and correct, and fix whatever is broken or missing.
"""

TAIL = """
You have a shell in the container. Reply with exactly one command per turn, in a \
single fenced block:

```bash
your command here
```

You may read and edit source, and run the repository's existing tests to check \
yourself. You may NOT edit, add or delete any test file, and you may not read \
anything under /patches. When you are done, reply with exactly:

```bash
echo TASK_COMPLETE
```
"""


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


def _added(patch_text: str) -> set[str]:
    return {
        ln[1:].strip()
        for ln in patch_text.splitlines()
        if ln.startswith("+") and not ln.startswith("+++") and ln[1:].strip()
    }


def run_round(item: dict, timeout: int = 1800) -> dict:
    repo, task = item["repo"], item["task_id"]
    f1, f2 = item["feature1"], item["feature2"]
    td = remerge.DATASET / repo / f"task{task}"
    out: dict = {**item, "resolved": False, "steps": 0,
                 "feature1_passed": False, "feature2_passed": False}
    out.pop("merged_patch", None)

    sb = None
    try:
        sb = get_backend("docker").create_sandbox(get_image_name(repo, task), timeout)
        S._write_patch(sb, "tests1.patch", (td / f"feature{f1}" / "tests.patch").read_text())
        S._write_patch(sb, "tests2.patch", (td / f"feature{f2}" / "tests.patch").read_text())

        if item["cohort"] == "solo-failed":
            patch = S._filter_test_files(Path(item["patch"]).read_text())
            S._write_patch(sb, "patch1.patch", patch)
            S._write_patch(sb, "patch2.patch", "")
            agent_lines = _added(patch)
            setup = S._setup_branches(sb)
            base_sha = setup["base_sha"]
            sb.exec("bash", "-c", "cd /workspace/repo && git checkout --force agent1 >/dev/null 2>&1")
            files, body = [], BODY_SOLO
        else:
            p1 = S._filter_test_files(Path(item["patch1"]).read_text())
            p2 = S._filter_test_files(Path(item["patch2"]).read_text())
            S._write_patch(sb, "patch1.patch", p1)
            S._write_patch(sb, "patch2.patch", p2)
            agent_lines = _added(p1) | _added(p2)
            setup = S._setup_branches(sb)
            base_sha = setup["base_sha"]
            files = [f for f in sb.exec("bash", "-c", """
cd /workspace/repo
git checkout --force agent2 >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
git diff --name-only --diff-filter=U
""").stdout_read().splitlines() if f.strip()]
            body = BODY_CONFLICT.format(files="\n".join(f"- {f}" for f in files)) if files else BODY_CLEAN
        out["conflicted_files"] = files

        prompt = HEAD.format(
            spec1=(td / f"feature{f1}" / "feature.md").read_text(),
            spec2=(td / f"feature{f2}" / "feature.md").read_text(),
        ) + body + TAIL
        messages = [{"role": "system", "content": prompt},
                    {"role": "user", "content": "Begin. Inspect the current state first."}]

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
                                 "Refused: the grading tests are off limits."})
                continue
            res = sb.exec("bash", "-c", f"cd /workspace/repo && {cmd}")
            obs = (res.stdout_read() + res.stderr_read())[:4000] or "(no output)"
            messages.append({"role": "user", "content": f"<output rc={res.returncode}>\n{obs}\n</output>"})

        touched = sb.exec("bash", "-c", "cd /workspace/repo && git status --porcelain").stdout_read()
        out["touched_test_files"] = [ln[3:] for ln in touched.splitlines() if TESTFILE.search(ln[3:])]
        markers = sb.exec("bash", "-c",
                          "cd /workspace/repo && grep -rl '^<<<<<<< ' --exclude-dir=.git . | head -3"
                          ).stdout_read().strip()
        if markers:
            out["error"] = f"markers remain: {markers}"
            return out

        made = sb.exec("bash", "-c",
                       f"cd /workspace/repo && git add -A && git commit -m integrated --allow-empty "
                       f">/dev/null 2>&1 && git diff {base_sha} HEAD > /patches/merged.patch && echo OK"
                       ).stdout_read()
        if "OK" not in made:
            out["error"] = "failed to build merged.patch"
            return out
        out["resolved"] = True

        final = sb.exec("bash", "-c", f"cd /workspace/repo && git diff {base_sha} HEAD").stdout_read()
        fin = _added(final)
        novel = fin - agent_lines
        out["added_lines_final"] = len(fin)
        out["added_lines_novel"] = len(novel)
        out["novel_fraction"] = round(len(novel) / max(len(fin), 1), 3)
        out["dropped_agent_lines"] = len(agent_lines - fin)
        out["agent_lines_total"] = len(agent_lines)

        t1 = S._run_tests(sb, "tests1.patch", "merged.patch", base_sha)
        t2 = S._run_tests(sb, "tests2.patch", "merged.patch", base_sha)
        out["feature1_passed"] = bool(t1["passed"])
        out["feature2_passed"] = bool(t2["passed"])
        out["tests"] = {"f1": [t1.get("tests_passed", 0), t1.get("tests_failed", 0)],
                        "f2": [t2.get("tests_passed", 0), t2.get("tests_failed", 0)]}
        # Keep failing output so near-misses can be diagnosed.
        for tag, t in (("f1", t1), ("f2", t2)):
            if not t["passed"]:
                out[f"{tag}_output_tail"] = (t.get("output") or "")[-2500:]
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


def collect(cohort: str) -> list[dict]:
    items = []
    if cohort == "solo-failed":
        for ev in sorted((remerge.REPO_ROOT / "logs/net-solo").rglob("eval.json")):
            d = json.loads(ev.read_text())
            if d.get("both_passed"):
                continue
            patch = ev.parent / "solo.patch"
            if not patch.exists():
                continue
            f1, f2 = sorted(d["features"])
            items.append({"cohort": cohort, "dir": str(ev.parent), "repo": d["repo"],
                          "task_id": d["task_id"], "feature1": f1, "feature2": f2,
                          "patch": str(patch), "original_both_passed": False})
        return items

    if cohort == "coopgit-failed":
        # The decisive cohort: these agents were TOLD the peer had finished, were
        # given its branch, and 72% of them merged it in-run.  The merge already
        # happened.  If the round still rescues them, what was missing was never
        # access to the partner's code -- it was accountability for the partner's
        # feature.
        for p in remerge.collect(remerge.REPO_ROOT / "logs/net-coopgit", only_conflicts=False):
            if p["original_both_passed"]:
                continue
            items.append({**p, "cohort": cohort})
        return items

    want_conflict = cohort == "coop-conflicted"
    for p in remerge.collect(remerge.REPO_ROOT / "logs/net-coop", only_conflicts=False):
        conflicted = p["original_status"] == "conflicts"
        if conflicted != want_conflict:
            continue
        if not want_conflict and p["original_both_passed"]:
            continue  # clean AND already passing -- nothing to fix
        items.append({**p, "cohort": cohort})
    return items


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cohort", required=True,
                    choices=["coop-clean-failed", "coop-conflicted", "solo-failed",
                             "coopgit-failed"])
    ap.add_argument("-c", "--concurrency", type=int, default=4)
    args = ap.parse_args()

    items = collect(args.cohort)
    print(f"cohort {args.cohort}: {len(items)} items ({MODEL}, <= {MAX_STEPS} steps)\n")
    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(run_round, it): it for it in items}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            state = ("PASS" if r["both_passed"]
                     else "partial" if r["feature1_passed"] or r["feature2_passed"] else "fail")
            err = f"  ERR {r['error'][:50]}" if r.get("error") else ""
            print(f"[{i:>3}/{len(items)}] {r['steps']:>2} steps  {state:<8}"
                  f"{r['repo'].split('_task')[0]}/{r['task_id']} f{r['feature1']}_f{r['feature2']}{err}")

    out = Path(__file__).resolve().parent.parent / "data" / f"integrate-{args.cohort}.json"
    out.write_text(json.dumps(results, indent=1))
    n = len(results)
    print(f"\nboth features pass  : {sum(r['both_passed'] for r in results)}/{n}")
    print(f"at least one        : {sum(r['feature1_passed'] or r['feature2_passed'] for r in results)}/{n}")
    print(f"edited a test file  : {sum(1 for r in results if r.get('touched_test_files'))}/{n}")
    comp = [r for r in results if r.get("added_lines_final")]
    if comp:
        fr = sorted(r["novel_fraction"] for r in comp)
        print(f"novel-line fraction : median {fr[len(fr) // 2]:.0%}, max {fr[-1]:.0%}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
