#!/usr/bin/env python3
"""Classify CooperBench coop trajectories against the coordination-failure theory.

Signals, one per hypothesis from the design discussion:
  H1 stale-reference   -> line-number refs in messages, and how many the sender
                          later invalidated with its own edits
  H2 no-state-recheck  -> did an agent inspect partner-touched files AFTER the
                          partner stopped editing them?
  H3 nobody-owns-union -> did either agent ever run the whole test suite (as
                          opposed to only its own feature's tests)?
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

TRAJ = Path("/tmp/cb/traj")

# "line 42", "lines 1-251", "lines 482-547", "L42", "line ~350"
LINE_REF = re.compile(r"\blines?\s*~?\d+\s*(?:[-–to]+\s*~?\d+)?", re.I)
PATH_REF = re.compile(r"/[\w./-]+\.(?:py|go|rs|ts|tsx|js|jsx|md|toml|json|yaml|yml)")
QUESTION = re.compile(r"\?\s*$|\?\s*\n")

# a command that runs the project's tests
TEST_CMD = re.compile(r"\b(pytest|go test|cargo test|npm test|yarn test|jest|vitest|tox|unittest)\b", re.I)
# a test invocation narrowed to specific files/nodes rather than the whole suite
SCOPED_TEST = re.compile(r"(pytest|go test|cargo test)\b[^\n|;&]*?(\S+_test\.\w+|test_\S+\.py|::|-run\s|-k\s)", re.I)


def load(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as fh:
        return json.load(fh)


def outbound_messages(events: list[dict]) -> list[tuple[int, str, str]]:
    """(index, sender, content) for every cross-agent message send."""
    out = []
    for i, e in enumerate(events):
        if e.get("action") == "call_tool_mcp":
            args = e.get("args") or {}
            if "send" in str(args.get("name", "")).lower():
                content = (args.get("arguments") or {}).get("content") or ""
                out.append((i, e.get("agentId", "?"), content))
    return out


def edits_by_agent(events: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """agent -> [(event_index, path)] for every edit."""
    d: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i, e in enumerate(events):
        if e.get("action") == "edit":
            p = (e.get("args") or {}).get("path")
            if p:
                d[e.get("agentId", "?")].append((i, p))
    return d


def reads_by_agent(events: list[dict]) -> dict[str, list[tuple[int, str]]]:
    d: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for i, e in enumerate(events):
        a = e.get("action")
        args = e.get("args") or {}
        if a == "read" and args.get("path"):
            d[e.get("agentId", "?")].append((i, args["path"]))
        elif a == "run":
            # treat a shell command naming a path as an inspection of it
            for m in PATH_REF.finditer(str(args.get("command") or "")):
                d[e.get("agentId", "?")].append((i, m.group(0)))
    return d


def analyse(path: Path) -> dict:
    events = load(path)
    msgs = outbound_messages(events)
    edits = edits_by_agent(events)
    reads = reads_by_agent(events)
    agents = sorted({e.get("agentId") for e in events if e.get("agentId")})

    # ---- H1: stale references -------------------------------------------
    line_ref_msgs = 0
    total_line_refs = 0
    stale_refs = 0  # sender later edited a file it had pinned by line number
    for idx, sender, content in msgs:
        refs = LINE_REF.findall(content)
        if refs:
            line_ref_msgs += 1
            total_line_refs += len(refs)
            paths = set(PATH_REF.findall(content))
            later = {p for i, p in edits.get(sender, []) if i > idx}
            # every pinned path the sender itself re-edited afterwards
            stale_refs += len(paths & later)

    # ---- H2: partner state re-check --------------------------------------
    recheck = {}
    for a in agents:
        partners = [x for x in agents if x != a]
        p_edits = [(i, p) for x in partners for i, p in edits.get(x, [])]
        if not p_edits:
            recheck[a] = None  # partner never edited; not applicable
            continue
        last_partner_edit = max(i for i, _ in p_edits)
        partner_paths = {p for _, p in p_edits}
        after = [(i, p) for i, p in reads.get(a, []) if i > last_partner_edit and p in partner_paths]
        ever = [(i, p) for i, p in reads.get(a, []) if p in partner_paths]
        recheck[a] = {"after_partner_done": len(after), "ever": len(ever)}

    # ---- H3: who owns the union ------------------------------------------
    full_suite_runs = 0
    scoped_runs = 0
    for e in events:
        if e.get("action") != "run":
            continue
        cmd = str((e.get("args") or {}).get("command") or "")
        if TEST_CMD.search(cmd):
            if SCOPED_TEST.search(cmd):
                scoped_runs += 1
            else:
                full_suite_runs += 1

    # ---- co-editing & comms ----------------------------------------------
    edit_sets = {a: {p for _, p in edits.get(a, [])} for a in agents}
    co_edited = set.intersection(*edit_sets.values()) if len(edit_sets) > 1 and all(edit_sets.values()) else set()

    questions = sum(1 for _, _, c in msgs if QUESTION.search(c))
    # identity confusion: message self-labels with an agent id that isn't the sender
    id_conf = 0
    for _, sender, content in msgs:
        for m in re.finditer(r"agent[_ ]?(\d+)", content[:200], re.I):
            if f"agent_{m.group(1)}" != sender:
                id_conf += 1
                break

    return {
        "file": path.name,
        "agents": len(agents),
        "events": len(events),
        "messages": len(msgs),
        "line_ref_msgs": line_ref_msgs,
        "total_line_refs": total_line_refs,
        "stale_refs": stale_refs,
        "questions": questions,
        "recheck": recheck,
        "full_suite_runs": full_suite_runs,
        "scoped_runs": scoped_runs,
        "co_edited": sorted(co_edited),
        "id_confusion_msgs": id_conf,
        "edits_per_agent": {a: len(edits.get(a, [])) for a in agents},
    }


def main() -> int:
    sample = json.load(open("/tmp/cb/sample.json"))
    label = {}
    for model, rows in sample.items():
        for t in rows:
            fn = f"{model}__{t['repo']}_{t['taskId']}_{t['features']}.json.gz"
            label[fn] = {"model": model, "passed": t["passed"], "hasConflict": t["hasConflict"]}

    results = []
    for p in sorted(TRAJ.glob("*.json.gz")):
        try:
            r = analyse(p)
        except Exception as exc:  # keep going; report at the end
            print(f"ERROR {p.name}: {exc}", file=sys.stderr)
            continue
        r.update(label.get(p.name, {}))
        results.append(r)

    json.dump(results, open("/tmp/cb/results.json", "w"), indent=1)
    print(f"analysed {len(results)} trajectories -> /tmp/cb/results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
