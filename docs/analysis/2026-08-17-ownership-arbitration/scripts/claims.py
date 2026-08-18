#!/usr/bin/env python3
"""Can a plan-time claim collision predict the co-definition that actually happens?

This is the detection problem proper.  At plan time the symbols do not exist yet
-- both agents merely *intend* to add `max_colors` -- so this is matching stated
intent in natural language, not intersecting code.  If it works, arbitration can
fire before either agent has written anything, which is the only point where
deciding who owns what is still free.

  claim index   symbol -> {agents who said they would define it}
  collision     len(claimants) > 1
  ground truth  symbol actually defined by >=2 agents, from their edits

Reported as precision/recall against ground truth, plus **lead time**: how far
before the first real co-definition the collision was visible.  A detector with
no lead time is useless no matter how accurate.

Two extraction strengths, because agents write plans both ways:

  strict   symbols in a clause carrying a first-person claim marker
           ("I'll add `foo`", "I'm taking `Bar.baz`")
  loose    every code-context symbol in a message that opens with a claim
           lead-in ("I'll be modifying:" followed by a bullet list)

IMPORTANT: published `agentId` is a renumbering (agent_1/agent_2) of the true
feature-derived ids used inside message bodies ("From agent_9"), per the
2026-08-16 review.  Claims are misattributed without mapping one to the other.
"""

from __future__ import annotations

import gzip
import json
import re
import statistics
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
INTER = re.compile(r"^\s*\[Inter-agent message\] From (\w+)")
WHOAMI = re.compile(r"^\s*You are (\w+) working on")

# A symbol only counts when it appears as code, never as prose -- the
# stale-premise analysis was burned by "Add Editor Environment Isolation"
# scoring as a claim on `Editor`.
BACKTICK = re.compile(r"`([^`\n]+)`")
CALL = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
ATTR = re.compile(r"\.([A-Za-z_]\w*)\b")
IDENT_IN = re.compile(r"[A-Za-z_]\w*")

CLAIM = re.compile(
    r"\b(?:I'?ll|I will|I'?m|I am|I'?ve|I have|I plan|I intend|I plan to|my|mine|"
    r"I'?d|taking|claiming|I own|owned by me|assigned to me|on my side)\b",
    re.I,
)
CLAIM_LEADIN = re.compile(
    r"\b(?:I'?ll be (?:modifying|editing|touching|adding|changing)|I'?m taking|"
    r"I will (?:modify|edit|touch|add|change)|files I (?:will|'ll) touch|"
    r"my (?:plan|changes|edits|files|scope)|I'?m going to)\b",
    re.I,
)


def keep(sym: str) -> bool:
    return len(sym) > 3 and sym not in STOP and not sym.startswith("__")


def ts(e: dict) -> float:
    raw = e.get("timestamp")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def code_symbols(text: str) -> set[str]:
    """Identifiers appearing as code, not as prose."""
    out: set[str] = set()
    for m in BACKTICK.finditer(text):
        for tok in IDENT_IN.findall(m.group(1)):
            if keep(tok):
                out.add(tok)
    for pattern in (CALL, ATTR, DECL):
        for m in pattern.finditer(text):
            if keep(m.group(1)):
                out.add(m.group(1))
    return out


def clauses(body: str) -> list[str]:
    """Split a message into claim-scopable units (lines, then sentences)."""
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        out.extend(p for p in re.split(r"(?<=[.;:!?])\s+", line) if p)
    return out


def analyse(events: list[dict]) -> dict | None:
    events = sorted(events, key=ts)

    # --- map renumbered agentId <-> true feature-derived id -------------------
    true_of: dict[str, str] = {}
    for e in events:
        if e.get("action") == "message" and e.get("agentId"):
            m = WHOAMI.match(e.get("message") or "")
            if m and e["agentId"] not in true_of:
                true_of[e["agentId"]] = m.group(1)
    id_of = {v: k for k, v in true_of.items()}

    # What each agent's own SPEC mentions -- available at t=0, before any
    # communication.  If the collision is already visible here, arbitration
    # never needs the agents to talk at all.
    spec: dict[str, set[str]] = defaultdict(set)
    # Everything ever said in an inter-agent message, by anyone: the recall
    # ceiling for any message-based detector, however clever.
    said: set[str] = set()

    claims_strict: dict[str, dict[str, float]] = defaultdict(dict)
    claims_loose: dict[str, dict[str, float]] = defaultdict(dict)
    defines: dict[str, dict[str, float]] = defaultdict(dict)
    n_msgs = 0
    unmapped = 0

    for e in events:
        agent, action = e.get("agentId"), e.get("action")
        if not agent:
            continue
        t = ts(e)
        args = e.get("args") or {}

        if action == "message":
            body = e.get("message") or args.get("content") or ""
            if WHOAMI.match(body):
                spec[agent] |= code_symbols(body)
                continue
            m = INTER.match(body)
            if not m:
                continue
            said |= code_symbols(body)
            n_msgs += 1
            sender = id_of.get(m.group(1))
            if sender is None:
                unmapped += 1
                continue
            for c in clauses(body):
                if CLAIM.search(c):
                    for s in code_symbols(c):
                        claims_strict[sender].setdefault(s, t)
            if CLAIM_LEADIN.search(body):
                for s in code_symbols(body):
                    claims_loose[sender].setdefault(s, t)
            for s, tt in claims_strict[sender].items():
                claims_loose[sender].setdefault(s, tt)

        elif action == "edit":
            new = (args.get("new_str") or "") + "\n" + (args.get("file_text") or "")
            for pattern in (DECL, KWARG):
                for mm in pattern.finditer(new):
                    if keep(mm.group(1)):
                        defines[agent].setdefault(mm.group(1), t)

    agents = sorted(set(list(defines) + list(true_of)))
    if len(agents) < 2:
        return None
    a1, a2 = agents[0], agents[1]

    truth = set(defines[a1]) & set(defines[a2])
    truth_time = {s: max(defines[a1][s], defines[a2][s]) for s in truth}

    def collisions(idx: dict[str, dict[str, float]]) -> dict[str, float]:
        common = set(idx.get(a1, {})) & set(idx.get(a2, {}))
        return {s: max(idx[a1][s], idx[a2][s]) for s in common}

    spec_collide = spec[a1] & spec[a2]
    res = {
        "n_msgs": n_msgs,
        "unmapped_senders": unmapped,
        "mapped_agents": len(true_of),
        "truth": len(truth),
        # ceilings
        "truth_said": len(truth & said),
        "truth_in_spec_collide": len(truth & spec_collide),
        "spec_collisions": len(spec_collide),
        "spec_hits": len(truth & spec_collide),
    }
    for name, idx in (("strict", claims_strict), ("loose", claims_loose)):
        col = collisions(idx)
        hit = set(col) & truth
        leads = [truth_time[s] - col[s] for s in hit if truth_time[s] > col[s]]
        res[f"{name}_claimed"] = len(set(idx.get(a1, {})) | set(idx.get(a2, {})))
        res[f"{name}_collisions"] = len(col)
        res[f"{name}_hits"] = len(hit)
        res[f"{name}_early"] = len(leads)
        res[f"{name}_lead_s"] = statistics.median(leads) if leads else 0.0
    return res


def main() -> int:
    labels = json.load(open(LABELS))
    rows = []
    for p in sorted(POP.glob("*.json.gz")):
        try:
            events = json.load(gzip.open(p, "rt"))
        except Exception:
            continue
        r = analyse(events)
        if not r:
            continue
        meta = labels.get(p.name, {})
        if meta.get("passed") is None:
            continue
        rows.append({"file": p.name, "model": meta["model"], "passed": meta["passed"], **r})

    print(f"N={len(rows)} pairs")
    tot_msgs = sum(r["n_msgs"] for r in rows)
    unmapped = sum(r["unmapped_senders"] for r in rows)
    print(f"inter-agent messages: {tot_msgs}   unmapped senders: {unmapped} "
          f"({100 * unmapped / max(tot_msgs, 1):.1f}%)")
    no_msgs = sum(1 for r in rows if r["n_msgs"] == 0)
    print(f"pairs with zero inter-agent messages: {no_msgs} ({100 * no_msgs / len(rows):.0f}%)")

    truth_tot = sum(r["truth"] for r in rows)
    said_tot = sum(r["truth_said"] for r in rows)
    print(f"\nground truth co-defined symbols: {truth_tot} across {len(rows)} pairs")
    print("\n--- CEILINGS: is the information even there? ---")
    print(f"  co-defined symbols ever mentioned in ANY inter-agent message: "
          f"{said_tot}/{truth_tot} = {100 * said_tot / max(truth_tot, 1):.1f}%")
    print("    ^ hard recall ceiling for ANY message-based detector")
    sp = sum(r["spec_hits"] for r in rows)
    spc = sum(r["spec_collisions"] for r in rows)
    print(f"  co-defined symbols already colliding in the two SPECS at t=0:  "
          f"{sp}/{truth_tot} = {100 * sp / max(truth_tot, 1):.1f}%")
    print(f"    ^ needs no communication at all; {spc} spec-collisions predicted, "
          f"precision {100 * sp / max(spc, 1):.1f}%")

    print(f"\n{'mode':<8}{'claimed':>9}{'collisions':>12}{'hits':>7}{'precision':>11}{'recall':>9}"
          f"{'early':>7}{'lead(s)':>9}")
    print("-" * 72)
    for mode in ("strict", "loose"):
        cl = sum(r[f"{mode}_claimed"] for r in rows)
        co = sum(r[f"{mode}_collisions"] for r in rows)
        hi = sum(r[f"{mode}_hits"] for r in rows)
        ea = sum(r[f"{mode}_early"] for r in rows)
        leads = [r[f"{mode}_lead_s"] for r in rows if r[f"{mode}_lead_s"] > 0]
        print(
            f"{mode:<8}{cl:>9}{co:>12}{hi:>7}{100 * hi / max(co, 1):>10.1f}%"
            f"{100 * hi / max(truth_tot, 1):>8.1f}%{ea:>7}"
            f"{statistics.median(leads) if leads else 0:>9.0f}"
        )

    print("\n--- pair-level: does a plan-time collision flag a pair that co-defines? ---")
    base = 100 * sum(1 for r in rows if r["truth"] > 0) / len(rows)
    print(f"  base rate (pairs that co-define at all): {base:.1f}% -- beat this, not 50%")
    for mode in ("strict", "loose", "spec"):
        key = "spec_collisions" if mode == "spec" else f"{mode}_collisions"
        tp = sum(1 for r in rows if r[key] > 0 and r["truth"] > 0)
        fp = sum(1 for r in rows if r[key] > 0 and r["truth"] == 0)
        fn = sum(1 for r in rows if r[key] == 0 and r["truth"] > 0)
        tn = sum(1 for r in rows if r[key] == 0 and r["truth"] == 0)
        print(
            f"  {mode:<7} fires on {100 * (tp + fp) / len(rows):5.1f}% of pairs   "
            f"precision {100 * tp / max(tp + fp, 1):5.1f}% ({100 * tp / max(tp + fp, 1) - base:+5.1f} vs base)"
            f"   recall {100 * tp / max(tp + fn, 1):5.1f}%"
            f"   (tp={tp} fp={fp} fn={fn} tn={tn})"
        )

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(OUT / "claims.json", "w"), indent=1)
    print(f"\nwrote {OUT / 'claims.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
