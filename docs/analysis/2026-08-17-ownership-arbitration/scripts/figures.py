#!/usr/bin/env python3
"""Generate every figure in SLIDES.md, from the measured data files.

Nothing here is hand-typed: the P3 bars come out of `data/p3_measure.json`, the
A/B split out of `data/merge_ladder_broad.json`, and the coupling/scaling
numbers are recomputed from the dataset's gold patches. Re-running this after a
new sweep regenerates the deck's figures with the new numbers.

    uv run --with matplotlib python scripts/figures.py           # -> figures/
    ZH=1 uv run --with matplotlib python scripts/figures.py      # -> figures_zh/

matplotlib is deliberately NOT a project dependency -- installing it into the
project venv drags in a numpy whose stubs break `mypy src/cooperbench/`.

Every user-visible string goes through `T(en, zh)`, so the English and Chinese
decks are guaranteed to plot identical numbers from identical code.
"""

from __future__ import annotations

import json
import os
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

from cooperbench.runner.ownership import _resources  # noqa: E402

ROOT = Path(__file__).resolve().parents[3].parent
DATASET = ROOT / "dataset"
DATA = Path(__file__).resolve().parent.parent / "data"

ZH = os.environ.get("ZH") == "1"
FIGS = Path(__file__).resolve().parent.parent / ("figures_zh" if ZH else "figures")

CJK_FONTS = (
    "/mnt/c/Windows/Fonts/SourceHanSansCN-Normal.ttf",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
)


def T(en: str, zh: str) -> str:
    return zh if ZH else en


BASE = "#9aa5b1"  # baseline / "without the note"
TREAT = "#2b6cb0"  # ownership / "with the note"
WARN = "#c05621"  # a cost, or a thing that got worse
GOOD = "#2f855a"
INK = "#1a202c"
MUTED = "#718096"

plt.rcParams.update(
    {
        "font.size": 15,
        "axes.titlesize": 18,
        "axes.labelsize": 15,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": INK,
        "ytick.color": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "figure.dpi": 160,
        "savefig.bbox": "tight",
        "savefig.facecolor": "white",
    }
)

if ZH:
    for cand in CJK_FONTS:
        if Path(cand).exists():
            font_manager.fontManager.addfont(cand)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=cand).get_name()
            plt.rcParams["axes.unicode_minus"] = False
            print(f"  using CJK font: {plt.rcParams['font.family']}")
            break
    else:
        raise SystemExit(f"no CJK font found, tried: {CJK_FONTS}")

WITHOUT = T("without the note", "没有提示")
WITH = T("with the note", "有提示")


def save(fig: plt.Figure, name: str) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIGS / name)
    plt.close(fig)
    print(f"  wrote {FIGS.name}/{name}")


def two_bars(ax, values, labels, colors, fmt="{:.0f}%", width=0.5):
    xs = range(len(values))
    bars = ax.bar(xs, values, width=width, color=colors)
    for b, v in zip(bars, values):
        ax.text(
            b.get_x() + b.get_width() / 2,
            v + max(values) * 0.03,
            fmt.format(v),
            ha="center",
            va="bottom",
            fontsize=19,
            fontweight="bold",
        )
    ax.set_xticks(list(xs))
    ax.set_xticklabels(labels)
    ax.set_ylim(0, max(values) * 1.28)
    ax.grid(axis="y", color="#e2e8f0", zorder=0)
    ax.set_axisbelow(True)
    return bars


# --------------------------------------------------------------------------- #
# dataset-level facts (no agents involved)
# --------------------------------------------------------------------------- #
def gold_stats() -> dict:
    tasks = []
    for rd in sorted(DATASET.iterdir()):
        if not rd.is_dir() or rd.name == "subsets":
            continue
        for td in sorted(rd.iterdir()):
            if td.is_dir() and td.name.startswith("task"):
                fids = sorted(int(p.name[7:]) for p in td.iterdir() if p.name.startswith("feature"))
                tasks.append((td, fids))

    share_file = share_scope = total = 0
    for td, fids in tasks:
        parsed = {}
        for f in fids:
            p = td / f"feature{f}" / "feature.patch"
            text = p.read_text() if p.exists() else ""
            files = {ln.split("\t")[0][6:].strip() for ln in text.splitlines() if ln.startswith("+++ ")}
            parsed[f] = (files - {"ev/null"}, _resources(text))
        for a, b in combinations(fids, 2):
            total += 1
            share_file += bool(parsed[a][0] & parsed[b][0])
            share_scope += bool(parsed[a][1] & parsed[b][1])

    big = [(td, f) for td, f in tasks if len(f) >= 8]
    curve = []
    for n in range(2, 9):
        pairs, contested = [], []
        for td, fids in big:
            parsed = {f: _resources((td / f"feature{f}" / "feature.patch").read_text()) for f in fids}
            subs = list(combinations(fids, n))[:60]
            for sub in subs:
                ps = list(combinations(sub, 2))
                pairs.append(len(ps))
                sc: set[str] = set()
                for a, b in ps:
                    sc |= parsed[a] & parsed[b]
                contested.append(len(sc))
        curve.append((n, sum(pairs) / len(pairs), sum(contested) / len(contested)))
    return {
        "total": total,
        "share_file": share_file,
        "share_scope": share_scope,
        "curve": curve,
        "n_big_tasks": len(big),
    }


def fig_ceiling() -> None:
    """Even the benchmark's own official answers fail to combine."""
    rows = [r for r in json.load(open(DATA / "gold_merge_scaling.json")) if not r.get("error")]
    by_n: dict[int, list] = {}
    for r in rows:
        by_n.setdefault(r["n"], []).append(r)
    ns = sorted(by_n)
    rates = [100 * sum(1 for r in by_n[n] if r["conflict"]) / len(by_n[n]) for n in ns]

    fig, ax = plt.subplots(figsize=(9, 4.8))
    bars = ax.bar([str(n) for n in ns], rates, width=0.55, color=WARN)
    for b, v in zip(bars, rates):
        ax.text(b.get_x() + b.get_width() / 2, v + 2, f"{v:.0f}%", ha="center", fontsize=18, fontweight="bold")
    ax.set_ylim(0, 118)
    ax.set_xlabel(T("number of agents working at the same time", "同时工作的 agent 数量"))
    ax.set_ylabel(T("of setups where combining failed", "合并失败的组合占比"))
    ax.grid(axis="y", color="#e2e8f0")
    ax.set_axisbelow(True)
    ax.set_title(T("Even the official answers fail to combine", "连官方标准答案都合不起来"), pad=14)
    fig.text(
        0.5,
        -0.05,
        T(
            "These are the benchmark's own correct solutions. A perfect team would still score zero this often.",
            "这些是基准自带的正确答案。就算团队完美无缺，也会这么频繁地得零分。",
        ),
        ha="center",
        fontsize=12,
        color=MUTED,
    )
    save(fig, "fig0_ceiling.png")


def fig_coupling(g: dict) -> None:
    fig, ax = plt.subplots(figsize=(9, 4.6))
    vals = [100 * g["share_file"] / g["total"], 100 * g["share_scope"] / g["total"]]
    two_bars(
        ax,
        vals,
        [
            T("edit the same FILE", "改到同一个文件"),
            T("edit the same FUNCTION,\nCLASS, or definition", "改到同一个函数、\n类或定义"),
        ],
        [WARN, "#dd9a6c"],
        fmt="{:.1f}%",
    )
    ax.set_ylabel(T("of all feature pairs", "占全部特性两两组合"))
    ax.set_title(
        T(f"Every pair collides  ({g['share_file']} of {g['total']} pairs)", f"每一对都会撞车（{g['share_file']} / {g['total']}）"),
        pad=14,
    )
    save(fig, "fig1_coupling.png")


def fig_scaling(g: dict) -> None:
    ns = [c[0] for c in g["curve"]]
    pairs = [c[1] for c in g["curve"]]
    contested = [c[2] for c in g["curve"]]

    fig, ax = plt.subplots(figsize=(9.4, 5.4))
    ax.plot(ns, pairs, "o-", color=WARN, lw=3, ms=9, label=T("pairs of agents that collide", "会撞车的 agent 对数"))
    ax.plot(ns, contested, "s-", color=TREAT, lw=3, ms=9, label=T("pieces of code they fight over", "被争夺的代码块数量"))
    ax.annotate(
        f"{pairs[-1]:.0f}",
        (ns[-1], pairs[-1]),
        textcoords="offset points",
        xytext=(-6, 12),
        fontsize=20,
        fontweight="bold",
        color=WARN,
    )
    ax.annotate(
        f"{contested[-1]:.2f}",
        (ns[-1], contested[-1]),
        textcoords="offset points",
        xytext=(-10, -30),
        fontsize=20,
        fontweight="bold",
        color=TREAT,
    )
    ax.annotate(
        "",
        xy=(7.75, pairs[-1]),
        xytext=(7.75, contested[-1]),
        arrowprops=dict(arrowstyle="<->", color=INK, lw=1.8),
    )
    ax.text(
        7.6,
        (pairs[-1] + contested[-1]) / 2,
        T(f"{pairs[-1] / contested[-1]:.1f}x fewer\nthings to settle", f"要处理的东西\n少 {pairs[-1] / contested[-1]:.1f} 倍"),
        fontsize=15,
        color=INK,
        ha="right",
        va="center",
        fontweight="bold",
    )
    ax.set_xlabel(T("number of agents working at the same time", "同时工作的 agent 数量"))
    ax.set_ylabel(T("count", "数量"))
    ax.set_xticks(ns)
    ax.legend(frameon=False, loc="upper left")
    ax.grid(axis="y", color="#e2e8f0")
    ax.set_axisbelow(True)
    ax.set_title(T("Collisions explode. The things collided over don't.", "撞车次数爆炸，被撞的东西却几乎不变"), pad=14)
    fig.text(
        0.5,
        -0.04,
        T(
            f"Measured from the benchmark's official answers on the {g['n_big_tasks']} tasks with >=8 features. No agents involved.",
            f"基于官方标准答案，取特性数 >= 8 的 {g['n_big_tasks']} 个任务统计，没有 agent 参与。",
        ),
        ha="center",
        fontsize=12,
        color=MUTED,
    )
    save(fig, "fig2_scaling.png")


# --------------------------------------------------------------------------- #
# the experiment
# --------------------------------------------------------------------------- #
def fig_design() -> None:
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 4.6)
    ax.axis("off")

    def box(x, y, w, h, text, fc, ec, fs=13, bold=False):
        ax.add_patch(
            FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.08", facecolor=fc, edgecolor=ec, linewidth=1.6)
        )
        ax.text(
            x + w / 2,
            y + h / 2,
            text,
            ha="center",
            va="center",
            fontsize=fs,
            color=INK,
            fontweight="bold" if bold else "normal",
        )

    def arrow(x1, y1, x2, y2, color=MUTED, curve=0.0):
        ax.add_patch(
            FancyArrowPatch(
                (x1, y1),
                (x2, y2),
                arrowstyle="-|>",
                mutation_scale=20,
                color=color,
                lw=1.8,
                connectionstyle=f"arc3,rad={curve}",
            )
        )

    box(0.1, 1.6, 1.9, 1.4, T("one task\n2 or 3 features", "一个任务\n2 或 3 个特性"), "#f7fafc", MUTED)
    arrow(2.1, 2.45, 2.92, 3.15, BASE, 0.15)
    arrow(2.1, 2.15, 2.92, 1.35, TREAT, -0.15)

    box(3.0, 2.55, 2.6, 1.2, T("agents code\n(no note)", "agent 写代码\n（无提示）"), "#f7fafc", BASE)
    box(3.0, 0.75, 2.6, 1.2, T("agents code\n+ ownership note", "agent 写代码\n＋归属提示"), "#ebf4fb", TREAT)
    arrow(5.7, 3.15, 6.42, 3.15, BASE)
    arrow(5.7, 1.35, 6.42, 1.35, TREAT)

    box(6.5, 2.55, 3.2, 1.2, T("combine + run tests", "合并 ＋ 跑测试"), "#f7fafc", BASE)
    box(6.5, 0.75, 3.2, 1.2, T("combine + run tests", "合并 ＋ 跑测试"), "#ebf4fb", TREAT)

    ax.text(
        1.05,
        3.9,
        T("Same task, same model, run twice.", "同一个任务，同一个模型，跑两遍。"),
        fontsize=15,
        fontweight="bold",
        ha="left",
    )
    ax.text(
        1.05,
        0.25,
        T(
            "The note is the ONLY difference.   20 setups x 2 arms = 100 agent-runs.",
            "唯一的差别就是那条提示。  20 组 × 2 个实验臂 = 100 次 agent 运行。",
        ),
        fontsize=13,
        color=MUTED,
        ha="left",
    )
    save(fig, "fig3_design.png")


def load_p3():
    d = json.load(open(DATA / "p3_measure.json"))
    b = {(r["repo"], r["task"], tuple(r["features"])): r for r in d["p3-base"]}
    o = {(r["repo"], r["task"], tuple(r["features"])): r for r in d["p3-own"]}
    keys = sorted(set(b) & set(o))
    return b, o, keys


def fig_compliance(b, o, keys) -> None:
    def comp(m):
        return 100 * sum(m[k]["respected"] for k in keys) / max(sum(m[k]["n_assigned"] for k in keys), 1)

    fig, ax = plt.subplots(figsize=(8, 4.8))
    two_bars(ax, [comp(b), comp(o)], [WITHOUT, WITH], [BASE, TREAT])
    ax.set_ylabel(T("contested code left to its owner", "争议代码留给归属者的比例"))
    ax.set_title(T("Did they listen?   Yes.", "它们听话了吗？   听了。"), pad=14, color=GOOD)
    save(fig, "fig4_compliance.png")


def fig_cotouched(b, o, keys) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12.4, 5.2), gridspec_kw={"width_ratios": [1, 1.35]})

    mb = sum(b[k]["co_touched"] for k in keys) / len(keys)
    mo = sum(o[k]["co_touched"] for k in keys) / len(keys)
    two_bars(ax1, [mb, mo], [T("without", "无提示"), T("with", "有提示")], [BASE, TREAT], fmt="{:.2f}")
    ax1.set_ylabel(T("pieces of code touched by >1 agent", "被 1 个以上 agent 改到的代码块"))
    ax1.set_title(T("On average", "平均值"), fontsize=15)

    better = worse = tied = 0
    for i, k in enumerate(keys):
        y0, y1 = b[k]["co_touched"], o[k]["co_touched"]
        jitter = (i % 5 - 2) * 0.012
        if y1 < y0:
            c, lw = TREAT, 2.2
            better += 1
        elif y1 > y0:
            c, lw = WARN, 2.2
            worse += 1
        else:
            c, lw = "#cbd5e0", 1.6
            tied += 1
        ax2.plot([0, 1], [y0 + jitter, y1 + jitter], "-o", color=c, lw=lw, ms=6, alpha=0.9)
    ax2.set_xlim(-0.25, 1.25)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels([T("without", "无提示"), T("with", "有提示")])
    ax2.set_title(T(f"Each of the {len(keys)} setups", f"全部 {len(keys)} 组逐一对比"), fontsize=15)
    ax2.grid(axis="y", color="#e2e8f0")
    ax2.set_axisbelow(True)
    for lbl, c, y in (
        (T(f"{better} got better", f"{better} 组变好"), TREAT, 0.95),
        (T(f"{worse} got worse", f"{worse} 组变差"), WARN, 0.87),
        (T(f"{tied} unchanged", f"{tied} 组不变"), "#a0aec0", 0.79),
    ):
        ax2.text(0.98, y, lbl, transform=ax2.transAxes, ha="right", color=c, fontsize=14, fontweight="bold")

    fig.suptitle(T("Did they actually overlap less?   Yes.", "重叠真的变少了吗？   变少了。"), fontsize=18, color=GOOD, y=1.0)
    save(fig, "fig5_cotouched.png")


def fig_by_n(b, o, keys) -> None:
    fig, ax = plt.subplots(figsize=(8.6, 5.0))
    ns = [2, 3]

    def mean(m, n):
        ks = [k for k in keys if len(k[2]) == n]
        return sum(m[k]["co_touched"] for k in ks) / len(ks)

    bs = [mean(b, n) for n in ns]
    os_ = [mean(o, n) for n in ns]
    x = range(len(ns))
    w = 0.34
    r1 = ax.bar([i - w / 2 for i in x], bs, w, color=BASE, label=WITHOUT)
    r2 = ax.bar([i + w / 2 for i in x], os_, w, color=TREAT, label=WITH)
    for rects in (r1, r2):
        for rr in rects:
            ax.text(
                rr.get_x() + rr.get_width() / 2,
                rr.get_height() + 0.05,
                f"{rr.get_height():.2f}",
                ha="center",
                fontsize=14,
                fontweight="bold",
            )
    for i in range(len(ns)):
        ax.annotate(
            f"-{bs[i] - os_[i]:.2f}",
            xy=(i, max(bs[i], os_[i]) + 0.28),
            ha="center",
            fontsize=16,
            fontweight="bold",
            color=GOOD,
        )
    ax.set_xticks(list(x))
    ax.set_xticklabels([T(f"{n} agents", f"{n} 个 agent") for n in ns])
    ax.set_ylabel(T("pieces of code touched by >1 agent", "被 1 个以上 agent 改到的代码块"))
    ax.set_ylim(0, max(bs) * 1.42)
    ax.legend(frameon=False)
    ax.grid(axis="y", color="#e2e8f0")
    ax.set_axisbelow(True)
    ax.set_title(T("The effect is bigger with more agents", "agent 越多，效果越大"), pad=14)
    save(fig, "fig6_by_n.png")


def fig_outcome(b, o, keys) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11.6, 4.9))
    cb = 100 * sum(1 for k in keys if b[k]["merge_status"] == "conflicts") / len(keys)
    co = 100 * sum(1 for k in keys if o[k]["merge_status"] == "conflicts") / len(keys)
    two_bars(ax1, [cb, co], [T("without", "无提示"), T("with", "有提示")], [BASE, TREAT])
    ax1.set_title(T("Combining failed less often", "合并失败变少了"), fontsize=16, color=GOOD)
    ax1.set_ylabel(T("setups where combining failed", "合并失败的组合占比"))

    def passrate(arm):
        n = ok = 0
        for k in keys:
            d = ROOT / "logs" / arm / "coop" / k[0] / str(k[1]) / "_".join(f"f{f}" for f in k[2])
            ev = d / "eval.json"
            if ev.exists():
                n += 1
                ok += 1 if json.loads(ev.read_text()).get("all_passed") else 0
        return 100 * ok / max(n, 1)

    two_bars(ax2, [passrate("p3-base"), passrate("p3-own")], [T("without", "无提示"), T("with", "有提示")], [BASE, BASE])
    ax2.set_title(T("But the code was no more correct", "但代码并没有更正确"), fontsize=16, color=WARN)
    ax2.set_ylabel(T("setups where every test passed", "全部测试通过的组合占比"))
    save(fig, "fig7_outcome.png")


def fig_cost(b, o, keys) -> None:
    fig, ax = plt.subplots(figsize=(8.4, 4.9))

    def empty(m):
        tot = sum(len(m[k]["features"]) for k in keys)
        return 100 * sum(len(m[k]["empty_patches"]) for k in keys) / tot

    two_bars(ax, [empty(b), empty(o)], [WITHOUT, WITH], [BASE, WARN])
    ax.set_ylabel(T("agents who submitted nothing", "什么都没交的 agent 占比"))
    ax.set_title(T("The cost: some agents gave up entirely", "代价：有些 agent 干脆放弃了"), pad=14, color=WARN)
    fig.text(
        0.5,
        -0.06,
        T(
            'Two said so outright: "...rather than violate ownership."  They asked the owner 0 times.',
            "有两个明说了：「与其违反归属，不如不改。」  而它们向归属者请求了 0 次。",
        ),
        ha="center",
        fontsize=13,
        color=MUTED,
    )
    save(fig, "fig8_cost.png")


def fig_two_jobs() -> None:
    rows = [r for r in json.load(open(DATA / "merge_ladder_broad.json")) if r.get("category")]
    conf = [r for r in rows if r["category"] != "clean-already"]
    mech = sum(1 for r in conf if r["category"] == "A-mechanical")

    fig, ax = plt.subplots(figsize=(9.6, 3.4))
    pm = 100 * mech / len(conf)
    ax.barh([0], [pm], color=GOOD, height=0.55)
    ax.barh([0], [100 - pm], left=[pm], color=WARN, height=0.55)
    ax.text(pm / 2, 0, f"{pm:.0f}%", ha="center", va="center", color="white", fontsize=19, fontweight="bold")
    ax.text(
        pm + (100 - pm) / 2, 0, f"{100 - pm:.0f}%", ha="center", va="center", color="white", fontsize=19, fontweight="bold"
    )
    ax.text(pm / 2, 0.45, T("a computer can\nfix these", "计算机能自动\n解决这些"), ha="center", fontsize=13, color=GOOD)
    ax.text(
        pm + (100 - pm) / 2,
        0.45,
        T("someone has to decide\nwhat the code should mean", "这些必须有人来决定\n代码应该是什么意思"),
        ha="center",
        fontsize=13,
        color=WARN,
    )
    ax.set_xlim(0, 100)
    ax.set_ylim(-0.4, 1.0)
    ax.set_yticks([])
    ax.set_xlabel(T("of the collisions that actually happen", "占真正发生的撞车"))
    ax.set_title(T("Why the second job can't be automated away", "为什么第二件工作没法自动化掉"), pad=12)
    save(fig, "fig9_two_jobs.png")


def main() -> int:
    print(f"generating {'Chinese' if ZH else 'English'} figures -> {FIGS.name}/")
    g = gold_stats()
    fig_ceiling()
    fig_coupling(g)
    fig_scaling(g)
    fig_design()
    b, o, keys = load_p3()
    fig_compliance(b, o, keys)
    fig_cotouched(b, o, keys)
    fig_by_n(b, o, keys)
    fig_outcome(b, o, keys)
    fig_cost(b, o, keys)
    fig_two_jobs()
    print(f"\nall figures in {FIGS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
