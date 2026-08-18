#!/usr/bin/env python3
"""Stale-premise accounting for coop pairs, over the full published population.

A *premise* is something one agent's code stands on: a symbol it references but
does not define, or a line range it read.  A premise is *falsified* when the
partner later changes that symbol's definition.  It is *repaired* if the owner
edits the dependent file after the falsification, and *stale* if it does not.

Three measures, head-to-head:

  V1 view-crossing   read-range n partner-write-range, time-ordered (the literal
                     "code view" proposal)
  V2 symbol-premise  reference n partner-redefinition, time-ordered (directed
                     R/W staleness)
  V3 absorption      both agents introduce the same new identifier (baseline
                     from the 2026-08-16 review; no time, no direction)

Every stale premise is then attributed to *why* it stayed stale, which is what
separates the candidate interventions:

  no_chance   owner had already stopped acting when the contract changed
              -> only a post-hoc round or reconciler can fix this
  uninformed  owner still active, but no partner message arrived afterwards
              -> a visibility channel could fix this
  informed    owner still active AND received a partner message afterwards,
              and still never revisited the dependent file
  named       ... and that message literally names the falsified symbol
              -> belief-revision failure; more channel cannot fix this

Source data is the full population (not outcome-stratified), so absolute rates
transfer as well as odds ratios.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

POP = Path("/tmp/cb/pop")
LABELS = Path("/tmp/cb/pop_labels.json")
OUT = Path(__file__).resolve().parent.parent / "data"

# Same stoplist as absorption.py, so V2/V3 stay comparable.
STOP = set(
    """None True False bool int str float list dict set tuple type self cls args kwargs
__init__ __call__ __str__ __repr__ __enter__ __exit__ return yield import from class def func
value values result results data item items name names key keys index count size length
error err exc exception test tests assert print format text content path file line lines
Optional Callable Union Any List Dict Set Tuple Iterator Sequence Mapping default options
config params param options kwargs opts obj node ctx context request response""".split()
)

DECL = re.compile(r"\b(?:def|func|class|type)\s+(\w+)")
SIG = re.compile(r"\b(?:def|func)\s+(\w+)\s*\(([^)]*)\)", re.S)
KWARG = re.compile(r"\b(\w+)\s*(?::\s*[\w\[\]\.\|\s]+)?\s*=\s*(?:None|False|True|0|1\.0|1|\"\"|'')")
IDENT = re.compile(r"\b[A-Za-z_]\w*\b")
# Anchored: the initial task prompt also *mentions* the inter-agent format, and
# is ~19k chars of feature prose.  Only a body that *begins* with the tag is a
# real partner message.
INTER = re.compile(r"^\s*\[Inter-agent message\] From (\w+)")


def names_symbol(body: str, sym: str) -> bool:
    """True if `body` mentions `sym` in a code context, not merely in prose.

    A feature title like "Add Editor Environment Isolation" should not count as
    telling the partner that `Editor`'s contract changed.
    """
    s = re.escape(sym)
    return bool(
        re.search(
            rf"`[^`\n]*\b{s}\b[^`\n]*`|\b{s}\s*\(|\.\s*{s}\b|\b(?:def|class|func)\s+{s}\b", body
        )
    )


def ts(event: dict) -> float:
    raw = event.get("timestamp")
    if not raw:
        return 0.0
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        return 0.0


def norm(path: str | None) -> str:
    if not path:
        return ""
    p = path.replace("\\", "/")
    for marker in ("/workspace/repo/", "/workspace/"):
        if marker in p:
            p = p.split(marker, 1)[1]
    return p.lstrip("/")


def keep(sym: str) -> bool:
    return len(sym) > 3 and sym not in STOP and not sym.startswith("__")


def declared(text: str) -> set[str]:
    return {m.group(1) for m in DECL.finditer(text) if keep(m.group(1))}


def signatures(text: str) -> dict[str, str]:
    return {m.group(1): " ".join(m.group(2).split()) for m in SIG.finditer(text) if keep(m.group(1))}


def odds_ratio(rows, pred):
    a = sum(1 for r in rows if pred(r) and r["passed"])
    b = sum(1 for r in rows if pred(r) and not r["passed"])
    c = sum(1 for r in rows if not pred(r) and r["passed"])
    d = sum(1 for r in rows if not pred(r) and not r["passed"])
    aa, bb, cc, dd = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    o = (aa * dd) / (bb * cc)
    se = math.sqrt(1 / aa + 1 / bb + 1 / cc + 1 / dd)
    return a, b, c, d, o, math.exp(math.log(o) - 1.96 * se), math.exp(math.log(o) + 1.96 * se)


def analyse(events: list[dict]) -> dict:
    events = sorted(events, key=ts)

    depends: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
    defines: dict[str, dict[str, tuple[float, str]]] = defaultdict(dict)
    last_edit: dict[str, dict[str, float]] = defaultdict(dict)
    last_act: dict[str, float] = defaultdict(float)
    views: dict[str, list[tuple[str, int, int, float]]] = defaultdict(list)
    writes: dict[str, list[tuple[str, int, int, float]]] = defaultdict(list)
    introduced: dict[str, set[str]] = defaultdict(set)
    edited_paths: dict[str, set[str]] = defaultdict(set)
    # agent -> [(time, text)] of messages *received* from the partner
    inbox: dict[str, list[tuple[float, str]]] = defaultdict(list)
    # (agent, symbol) -> (old params, new params) for signature rewrites
    sig_change: dict[tuple[str, str], tuple[str, str]] = {}

    for e in events:
        agent, action, args = e.get("agentId"), e.get("action"), e.get("args") or {}
        if not agent:
            continue
        t, path = ts(e), norm(args.get("path"))
        last_act[agent] = max(last_act[agent], t)

        if action == "message":
            body = e.get("message") or (args.get("content") or "")
            if INTER.search(body):
                inbox[agent].append((t, body))

        elif action == "read":
            lo, hi = args.get("start") or 1, args.get("end") or -1
            views[agent].append((path, int(lo), int(hi) if hi and hi > 0 else 10**9, t))

        elif action == "edit":
            old = args.get("old_str") or ""
            new = (args.get("new_str") or "") + "\n" + (args.get("file_text") or "")
            edited_paths[agent].add(path)
            last_edit[agent][path] = t

            for pattern in (DECL, KWARG):
                for m in pattern.finditer(new):
                    if keep(m.group(1)):
                        introduced[agent].add(m.group(1))

            lo = int(args.get("insert_line") or args.get("start") or 0)
            writes[agent].append((path, lo, lo + new.count("\n") + 1, t))

            new_defs, old_sigs, new_sigs = declared(new), signatures(old), signatures(new)
            for sym in new_defs:
                kind = "sig" if sym in old_sigs and old_sigs[sym] != new_sigs.get(sym) else "def"
                prev = defines[agent].get(sym)
                if prev is None or (kind == "sig" and prev[1] != "sig"):
                    defines[agent][sym] = (t, kind)
                if kind == "sig":
                    sig_change[(agent, sym)] = (old_sigs[sym], new_sigs.get(sym, ""))

            for m in IDENT.finditer(new):
                sym = m.group(0)
                if keep(sym) and sym not in new_defs and (sym, path) not in depends[agent]:
                    depends[agent][(sym, path)] = t

    agents = sorted(set(list(depends) + list(defines) + list(edited_paths)))
    if len(agents) < 2:
        return {}
    a1, a2 = agents[0], agents[1]

    # ---- V2: directed symbol-premise staleness + why it stayed stale --------
    falsified: list[dict] = []
    for owner, partner in ((a1, a2), (a2, a1)):
        for (sym, path), t_dep in depends[owner].items():
            hit = defines[partner].get(sym)
            if not hit:
                continue
            t_inv, kind = hit
            if t_inv <= t_dep:
                continue  # contract changed before the dependency formed
            if last_edit[owner].get(path, 0.0) > t_inv:
                why = "repaired"
            elif last_act[owner] <= t_inv:
                why = "no_chance"
            else:
                later = [b for tm, b in inbox[owner] if tm > t_inv]
                if not later:
                    why = "uninformed"
                else:
                    why = "named" if any(names_symbol(b, sym) for b in later) else "informed"
            falsified.append({"owner": owner, "sym": sym, "path": path, "kind": kind, "why": why})

    # ---- V1: view-crossing (the literal proposal) ---------------------------
    crossings = crossings_stale = 0
    for owner, partner in ((a1, a2), (a2, a1)):
        for vpath, vlo, vhi, t_view in views[owner]:
            for wpath, wlo, whi, t_write in writes[partner]:
                if vpath and vpath == wpath and t_write > t_view and wlo <= vhi and whi >= vlo:
                    crossings += 1
                    if last_edit[owner].get(vpath, 0.0) <= t_write:
                        crossings_stale += 1
                    break

    why = Counter(f["why"] for f in falsified)
    stale = [f for f in falsified if f["why"] != "repaired"]
    sig = [f for f in falsified if f["kind"] == "sig"]

    # ---- ownership: symbols BOTH agents define / re-sign ---------------------
    co_defined = set(defines[a1]) & set(defines[a2])
    co_sig = {s for s in co_defined if defines[a1][s][1] == "sig" and defines[a2][s][1] == "sig"}
    co_any_sig = {s for s in co_defined if "sig" in (defines[a1][s][1], defines[a2][s][1])}

    # ---- is a falsification actually breaking, or just an added kwarg? -------
    breaking = additive = 0
    for f in falsified:
        if f["kind"] != "sig":
            continue
        owner_partner = a2 if f["owner"] == a1 else a1
        old_p, new_p = sig_change.get((owner_partner, f["sym"]), ("", ""))
        o = {q.split("=")[0].split(":")[0].strip() for q in old_p.split(",") if q.strip()}
        n = {q.split("=")[0].split(":")[0].strip() for q in new_p.split(",") if q.strip()}
        if o - n:
            breaking += 1  # a parameter disappeared or was renamed
        else:
            additive += 1  # parameters only added -- callers keep working

    return {
        "co_defined": len(co_defined),
        "co_sig": len(co_sig),
        "co_any_sig": len(co_any_sig),
        "n_breaking": breaking,
        "n_additive": additive,
        "n_falsified": len(falsified),
        "n_stale": len(stale),
        "n_repaired": why["repaired"],
        "n_no_chance": why["no_chance"],
        "n_uninformed": why["uninformed"],
        "n_informed": why["informed"] + why["named"],
        "n_named": why["named"],
        "n_sig_falsified": len(sig),
        "n_sig_stale": sum(1 for f in sig if f["why"] != "repaired"),
        "n_crossings": crossings,
        "n_crossings_stale": crossings_stale,
        "shared": len(introduced[a1] & introduced[a2]),
        "co_files": len(edited_paths[a1] & edited_paths[a2]),
        "n_edits": sum(len(w) for w in writes.values()),
        "n_msgs": sum(len(v) for v in inbox.values()),
        "stale_syms": sorted({f"{f['owner']}:{f['sym']}" for f in stale})[:8],
    }


def main() -> int:
    labels = json.load(open(LABELS))
    rows = []
    for p in sorted(POP.glob("*.json.gz")):
        try:
            events = json.load(gzip.open(p, "rt"))
        except Exception:
            continue
        stats = analyse(events)
        if not stats:
            continue
        meta = labels.get(p.name, {})
        if meta.get("passed") is None:
            continue
        rows.append({"file": p.name, **{k: meta[k] for k in ("model", "passed", "hasConflict")}, **stats})

    def show(title, pred, subset=None):
        rs = rows if subset is None else subset
        if not rs:
            print(f"  {title:<46} (empty)")
            return
        a, b, c, d, o, lo, hi = odds_ratio(rs, pred)
        flag = "*" if (lo > 1 or hi < 1) else " "
        rate = 100 * (a + b) / len(rs)
        p_yes = 100 * a / max(a + b, 1)
        p_no = 100 * c / max(c + d, 1)
        print(
            f"{flag} {title:<46} {rate:5.1f}% of pairs | pass {p_yes:5.1f}% vs {p_no:5.1f}%"
            f"  OR={o:5.2f} [{lo:.2f},{hi:.2f}]"
        )

    base = 100 * sum(1 for r in rows if r["passed"]) / len(rows)
    print(f"N={len(rows)} pairs (full population, unstratified)   pooled pass rate {base:.1f}%")
    print("(* = 95% CI excludes 1; OR<1 => associated with FAILING)\n")

    print("--- V3 absorption (baseline) ---")
    for t in (1, 2, 3, 5):
        show(f">={t} shared new identifiers", lambda r, x=t: r["shared"] >= x)

    print("\n--- V1 view-crossing (the literal code-view proposal) ---")
    for t in (1, 5, 20):
        show(f">={t} read-ranges later overwritten by partner", lambda r, x=t: r["n_crossings"] >= x)
    for t in (1, 5):
        show(f">={t} stale view-crossings", lambda r, x=t: r["n_crossings_stale"] >= x)

    print("\n--- V2 symbol-premise staleness (directed R/W + time) ---")
    for t in (1, 2, 3, 5):
        show(f">={t} falsified premises", lambda r, x=t: r["n_falsified"] >= x)
    for t in (1, 2, 3, 5):
        show(f">={t} STALE premises", lambda r, x=t: r["n_stale"] >= x)
    show(">=1 signature-change falsification", lambda r: r["n_sig_falsified"] >= 1)
    show(">=1 STALE signature falsification", lambda r: r["n_sig_stale"] >= 1)

    coupled = [r for r in rows if r["n_falsified"] >= 1]
    print(f"\n--- REPAIR RATE | falsification present (n={len(coupled)}) ---")
    print("    conditions on coupling, so only agent behaviour varies")
    for thr in (0.5, 1.0):
        show(
            f"repaired >= {thr:.0%} of falsified premises",
            lambda r, x=thr: r["n_repaired"] / max(r["n_falsified"], 1) >= x,
            coupled,
        )
    show("zero repairs at all", lambda r: r["n_repaired"] == 0, coupled)

    print("\n--- WHY premises stayed stale (premise-level, n=%d) ---" % sum(r["n_falsified"] for r in rows))
    tot = sum(r["n_falsified"] for r in rows)
    for k, lbl in (
        ("n_repaired", "repaired (owner revisited the file)"),
        ("n_no_chance", "no_chance (owner already stopped)"),
        ("n_uninformed", "uninformed (active, no partner msg after)"),
        ("n_informed", "informed  (active, partner msg after)"),
        ("n_named", "  ... of which msg NAMES the symbol"),
    ):
        v = sum(r[k] for r in rows)
        print(f"    {lbl:<44} {v:6}  {100 * v / max(tot, 1):5.1f}%")

    print("\n--- OWNERSHIP: symbols BOTH agents define ---")
    for t in (1, 2):
        show(f">={t} co-defined symbols", lambda r, x=t: r["co_defined"] >= x)
    show(">=1 symbol whose signature BOTH changed", lambda r: r["co_sig"] >= 1)
    show(">=1 co-defined symbol, either re-signed", lambda r: r["co_any_sig"] >= 1)

    br = sum(r["n_breaking"] for r in rows)
    ad = sum(r["n_additive"] for r in rows)
    print(f"\n--- are falsifications actually breaking? (n={br + ad} sig falsifications) ---")
    print(f"    additive (params only added, callers keep working) {ad:5}  {100 * ad / max(br + ad, 1):5.1f}%")
    print(f"    breaking (a param disappeared or was renamed)      {br:5}  {100 * br / max(br + ad, 1):5.1f}%")
    show(">=1 BREAKING falsification", lambda r: r["n_breaking"] >= 1)

    clean = [r for r in rows if not r["hasConflict"]]
    print(f"\n--- within CLEAN merges only, where git is blind (n={len(clean)}) ---")
    show(">=1 shared new identifier (V3)", lambda r: r["shared"] >= 1, clean)
    show(">=1 stale premise (V2)", lambda r: r["n_stale"] >= 1, clean)
    show(">=2 stale premises (V2)", lambda r: r["n_stale"] >= 2, clean)

    print("\n--- does V2 add anything beyond V3? (stratified) ---")
    for lbl, subset in (
        ("absorption ABSENT", [r for r in rows if r["shared"] == 0]),
        ("absorption PRESENT", [r for r in rows if r["shared"] >= 1]),
    ):
        show(f"[{lbl}] >=1 stale premise", lambda r: r["n_stale"] >= 1, subset)
    for lbl, subset in (
        ("staleness ABSENT", [r for r in rows if r["n_stale"] == 0]),
        ("staleness PRESENT", [r for r in rows if r["n_stale"] >= 1]),
    ):
        show(f"[{lbl}] >=1 shared identifier", lambda r: r["shared"] >= 1, subset)

    print("\n--- per model ---")
    for m in sorted({r["model"] for r in rows}):
        sub = [r for r in rows if r["model"] == m]
        show(f"[{m}] >=1 stale premise", lambda r: r["n_stale"] >= 1, sub)

    OUT.mkdir(parents=True, exist_ok=True)
    json.dump(rows, open(OUT / "staleness_pop.json", "w"), indent=1)
    print(f"\nwrote {OUT / 'staleness_pop.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
