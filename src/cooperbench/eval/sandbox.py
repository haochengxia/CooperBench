"""Sandbox execution for patch testing."""

import base64
import os
import re
from pathlib import Path

from cooperbench.eval.backends import get_backend
from cooperbench.eval.backends.base import Sandbox
from cooperbench.runner.tasks import DEFAULT_DATASET_DIR
from cooperbench.utils import get_image_name


def run_patch_test(
    repo_name: str,
    task_id: int,
    feature_id: int,
    agent_patch: str | Path | None = None,
    timeout: int = 600,
    backend: str = "docker",
    dataset_dir: Path | str | None = None,
) -> dict:
    """Test a single patch against one feature's tests.

    Args:
        repo_name: Repository name (e.g., "llama_index_task")
        task_id: Task ID from dataset/
        feature_id: Which feature's tests to run
        agent_patch: Patch content (str) or path to .patch file
        timeout: Max seconds for sandbox execution
        backend: Evaluation backend ("modal", "docker", "gcp_batch")
        dataset_dir: Root of the dataset tree.  Defaults to ``./dataset``.

    Returns:
        Dict with keys: passed, tests_passed, tests_failed, output, error
    """
    root = Path(dataset_dir) if dataset_dir is not None else DEFAULT_DATASET_DIR
    task_dir = root / repo_name / f"task{task_id}"
    feature_dir = task_dir / f"feature{feature_id}"
    tests_patch_path = feature_dir / "tests.patch"
    gold_patch_path = feature_dir / "feature.patch"

    if not tests_patch_path.exists():
        return _error_result(f"Tests patch not found: {tests_patch_path}")

    tests_patch = tests_patch_path.read_text()

    # If no agent patch provided, use the gold patch from dataset
    if agent_patch is None and gold_patch_path.exists():
        agent_patch = gold_patch_path

    agent_patch_content = _load_patch(agent_patch)

    # Filter test files from agent patch
    if agent_patch_content:
        agent_patch_content = _filter_test_files(agent_patch_content)

    if agent_patch is not None and not agent_patch_content:
        return _error_result("Agent patch is empty")

    image = get_image_name(repo_name, task_id)
    eval_backend = get_backend(backend)
    sb = eval_backend.create_sandbox(image, timeout)

    try:
        _write_patch(sb, "tests.patch", tests_patch)
        if agent_patch_content:
            _write_patch(sb, "agent.patch", agent_patch_content)

        # Use runner.sh with [tests.patch, feature.patch]
        if agent_patch_content:
            result = sb.exec("bash", "/usr/local/bin/runner.sh", "tests.patch", "agent.patch")
        else:
            result = sb.exec("bash", "/usr/local/bin/runner.sh", "tests.patch")

        output = result.stdout_read() + result.stderr_read()
        exit_code = result.returncode
        parsed = _parse_results(output)

        return {
            "passed": exit_code == 0 and parsed["passed"] > 0,
            "tests_passed": parsed["passed"],
            "tests_failed": parsed["failed"],
            "tests_total": parsed["passed"] + parsed["failed"],
            "output": output,
            "error": None,
        }
    except Exception as e:
        return _error_result(str(e))
    finally:
        sb.terminate()


def _feature_payload(fid: int, r: dict) -> dict:
    return {
        "feature_id": fid,
        "passed": r["passed"],
        "exit_code": r.get("exit_code"),
        "tests_passed": r.get("tests_passed", 0),
        "tests_failed": r.get("tests_failed", 0),
        "test_output": r["output"],
    }


def _merged_payload(feature_ids: list[int], results: list[dict], **extra) -> dict:
    """Build the result dict, with the N==2 legacy keys kept identical."""
    feats = [_feature_payload(f, r) for f, r in zip(feature_ids, results)]
    all_passed = all(r["passed"] for r in results)
    out = dict(extra)
    out["features"] = feats
    out["all_passed"] = all_passed
    if len(feature_ids) == 2:
        out["feature1"], out["feature2"] = feats[0], feats[1]
        out["both_passed"] = all_passed
    out["error"] = None
    return out


def test_merged(
    repo_name: str,
    task_id: int,
    feature1_id: int | None = None,
    feature2_id: int | None = None,
    patch1: str | Path | None = None,
    patch2: str | Path | None = None,
    timeout: int = 600,
    backend: str = "docker",
    dataset_dir: Path | str | None = None,
    *,
    feature_ids: list[int] | None = None,
    patches: list[str | Path | None] | None = None,
) -> dict:
    """Test merged patches from N agents (coop mode).

    Creates one git branch per agent, applies each agent's patch, merges them
    sequentially into ``agent1``, then tests the merged result against every
    feature's test suite.

    Two calling conventions:

    * ``feature_ids=[...], patches=[...]`` -- any N, preferred.
    * ``feature1_id/feature2_id/patch1/patch2`` -- N=2 only, kept so existing
      callers and readers of already-published runs keep working.

    Returns ``features`` (one entry per feature, in the given order) and
    ``all_passed``.  When N==2 the legacy ``feature1``/``feature2``/
    ``both_passed`` keys are also emitted, with identical content.
    """
    if feature_ids is None:
        feature_ids = [f for f in (feature1_id, feature2_id) if f is not None]
    if patches is None:
        patches = [patch1, patch2]
    if len(feature_ids) < 2:
        return _merged_error_result("test_merged requires at least 2 features")
    patches = list(patches)[: len(feature_ids)]
    patches += [None] * (len(feature_ids) - len(patches))
    n = len(feature_ids)

    root = Path(dataset_dir) if dataset_dir is not None else DEFAULT_DATASET_DIR
    task_dir = root / repo_name / f"task{task_id}"

    tests_paths = [task_dir / f"feature{f}" / "tests.patch" for f in feature_ids]
    for tp in tests_paths:
        if not tp.exists():
            return _merged_error_result(f"Tests patch not found: {tp}")

    # Filter test files from patches -- agents must not grade themselves.
    patch_contents = [_filter_test_files(_load_patch(pc) or "") for pc in patches]
    tests_contents = [tp.read_text() for tp in tests_paths]

    image = get_image_name(repo_name, task_id)
    eval_backend = get_backend(backend)
    sb = eval_backend.create_sandbox(image, timeout)

    try:
        for i, content in enumerate(patch_contents, 1):
            _write_patch(sb, f"patch{i}.patch", content)
        for i, content in enumerate(tests_contents, 1):
            _write_patch(sb, f"tests{i}.patch", content)

        # Step 1: Apply patches to branches
        setup_result = _setup_branches(sb, n)
        if setup_result.get("error"):
            return _merged_error_result(setup_result["error"])

        base_sha = setup_result.get("base_sha")
        if not base_sha:
            return _merged_error_result("Failed to get base commit SHA")

        apply_status = setup_result.get("apply_status", {f"agent{i}": "unknown" for i in range(1, n + 1)})
        any_apply_failed = "failed" in apply_status.values()

        # Short-circuit: if every agent submitted byte-identical patches
        # (e.g. team mode where they fully merged each other's work and
        # ended up with the exact same tree), there's nothing to merge.
        # Skip the naive merge, which would try to apply patch B on
        # top of patch A's hunks and reject them as already-applied -- that
        # produces an empty merged.patch and a downstream "No valid patches
        # in input" failure even though the submissions are identical and
        # individually fine.
        #
        # We also normalize the patch here: agents (notably codex) can emit
        # unified diffs whose last hunk header has the wrong line count
        # ("corrupt patch at line N").  ``git apply --recount`` ignores
        # the header and rebuilds from content; we then re-emit the diff
        # so runner.sh's plain ``git apply`` accepts it.
        if all(patch_contents) and len(set(patch_contents)) == 1:
            normalize = """
cd /workspace/repo
git checkout $BASE_SHA 2>&1 >/dev/null
git checkout -b identical-merge 2>&1 >/dev/null
if git apply /patches/patch1.patch 2>/dev/null \\
   || git apply --recount /patches/patch1.patch 2>/dev/null; then
    git add -A
    git commit -m 'merged' --allow-empty >/dev/null 2>&1
    git diff $BASE_SHA HEAD > /patches/merged.patch
    echo "NORMALIZED"
else
    # fall back to raw patch if normalization itself fails
    cp /patches/patch1.patch /patches/merged.patch
    echo "RAW"
fi
"""
            sb.exec("bash", "-c", f"export BASE_SHA={base_sha}\n{normalize}")
            results = [_run_tests(sb, f"tests{i}.patch", "merged.patch", base_sha) for i in range(1, n + 1)]
            return _merged_payload(
                feature_ids,
                results,
                repo=repo_name,
                task_id=task_id,
                features_ids=list(feature_ids),
                setting="coop",
                apply_status={f"agent{i}": "applied" for i in range(1, n + 1)},
                merge={
                    "status": "identical",
                    "strategy": "skip-merge-identical",
                    "diff": patch_contents[0][:5000],
                },
                evaluated_at=__import__("datetime").datetime.now().isoformat(),
            )

        # Step 2: Try naive merge.  No union fallback -- union resolves
        # conflicts by concatenating both sides, which usually produces
        # syntactically broken code and rewards lucky non-overlap rather
        # than real coordination.  Instead, when naive conflicts the eval
        # falls through to "lead's patch alone" below.
        # agent1's tip *before* the merge -- naive commits onto that branch, so
        # the union diagnostic below needs the pre-merge SHA to start clean.
        agent1_sha: str | None = None
        if os.environ.get("COOPERBENCH_UNION_DIAGNOSTIC") == "1":
            rev = sb.exec("bash", "-c", "cd /workspace/repo && git rev-parse agent1")
            agent1_sha = (rev.stdout_read().strip().splitlines() or [""])[-1] or None

        naive_result = _merge_naive(sb, base_sha, n)

        if any_apply_failed:
            merge_status = "missing_input"
        elif naive_result["conflict"]:
            merge_status = "conflicts"
        else:
            merge_status = "clean"
        strategy_used = "naive"
        merged_diff = naive_result["diff"]

        # Step 3: Compute the merged-tree test result.
        #
        # - status="clean": naive merge worked, copy that tree, run tests.
        #   The merged-tree tests are AUTHORITATIVE for clean merges -- no
        #   fallback.  If the team's joint patch fails tests, the team failed.
        # - status in {"conflicts", "missing_input"}: no useful merged tree.
        #   Skip the merged-tree tests and go straight to the lead-alone
        #   fallback below.
        empty = {"passed": False, "exit_code": None, "tests_passed": 0, "tests_failed": 0, "output": ""}
        if merge_status == "clean":
            sb.exec("cp", "/patches/naive_diff.patch", "/patches/merged.patch")
            verify = sb.exec("test", "-f", "/patches/merged.patch")
            if verify.returncode != 0:
                return _merged_error_result(f"Failed to create merged.patch (strategy: {strategy_used})")
            results = [_run_tests(sb, f"tests{i}.patch", "merged.patch", base_sha) for i in range(1, n + 1)]
            winning_solo: str | None = None
        else:
            # No merged-tree test path; surface failure for every feature and
            # let the lead-only fallback decide.
            results = [dict(empty) for _ in range(n)]
            winning_solo = None
            # Step 4 (fallback): only when naive failed.  Test the LEAD's
            # patch alone against every feature suite -- the lead is the
            # team's integrator and their patch.txt is the "shipped artifact".
            # No member fallback -- if the members integrated but the lead
            # didn't, the team coordination failed.
            if apply_status.get("agent1") == "applied":
                solo = [_run_tests(sb, f"tests{i}.patch", "patch1.patch", base_sha) for i in range(1, n + 1)]
                if all(r["passed"] for r in solo):
                    results = solo
                    winning_solo = "agent1"

        merge_payload = {
            "status": merge_status,
            "strategy": strategy_used if winning_solo is None else f"solo-{winning_solo}",
            "diff": merged_diff[:5000] if merged_diff else "",  # Truncate for storage
        }
        if naive_result.get("conflict_at"):
            merge_payload["conflict_at"] = naive_result["conflict_at"]

        # Diagnostic only -- deliberately computed AFTER results/merge_status are
        # final, so it cannot influence the score.
        if merge_status == "conflicts" and os.environ.get("COOPERBENCH_UNION_DIAGNOSTIC") == "1":
            merge_payload["union_diagnostic"] = _union_diagnostic(sb, base_sha, n, agent1_sha)

        return _merged_payload(
            feature_ids,
            results,
            apply_status=apply_status,
            merge=merge_payload,
        )
    except Exception as e:
        return _merged_error_result(str(e))


def test_solo(
    repo_name: str,
    task_id: int,
    feature1_id: int,
    feature2_id: int,
    patch: str | Path | None = None,
    timeout: int = 600,
    backend: str = "docker",
    dataset_dir: Path | str | None = None,
) -> dict:
    """Test a solo patch against both features' tests.

    In solo mode, one agent implements both features in a single patch.
    We test that patch against each feature's test suite separately.

    Args:
        repo_name: Repository name
        task_id: Task ID
        feature1_id: First feature ID
        feature2_id: Second feature ID
        patch: The solo agent's combined patch
        timeout: Max seconds for sandbox execution
        backend: Evaluation backend
        dataset_dir: Root of the dataset tree.  Defaults to ``./dataset``.

    Returns:
        Dict with keys: setting, patch_lines, feature1, feature2,
        both_passed, error
    """
    root = Path(dataset_dir) if dataset_dir is not None else DEFAULT_DATASET_DIR
    task_dir = root / repo_name / f"task{task_id}"

    tests1_path = task_dir / f"feature{feature1_id}" / "tests.patch"
    tests2_path = task_dir / f"feature{feature2_id}" / "tests.patch"

    if not tests1_path.exists():
        return _solo_error_result(f"Tests patch not found: {tests1_path}")
    if not tests2_path.exists():
        return _solo_error_result(f"Tests patch not found: {tests2_path}")

    patch_content = _load_patch(patch) or ""

    # Filter test files from patch
    patch_content = _filter_test_files(patch_content)

    tests1_content = tests1_path.read_text()
    tests2_content = tests2_path.read_text()

    image = get_image_name(repo_name, task_id)
    eval_backend = get_backend(backend)
    sb = eval_backend.create_sandbox(image, timeout)

    try:
        # Get base SHA
        result = sb.exec("bash", "-c", "cd /workspace/repo && git rev-parse HEAD")
        base_sha = result.stdout_read().strip()

        if not base_sha:
            return _solo_error_result("Failed to get base commit SHA")

        # Write patches
        _write_patch(sb, "solo.patch", patch_content)
        _write_patch(sb, "tests1.patch", tests1_content)
        _write_patch(sb, "tests2.patch", tests2_content)

        # Test feature 1: runner.sh tests1.patch solo.patch
        test1_result = _run_tests(sb, "tests1.patch", "solo.patch", base_sha)

        # Test feature 2: runner.sh tests2.patch solo.patch
        test2_result = _run_tests(sb, "tests2.patch", "solo.patch", base_sha)

        return {
            "setting": "solo",
            "patch_lines": len(patch_content.splitlines()) if patch_content else 0,
            "feature1": {
                "passed": test1_result["passed"],
                "test_output": test1_result["output"],
            },
            "feature2": {
                "passed": test2_result["passed"],
                "test_output": test2_result["output"],
            },
            "both_passed": test1_result["passed"] and test2_result["passed"],
            "error": None,
        }
    except Exception as e:
        return _solo_error_result(str(e))
    finally:
        sb.terminate()


# Alias for training compatibility
def evaluate_merge(
    repo_name: str,
    task_id: int,
    feature1_id: int,
    feature2_id: int,
    patch1: str,
    patch2: str,
) -> dict:
    """Evaluate merged patches - wrapper for training compatibility.

    Returns dict with keys expected by training code:
        feature1_tests_passed, feature1_tests_total,
        feature2_tests_passed, feature2_tests_total, error
    """
    result = test_merged(
        repo_name=repo_name,
        task_id=task_id,
        feature1_id=feature1_id,
        feature2_id=feature2_id,
        patch1=patch1,
        patch2=patch2,
    )
    return {
        "feature1_tests_passed": 1 if result.get("feature1", {}).get("passed") else 0,
        "feature1_tests_total": 1,
        "feature2_tests_passed": 1 if result.get("feature2", {}).get("passed") else 0,
        "feature2_tests_total": 1,
        "error": result.get("error"),
    }


# === Helper functions ===


def _write_patch(sb: Sandbox, filename: str, content: str) -> None:
    """Write a patch file to the sandbox."""
    encoded = base64.b64encode(content.encode()).decode()
    result = sb.exec("bash", "-c", f"echo '{encoded}' | base64 -d > /patches/{filename}")
    if result.returncode != 0:
        raise RuntimeError(f"Failed to write {filename}: {result.stderr_read()}")


def _setup_branches(sb: Sandbox, n_agents: int = 2) -> dict:
    """Set up one git branch per agent, each off the base commit.

    Returns ``apply_status`` per agent: ``"applied"`` / ``"skipped"`` (empty
    patch) / ``"failed"`` (git apply rejected the patch).  Callers must check
    this — a "clean" merge between two branches where one branch's patch
    silently failed to apply is not actually a clean merge of the agents'
    work, just a clean merge of nothing into the other.
    """
    branches = "\n".join(
        f"""
git checkout $BASE_SHA 2>&1
git checkout -b agent{i} 2>&1
apply_patch {i}
git add -A
git commit -m "Agent {i} changes" --allow-empty 2>&1"""
        for i in range(1, n_agents + 1)
    )
    commands = f"""
cd /workspace/repo
git config user.email "eval@cooperbench.local"
git config user.name "CooperBench Eval"

# Save base commit SHA
BASE_SHA=$(git rev-parse HEAD)
echo "BASE_SHA=$BASE_SHA"

apply_patch() {{
    local n=$1
    if [ -s /patches/patch${{n}}.patch ]; then
        if git apply /patches/patch${{n}}.patch 2>&1; then
            echo "PATCH${{n}}_APPLIED"
        elif git apply --3way /patches/patch${{n}}.patch 2>&1; then
            echo "PATCH${{n}}_APPLIED"
        else
            echo "PATCH${{n}}_FAILED"
        fi
    else
        echo "PATCH${{n}}_SKIPPED"
    fi
}}
{branches}

echo "SETUP_COMPLETE"
"""
    result = sb.exec("bash", "-c", commands)
    output = result.stdout_read() + result.stderr_read()

    if "SETUP_COMPLETE" not in output:
        return {"error": f"Branch setup failed: {output}"}

    # Extract base SHA
    base_sha = None
    for line in output.split("\n"):
        if line.startswith("BASE_SHA="):
            base_sha = line.split("=")[1].strip()
            break

    def _status(n: int) -> str:
        if f"PATCH{n}_APPLIED" in output:
            return "applied"
        if f"PATCH{n}_SKIPPED" in output:
            return "skipped"
        return "failed"

    return {
        "output": output,
        "error": None,
        "base_sha": base_sha,
        "apply_status": {f"agent{i}": _status(i) for i in range(1, n_agents + 1)},
    }


def _merge_naive(sb: Sandbox, base_sha: str, n_agents: int = 2) -> dict:
    """Merge every agent branch into agent1, sequentially.

    Order is agent1 <- agent2 <- ... <- agentN, i.e. sorted feature id, so the
    result is deterministic.  Order matters once N > 2: a conflict between
    agent2 and agent3 surfaces at a different step than one between agent1 and
    agent3, and `conflict_at` records which step first failed.
    """
    steps = "\n".join(
        f"""
if [ "$FAILED" = "" ]; then
    if git merge agent{i} --no-commit --no-ff 2>&1; then
        git commit -m "Temp merge agent{i}" 2>&1 || true
    else
        echo "MERGE_STATUS=conflicts"
        echo "CONFLICT_AT=agent{i}"
        git merge --abort 2>/dev/null || true
        FAILED=agent{i}
    fi
fi"""
        for i in range(2, n_agents + 1)
    )
    commands = f"""
cd /workspace/repo
git checkout agent1 2>&1
FAILED=""
{steps}

if [ "$FAILED" = "" ]; then
    echo "MERGE_STATUS=clean"
    # Diff against BASE commit, not against the branch tip
    git diff {base_sha} HEAD > /patches/naive_diff.patch
fi
"""
    result = sb.exec("bash", "-c", commands)
    output = result.stdout_read() + result.stderr_read()

    conflict = "MERGE_STATUS=conflicts" in output

    # Read diff from file if clean merge
    diff = ""
    if not conflict:
        diff_result = sb.exec("cat", "/patches/naive_diff.patch")
        diff = diff_result.stdout_read()

    conflict_at = None
    for line in output.split("\n"):
        if line.startswith("CONFLICT_AT="):
            conflict_at = line.split("=", 1)[1].strip()
            break

    return {"conflict": conflict, "diff": diff, "output": output, "conflict_at": conflict_at}


def _merge_union(sb: Sandbox, base_sha: str, n_agents: int = 2, start_sha: str | None = None) -> dict:
    """Merge every agent branch with a repo-wide ``merge=union`` driver.

    Union never reports a conflict -- it keeps *both* sides of every clashing
    hunk -- so this is a diagnostic, never a score.  See ``_union_diagnostic``.

    ``start_sha`` must be agent1's tip as it was *before* ``_merge_naive`` ran:
    naive commits its successful merge steps onto the agent1 branch itself, so
    after a partial merge that branch is no longer a clean starting point.
    """
    start = start_sha or "agent1"
    steps = "\n".join(
        f"""
if [ "$FAILED" = "" ]; then
    if git merge agent{i} --no-commit --no-ff >/dev/null 2>&1; then
        git commit -m "Temp union merge agent{i}" >/dev/null 2>&1 || true
    else
        echo "UNION_STATUS=conflicts"
        echo "UNION_CONFLICT_AT=agent{i}"
        git merge --abort 2>/dev/null || true
        FAILED=agent{i}
    fi
fi"""
        for i in range(2, n_agents + 1)
    )
    commands = f"""
cd /workspace/repo
git merge --abort >/dev/null 2>&1 || true
git reset --hard >/dev/null 2>&1
git clean -fdq >/dev/null 2>&1
git checkout -f -B union-trial {start} >/dev/null 2>&1

echo "* merge=union" >> .gitattributes
git add .gitattributes && git commit -m "union attrs" >/dev/null 2>&1

FAILED=""
{steps}

# Drop the driver again so it never shows up in the emitted diff.
rm -f .gitattributes
git add -A && git commit -m "drop union attrs" >/dev/null 2>&1 || true

if [ "$FAILED" = "" ]; then
    echo "UNION_STATUS=clean"
    git diff {base_sha} HEAD > /patches/union_diff.patch
fi
"""
    result = sb.exec("bash", "-c", commands)
    output = result.stdout_read() + result.stderr_read()

    if "UNION_STATUS=conflicts" in output:
        return {"error": "Union merge still has conflicts", "diff": "", "output": output}

    diff_result = sb.exec("cat", "/patches/union_diff.patch")
    return {"diff": diff_result.stdout_read(), "output": output, "error": None}


def _union_diagnostic(sb: Sandbox, base_sha: str, n: int, start_sha: str | None) -> dict:
    """Would a union merge have produced a tree that passes every suite?

    Purely diagnostic -- it does **not** feed the score.  The P2.5 audit
    (docs/analysis/2026-08-17-ownership-arbitration) measured this over 108
    conflicting gold subsets: union yields a fully passing tree for only 31% of
    them, and the other 69% need semantic reconciliation no deterministic
    strategy can do.  Scoring on union would therefore reward lucky non-overlap,
    which is why the naive result stays authoritative.  Recording it separately
    tells us how much of a run's conflict rate is mechanical.

    Off by default (it costs an extra merge plus N test runs on every
    conflicted evaluation); enable with ``COOPERBENCH_UNION_DIAGNOSTIC=1``.
    """
    union = _merge_union(sb, base_sha, n, start_sha)
    if union.get("error"):
        return {"clean": False, "all_passed": False, "reason": union["error"]}
    sb.exec("cp", "/patches/union_diff.patch", "/patches/union_merged.patch")
    results = [_run_tests(sb, f"tests{i}.patch", "union_merged.patch", base_sha) for i in range(1, n + 1)]
    return {
        "clean": True,
        "all_passed": all(r["passed"] for r in results),
        "per_feature": [r["passed"] for r in results],
    }


def _run_tests(sb: Sandbox, tests_patch: str, feature_patch: str, base_sha: str) -> dict:
    """Run tests via runner.sh."""
    commands = f"""
cd /workspace/repo

# Remove any stale git lock left by a previous operation
rm -f .git/index.lock .git/refs/heads/.lock

# Reset to base commit. No -x: it deletes the build output the images pre-compile, which
# forced a full recompile per graded feature (335 crate compiles for typst).
git checkout --force {base_sha} 2>&1
git reset --hard {base_sha} 2>&1
git clean -fd 2>&1

echo "Reset to base: $(git rev-parse HEAD)"

# Run tests via runner.sh
bash /usr/local/bin/runner.sh {tests_patch} {feature_patch}
"""
    result = sb.exec("bash", "-c", commands)

    output = result.stdout_read() + result.stderr_read()
    exit_code = result.returncode
    parsed = _parse_results(output)

    return {
        "passed": exit_code == 0 and parsed["passed"] > 0,
        "output": output,
        "exit_code": exit_code,
        "tests_passed": parsed["passed"],
        "tests_failed": parsed["failed"],
    }


def _parse_results(output: str) -> dict:
    """Parse test output to extract pass/fail counts.

    Supports: pytest, go test, cargo test, jest/vitest
    """
    passed = 0
    failed = 0

    # jest/vitest (TypeScript) - check first due to specific "Tests:" prefix
    # Format: "Tests:       2 failed, 15 passed, 17 total"
    jest_match = re.search(r"Tests:\s*(?:(\d+)\s*failed,\s*)?(\d+)\s*passed", output)
    if jest_match:
        failed = int(jest_match.group(1)) if jest_match.group(1) else 0
        passed = int(jest_match.group(2))
        return {"passed": passed, "failed": failed}

    # pytest - look for the summary line format "X passed in Y.YYs"
    pytest_passed = re.search(r"(\d+) passed", output)
    pytest_failed = re.search(r"(\d+) failed", output)
    pytest_error = re.search(r"(\d+) error", output)

    if pytest_passed:
        passed = int(pytest_passed.group(1))
    if pytest_failed:
        failed = int(pytest_failed.group(1))
    if pytest_error:
        failed += int(pytest_error.group(1))

    if passed > 0 or failed > 0:
        return {"passed": passed, "failed": failed}

    # go test - verbose output (--- PASS:/--- FAIL:)
    go_pass = len(re.findall(r"--- PASS:", output))
    go_fail = len(re.findall(r"--- FAIL:", output))
    if go_pass or go_fail:
        return {"passed": go_pass, "failed": go_fail}

    # go test - non-verbose output (ok/FAIL package lines)
    # Format: "ok  github.com/pkg  0.123s" or "FAIL github.com/pkg [build failed]"
    go_ok_packages = len(re.findall(r"^ok\s+\S+", output, re.MULTILINE))
    go_fail_packages = len(re.findall(r"^FAIL\s+\S+", output, re.MULTILINE))
    if go_ok_packages or go_fail_packages:
        # If any package failed, count it; otherwise count ok packages as passed
        return {"passed": go_ok_packages if go_fail_packages == 0 else 0, "failed": go_fail_packages}

    # cargo test
    cargo_match = re.search(r"test result:.*?(\d+) passed.*?(\d+) failed", output)
    if cargo_match:
        return {"passed": int(cargo_match.group(1)), "failed": int(cargo_match.group(2))}

    return {"passed": passed, "failed": failed}


# A test file the agent wrote must never reach the graded patch: it collides with the hidden
# tests.patch and `git apply` then rejects the WHOLE patch, failing both features for a reason
# unrelated to their code. The previous rule only knew Python (`_test.py`), so `metrics_test.go`
# survived — which is what actually killed 3 of 4 runs on go_chi/26, not the merge.
_TEST_DIR_RE = re.compile(r"/(tests?|__tests__|spec|testdata)/")
_TEST_FILE_RE = re.compile(
    r"(^|/)("
    r"test_[^/]+"  # test_foo.py
    r"|[^/]+_test\.[A-Za-z0-9]+"  # foo_test.go / foo_test.rs / foo_test.py
    r"|[^/]+\.(test|spec)\.[A-Za-z0-9]+"  # foo.test.ts / foo.spec.js
    r"|[^/]*Test[s]?\.(java|kt|cs|scala)"  # FooTest.java
    r"|tests?\.py"  # tests.py
    r")$"
)


def _is_test_path(diff_header: str) -> bool:
    """Whether a `diff --git a/X b/Y` header names a test file, in any language."""
    paths = re.findall(r"[ab]/(\S+)", diff_header)
    return any(_TEST_DIR_RE.search("/" + p) or _TEST_FILE_RE.search(p) for p in paths)


def _filter_test_files(patch_content: str) -> str:
    """Filter test files from patch content."""
    if not patch_content:
        return patch_content

    filtered_lines = []
    skip_until_next_diff = False

    for line in patch_content.split("\n"):
        # Check if this is a new file diff header
        if line.startswith("diff --git"):
            skip_until_next_diff = _is_test_path(line)

        if not skip_until_next_diff:
            filtered_lines.append(line)

    result = "\n".join(filtered_lines)
    # Ensure patch ends with newline (required by git)
    if result and not result.endswith("\n"):
        result += "\n"
    return result


def _load_patch(patch: str | Path | None) -> str | None:
    """Load patch content from string or file."""
    if patch is None:
        return None
    if isinstance(patch, Path):
        content = patch.read_text()
    elif not patch or not patch.strip():
        # Empty string should return None, not try to read "." directory
        return None
    elif len(patch) < 500 and Path(patch).exists() and Path(patch).is_file():
        # If it looks like a file path (short, exists, is a file), read it
        content = Path(patch).read_text()
    else:
        content = patch

    # Sanitize patch content
    return _sanitize_patch(content)


def _sanitize_patch(content: str) -> str:
    """Sanitize patch content to fix common issues."""
    if not content:
        return content

    # Fix shell-escaped single quotes (e.g., won'\''t -> won't)
    content = content.replace("'\\''", "'")

    # Ensure patch ends with newline (required by git)
    if not content.endswith("\n"):
        content += "\n"

    return content


def _error_result(error: str) -> dict:
    return {
        "passed": False,
        "tests_passed": 0,
        "tests_failed": 0,
        "tests_total": 0,
        "output": "",
        "error": error,
    }


def _merged_error_result(error: str) -> dict:
    return {
        "apply_status": {"agent1": "unknown", "agent2": "unknown"},
        "merge": {"status": "error", "strategy": None, "diff": ""},
        "feature1": {
            "feature_id": None,
            "passed": False,
            "exit_code": None,
            "tests_passed": 0,
            "tests_failed": 0,
            "test_output": "",
        },
        "feature2": {
            "feature_id": None,
            "passed": False,
            "exit_code": None,
            "tests_passed": 0,
            "tests_failed": 0,
            "test_output": "",
        },
        "both_passed": False,
        "error": error,
    }


def _solo_error_result(error: str) -> dict:
    return {
        "setting": "solo",
        "patch_lines": 0,
        "feature1": {"passed": False, "test_output": ""},
        "feature2": {"passed": False, "test_output": ""},
        "both_passed": False,
        "error": error,
    }
