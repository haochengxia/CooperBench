"""Oracle ownership assignments for the P3 arbitration arm.

This is an **oracle upper bound**, not a deployable method: it reads the gold
patches to find which code each feature will end up touching, and hands every
agent a binding owner for the resources more than one feature touches.  It never
reveals the diff -- only resource names and who owns them -- so the arm tests
what *ownership information* is worth, not what a leaked implementation is worth.

The resource unit is the **enclosing scope** (function/class), not the
newly-defined symbol.  Both P0 and P2.75 (docs/analysis/2026-08-17-ownership-
arbitration) point the same way: contested *definitions* average only 0.32 per
pair at N=2, so a definition-only oracle would be a no-op for most pairs, while
contested *scopes* average 1.41 at N=2 and 2.65 at N=8 -- bounded, but non-empty.
Scopes come free out of `git diff` hunk headers, in every language.

Enabled by setting ``COOPERBENCH_OWNERSHIP=1``.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from pathlib import Path

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@ ?(.*)$")
SCOPE = re.compile(
    r"\b(?:def|class)\s+(\w+)"  # python
    r"|\bfunc\s+(?:\([^)]*\)\s*)?(\w+)"  # go
    r"|\b(?:fn|struct|impl|trait)\s+(\w+)"  # rust
    r"|\b(?:function|const|let|class|interface)\s+(\w+)"  # js/ts
)
DECL = re.compile(r"\b(?:def|func|class|type)\s+(\w+)")
STOP = set(
    """None True False bool int str float list dict set tuple type self cls args kwargs
return yield import from class def func value values result results data item items name
names key keys index count size length error err exc exception test tests assert print
format text content path file line lines Optional Callable Union Any List Dict Set Tuple
config params param options kwargs opts obj node ctx context request response""".split()
)


def _resources(patch: str) -> set[str]:
    """Resources a patch touches: enclosing scopes of its hunks, plus new declarations."""
    out: set[str] = set()
    cur = ""
    for line in patch.splitlines():
        if line.startswith("+++ "):
            cur = line.split("\t")[0][6:].strip()
            continue
        m = HUNK.match(line)
        if m and cur and cur != "ev/null":
            sm = SCOPE.search(m.group(3) or "")
            if sm:
                name = next(g for g in sm.groups() if g)
                if name not in STOP:
                    out.add(name)
            continue
        if line.startswith("+") and not line.startswith("+++"):
            for dm in DECL.finditer(line[1:]):
                s = dm.group(1)
                if len(s) > 3 and s not in STOP and not s.startswith("__"):
                    out.add(s)
    return out


def contested_resources(task_dir: Path, features: list[int]) -> dict[str, int]:
    """-> {resource: owning feature id}, for resources touched by >1 feature.

    Ownership goes to the lowest feature id so the assignment is deterministic
    and identical for every agent in the subset.
    """
    touched: dict[int, set[str]] = {}
    for f in features:
        patch = task_dir / f"feature{f}" / "feature.patch"
        touched[f] = _resources(patch.read_text()) if patch.exists() else set()

    by_res: dict[str, list[int]] = defaultdict(list)
    for f in sorted(features):
        for r in touched[f]:
            by_res[r].append(f)
    return {r: min(fs) for r, fs in by_res.items() if len(fs) > 1}


def ownership_block(task_dir: Path, features: list[int], feature_id: int, agents: list[str] | None) -> str | None:
    """The binding-assignment text appended to one agent's task, or None."""
    if len(features) < 2:
        return None
    owners = contested_resources(task_dir, features)
    if not owners:
        return None

    ordered = sorted(features)
    agent_of = {f: (agents[i] if agents and i < len(agents) else f"agent_{i + 1}") for i, f in enumerate(ordered)}

    mine = sorted(r for r, f in owners.items() if f == feature_id)
    theirs: dict[str, list[str]] = defaultdict(list)
    for r, f in sorted(owners.items()):
        if f != feature_id:
            theirs[agent_of[f]].append(r)

    lines = [
        "",
        "## Ownership assignments (binding)",
        "",
        "Other agents are working in parallel on different features in this same",
        "repository. The code below is code that more than one of you would",
        "otherwise edit, so each piece has been assigned a single owner. These",
        "assignments are binding.",
        "",
    ]
    if mine:
        lines.append("You own these -- you may change them freely:")
        lines += [f"  - `{r}`" for r in mine]
        lines.append("")
    for agent, rs in theirs.items():
        lines.append(f"Owned by {agent} -- you may CALL these, but do NOT edit their bodies,")
        lines.append("change their signatures, or add parameters to them:")
        lines += [f"  - `{r}`" for r in rs]
        lines.append("")
    lines += [
        "If you are convinced you cannot implement your feature without changing",
        "something you do not own, message its owner and agree on the change",
        "before making it.",
        "",
    ]
    return "\n".join(lines)


def maybe_apply(
    task: str, task_dir: Path, features: list[int] | None, feature_id: int, agents: list[str] | None
) -> str:
    """Append the ownership block when COOPERBENCH_OWNERSHIP=1, else pass through."""
    if os.environ.get("COOPERBENCH_OWNERSHIP") != "1" or not features:
        return task
    block = ownership_block(task_dir, list(features), feature_id, agents)
    return task + "\n" + block if block else task
