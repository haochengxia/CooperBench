#!/usr/bin/env python3
"""Dump every conflict hunk from the recorded coop pairs, and size them.

The deterministic ladder in ``remerge.py`` resolves none of them, so these are
real overlapping edits.  The question this answers is what KIND of overlap:
a deep design disagreement, or two agents independently wrapping the same call
site.  That distinction decides whether "merge repair" is a research problem or
a chore.
"""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import remerge  # noqa: E402

import cooperbench.eval.sandbox as S  # noqa: E402
from cooperbench.eval.backends import get_backend  # noqa: E402
from cooperbench.utils import get_image_name  # noqa: E402

DUMP = r"""
cd /workspace/repo
git checkout --force agent2 >/dev/null 2>&1
git merge agent1 --no-commit --no-ff >/dev/null 2>&1
for f in $(git diff --name-only --diff-filter=U); do
    echo "@@@FILE $f"
    cat "$f"
done
"""


def _split_hunks(text: str) -> list[dict]:
    """Pull <<<<<<< / ======= / >>>>>>> triples out of the conflicted files."""
    hunks, cur_file = [], None
    ours: list[str] = []
    theirs: list[str] = []
    state = None
    for line in text.splitlines():
        if line.startswith("@@@FILE "):
            cur_file, state = line[8:].strip(), None
            continue
        if line.startswith("<<<<<<< "):
            state, ours, theirs = "ours", [], []
            continue
        if line.startswith("=======") and state == "ours":
            state = "theirs"
            continue
        if line.startswith(">>>>>>> ") and state == "theirs":
            hunks.append(
                {
                    "file": cur_file,
                    "ours": "\n".join(ours),
                    "theirs": "\n".join(theirs),
                    "ours_lines": len(ours),
                    "theirs_lines": len(theirs),
                }
            )
            state = None
            continue
        if state == "ours":
            ours.append(line)
        elif state == "theirs":
            theirs.append(line)
    return hunks


def dump(pair: dict) -> dict:
    sb = None
    try:
        sb = get_backend("docker").create_sandbox(get_image_name(pair["repo"], pair["task_id"]), 600)
        S._write_patch(sb, "patch1.patch", S._filter_test_files(Path(pair["patch1"]).read_text()))
        S._write_patch(sb, "patch2.patch", S._filter_test_files(Path(pair["patch2"]).read_text()))
        S._setup_branches(sb)
        out = sb.exec("bash", "-c", DUMP).stdout_read()
        return {**pair, "hunks": _split_hunks(out)}
    except Exception as exc:  # noqa: BLE001
        return {**pair, "hunks": [], "error": str(exc)}
    finally:
        if sb is not None:
            try:
                sb.terminate()
            except Exception:  # noqa: BLE001
                pass


def main() -> int:
    pairs = remerge.collect(remerge.REPO_ROOT / "logs/net-coop", only_conflicts=True)
    results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futs = {pool.submit(dump, p): p for p in pairs}
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            results.append(r)
            print(f"[{i:>3}/{len(pairs)}] {r['repo']}/{r['task_id']} f{r['feature1']}_f{r['feature2']}: "
                  f"{len(r['hunks'])} hunk(s)")

    out = Path(__file__).resolve().parent.parent / "data" / "conflicts.json"
    out.write_text(json.dumps(results, indent=1))

    sizes = [max(h["ours_lines"], h["theirs_lines"]) for r in results for h in r["hunks"]]
    total = len(sizes)
    print(f"\n{total} conflict hunks across {len(results)} pairs")
    if total:
        sizes.sort()
        print(f"  median largest-side size: {sizes[total // 2]} lines")
        for cut in (1, 3, 10):
            n = sum(1 for s in sizes if s <= cut)
            print(f"  <= {cut:>2} line(s): {n:>3}/{total}  ({100 * n / total:.0f}%)")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
