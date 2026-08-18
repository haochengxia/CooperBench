#!/usr/bin/env python3
"""Predict co-definition from the two feature SPECS alone, at t=0.

`claims.py` established the two ceilings that decide where detection should
live:

  * co-defined symbols ever mentioned in any inter-agent message: 47.8%
  * co-defined symbols already colliding in the two specs at t=0:  67.5%

The specs contain more of the collision than all the chatter does, and they are
available before either agent starts.  So message-based claim extraction is
strictly the worse place to detect -- lower ceiling and later.  What the specs
lack is *selectivity*: raw "symbols mentioned in both specs" fires on 100% of
pairs at 7.2% symbol precision, which is no better than always predicting a
collision.

This script is the baseline table for that detection problem: how much of the
67.5% is reachable at usable precision, using progressively more selective
extractors.  Ground truth is what the agents actually co-defined, from their
edits, so it is the same target `absorption` measures.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

POP = Path("/tmp/cb/pop")
LABELS = Path("/tmp/cb/pop_labels.json")
OUT = Path(__file__).resolve().parent.parent / "data"

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
WHOAMI = re.compile(r"^\s*You are (\w+) working on")
BACKTICK = re.compile(r"`([^`\n]+)`")
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
ATTR = re.compile(r"\.([A-Za-z_]\w*)\b")
IDENT_IN = re.compile(r"[A-Za-z_]\w*")

# The verbs a spec uses when it says the feature will *own* something, as
# opposed to merely referring to it.  Taken from the dataset's own phrasing:
# "Key changes include: - Updated the `filename` parameter ... - Modified the
# internal `edit_file()` method to `edit_files()`".
CHANGE = re.compile(
    r"\b(?:add(?:s|ed|ing)?|introduc(?:e|es|ed|ing)|creat(?:e|es|ed|ing)|"
    r"modif(?:y|ies|ied|ying)|updat(?:e|es|ed|ing)|extend(?:s|ed|ing)?|"
    r"renam(?:e|es|ed|ing)|replac(?:e|es|ed|ing)|implement(?:s|ed|ing)?|"
    r"expos(?:e|es|ed|ing)|new)\b",
    re.I,
)


def keep(s: str) -> bool:
    return len(s) > 3 and s not in STOP and not s.startswith("__")


def ts(e: dict) -> float:
    raw = e.get("timestamp")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def backticked(text: str) -> set[str]:
    out = set()
    for m in BACKTICK.finditer(text):
        for tok in IDENT_IN.findall(m.group(1)):
            if keep(tok):
                out.add(tok)
    return out


def code_symbols(text: str) -> set[str]:
    out = backticked(text)
    for pattern in (CALL, ATTR, DECL):
        for m in pattern.finditer(text):
            if keep(m.group(1)):
                out.add(m.group(1))
    return out


def clauses(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            out.extend(p for p in re.split(r"(?<=[.;:!?])\s+", line) if p)
    return out


def extractors(spec: str) -> dict[str, set[str]]:
    """Progressively more selective readings of what a spec claims to own."""
    change_text = " \n".join(c for c in clauses(spec) if CHANGE.search(c))
    return {
        "all-mentions": code_symbols(spec),
        "backticked": backticked(spec),
        "change-verb": code_symbols(change_text),
        "change+backtick": backticked(change_text),
    }


def analyse(events: list[dict]) -> dict | None:
    events = sorted(events, key=ts)
    specs: dict[str, str] = {}
    defines: dict[str, set[str]] = defaultdict(set)

    for e in events:
        agent, action = e.get("agentId"), e.get("action")
        if not agent:
            continue
        args = e.get("args") or {}
        if action == "message":
            body = e.get("message") or args.get("content") or ""
            if WHOAMI.match(body) and agent not in specs:
                specs[agent] = body
        elif action == "edit":
            new = (args.get("new_str") or "") + "\n" + (args.get("file_text") or "")
            for pattern in (DECL, KWARG):
                for m in pattern.finditer(new):
                    if keep(m.group(1)):
                        defines[agent].add(m.group(1))

    agents = sorted(set(list(defines) + list(specs)))
    if len(agents) < 2 or not all(a in specs for a in agents[:2]):
        return None
    a1, a2 = agents[0], agents[1]
    truth = defines[a1] & defines[a2]

    e1, e2 = extractors(specs[a1]), extractors(specs[a2])
    row: dict = {"truth": len(truth)}
    for name in e1:
        pred = e1[name] & e2[name]
        row[f"{name}|pred"] = len(pred)
        row[f"{name}|hit"] = len(pred & truth)
    return row


def main() -> int:
    labels = json.load(open(LABELS))
    rows = []
    for p in sorted(POP.glob("*.json.gz")):
        try:
            events = json.load(gzip.open(p, "rt"))
        except Exception:
            continue
        r = analyse(events)
        if r is None:
            continue
        meta = labels.get(p.name, {})
        if meta.get("passed") is None:
            continue
        rows.append({"file": p.name, "passed": meta["passed"], **r})

    truth_tot = sum(r["truth"] for r in rows)
    base = 100 * sum(1 for r in rows if r["truth"] > 0) / len(rows)
    print(f"N={len(rows)} pairs   ground-truth co-defined symbols={truth_tot}")
    print(f"pair-level base rate (pairs that co-define at all) = {base:.1f}%\n")

    names = ["all-mentions", "backticked", "change-verb", "change+backtick"]
    print("SYMBOL level")
    print(f"{'extractor':<18}{'predicted':>10}{'hits':>7}{'precision':>11}{'recall':>9}{'F1':>7}")
    print("-" * 62)
    for n in names:
        pr = sum(r[f"{n}|pred"] for r in rows)
        hi = sum(r[f"{n}|hit"] for r in rows)
        p = 100 * hi / max(pr, 1)
        rc = 100 * hi / max(truth_tot, 1)
        f1 = 2 * p * rc / max(p + rc, 1e-9)
        print(f"{n:<18}{pr:>10}{hi:>7}{p:>10.1f}%{rc:>8.1f}%{f1:>7.1f}")

    print("\nPAIR level (does the pair get flagged for arbitration at all?)")
    print(f"{'extractor':<18}{'fires':>8}{'precision':>11}{'vs base':>9}{'recall':>9}")
    print("-" * 56)
    for n in names:
        tp = sum(1 for r in rows if r[f"{n}|pred"] > 0 and r["truth"] > 0)
        fp = sum(1 for r in rows if r[f"{n}|pred"] > 0 and r["truth"] == 0)
        fn = sum(1 for r in rows if r[f"{n}|pred"] == 0 and r["truth"] > 0)
        p = 100 * tp / max(tp + fp, 1)
        print(
            f"{n:<18}{100 * (tp + fp) / len(rows):>7.1f}%{p:>10.1f}%"
            f"{p - base:>+9.1f}{100 * tp / max(tp + fn, 1):>8.1f}%"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(OUT / "spec_detect.json", "w"), indent=1)
    print(f"\nwrote {OUT / 'spec_detect.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
