#!/usr/bin/env python3
"""Generate final social-media-ready file-read waste charts."""

from __future__ import annotations

import math
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
import numpy as np

# --- Theme ---
BG = "#1e1e2e"
FG = "#ffffff"
CORAL = "#ff6b6b"      # wasteful/redundant
TEAL = "#4ecdc4"       # efficient/unique
BLUE = "#74b9ff"       # neutral/informational
ORANGE = "#fdcb6e"     # mid-tier
GRAY = "#7f8c8d"

WIDTH_PX = 1200
HEIGHT_PX = 675
DPI = 150
FIGSIZE = (WIDTH_PX / DPI, HEIGHT_PX / DPI)
WATERMARK = "meridian-flow • 4,339 agent sessions analyzed"

plt.rcParams.update(
    {
        "figure.facecolor": BG,
        "axes.facecolor": BG,
        "savefig.facecolor": BG,
        "text.color": FG,
        "axes.labelcolor": FG,
        "xtick.color": FG,
        "ytick.color": FG,
        "axes.edgecolor": "#3a3a4f",
        "font.family": "sans-serif",
        "font.sans-serif": ["DejaVu Sans", "Arial", "Liberation Sans"],
        "axes.titleweight": "bold",
    }
)


def _clean_axes(ax: plt.Axes, grid_y: bool = False) -> None:
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(length=0)
    if grid_y:
        ax.grid(axis="y", color="#40445f", alpha=0.35, linewidth=0.8)
        ax.set_axisbelow(True)


def _watermark(fig: plt.Figure) -> None:
    fig.text(0.99, 0.015, WATERMARK, ha="right", va="bottom", fontsize=8.5, color="#c7c9d9", alpha=0.85)


def _save(fig: plt.Figure, path: str) -> None:
    _watermark(fig)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)


def chart_1_headline(path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    values = [75.1, 24.9]
    colors = [CORAL, TEAL]
    ax.pie(
        values,
        startangle=95,
        counterclock=False,
        colors=colors,
        wedgeprops={"width": 0.33, "edgecolor": BG, "linewidth": 2},
    )
    ax.text(0, 0.03, "75.1%", ha="center", va="center", fontsize=42, weight="bold", color=FG)
    ax.text(0, -0.20, "redundant", ha="center", va="center", fontsize=13, color="#d8deff")
    ax.set_aspect("equal")

    fig.text(0.5, 0.93, "AI Agents Keep Reading the Same Files", ha="center", fontsize=26, weight="bold")
    fig.text(0.5, 0.875, "67,017 file reads analyzed across 4,339 AI agent sessions", ha="center", fontsize=12.5, color="#d8deff")
    fig.text(
        0.5,
        0.10,
        "16,698 unique files • 50,319 redundant re-reads • 3 months of data",
        ha="center",
        fontsize=12,
        color="#e5e8ff",
    )

    _save(fig, path)


def chart_2_delegation_tax(path: str) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=FIGSIZE, dpi=DPI, gridspec_kw={"width_ratios": [1.25, 1]})
    labels = ["Primary", "Subagent"]
    totals = [437, 15972]
    ratios = [5.5, 80.8]
    unique = [413, 3074]
    redundant = [24, 12898]

    x = np.arange(len(labels))
    ax1.bar(x, unique, color=TEAL, label="Unique reads", width=0.58)
    ax1.bar(x, redundant, bottom=unique, color=CORAL, label="Redundant reads", width=0.58)
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, fontsize=11)
    ax1.set_ylabel("File reads")
    ax1.set_title("Read Volume Composition", fontsize=14, pad=12)
    _clean_axes(ax1, grid_y=True)
    ax1.legend(loc="upper left", frameon=False, fontsize=10)
    for i, t in enumerate(totals):
        ax1.text(i, t + max(totals) * 0.015, f"{t:,}", ha="center", va="bottom", fontsize=10.5, color=FG)

    bars = ax2.bar(labels, ratios, color=[BLUE, CORAL], width=0.58)
    ax2.set_ylim(0, 100)
    ax2.set_ylabel("Redundant read rate (%)")
    ax2.set_title("Redundancy Ratio", fontsize=14, pad=12)
    _clean_axes(ax2, grid_y=True)
    for b, r in zip(bars, ratios):
        ax2.text(b.get_x() + b.get_width() / 2, r + 2.2, f"{r:.1f}%", ha="center", fontsize=11)

    fig.text(0.5, 0.93, "The Delegation Tax", ha="center", fontsize=26, weight="bold")
    fig.text(0.5, 0.86, "14.7× higher redundancy in delegated agents", ha="center", fontsize=13, color=ORANGE, weight="bold")

    _save(fig, path)


def chart_3_agent_roles(path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    data = [
        ("architect", 2767, 6672, 70.7),
        ("reviewer", 2767, 6024, 68.5),
        ("coder", 2172, 4915, 69.4),
        ("dev-orchestrator", 3178, 3476, 52.2),
        ("impl-orchestrator", 2683, 2504, 48.3),
        ("tech-lead", 1579, 1579, 50.0),
        ("design-orchestrator", 1323, 1743, 56.8),
        ("product-lead", 1653, 976, 37.1),
    ]
    data = sorted(data, key=lambda x: x[1] + x[2], reverse=True)

    roles = [r for r, _, _, _ in data][::-1]
    unique = [u for _, u, _, _ in data][::-1]
    redundant = [r for _, _, r, _ in data][::-1]
    ratios = [p for _, _, _, p in data][::-1]

    y = np.arange(len(roles))
    ax.barh(y, unique, color=TEAL, label="Unique")
    ax.barh(y, redundant, left=unique, color=CORAL, label="Redundant")
    ax.set_yticks(y)
    ax.set_yticklabels(roles, fontsize=11)
    ax.set_xlabel("Reads")
    _clean_axes(ax, grid_y=False)
    ax.grid(axis="x", color="#40445f", alpha=0.35, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(loc="lower right", frameon=False, fontsize=10)

    for yi, (u, r, p) in enumerate(zip(unique, redundant, ratios)):
        total = u + r
        ax.text(total + max(np.array(unique) + np.array(redundant)) * 0.012, yi, f"{p:.1f}%", va="center", fontsize=10.5, color="#ffd9d9")

    ax.set_xlim(0, max(np.array(unique) + np.array(redundant)) * 1.18)
    fig.text(0.5, 0.93, "File-Read Waste by Agent Role", ha="center", fontsize=25, weight="bold")

    _save(fig, path)


def chart_4_models(path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    models = [
        "GPT-5.4",
        "Claude Haiku 4.5",
        "Codex (GPT-5.3)",
        "Claude Opus 4-6",
        "Claude Sonnet 4-6",
        "Claude Opus 4-5",
        "Claude Sonnet 4-5",
        "GPT-5.4-mini",
        "GPT-5.5",
    ]
    ratios = [80.4, 74.7, 71.4, 64.6, 63.3, 57.3, 50.3, 48.1, 29.5]
    spawns = [636, 781, 140, 1204, 642, 198, 49, 80, 47]

    cmap = mcolors.LinearSegmentedColormap.from_list("waste_scale", [BLUE, CORAL])
    rmin, rmax = min(ratios), max(ratios)
    bar_colors = [cmap((r - rmin) / (rmax - rmin + 1e-9)) for r in ratios]

    x = np.arange(len(models))
    bars = ax.bar(x, ratios, color=bar_colors, width=0.65)
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=20, ha="right", fontsize=10.5)
    ax.set_ylim(0, 90)
    ax.set_ylabel("Redundant read rate (%)")
    _clean_axes(ax, grid_y=True)

    for i, (bar, ratio, spawn) in enumerate(zip(bars, ratios, spawns)):
        ax.text(bar.get_x() + bar.get_width() / 2, ratio + 1.8, f"{ratio:.1f}%", ha="center", fontsize=10.5)
        ax.text(bar.get_x() + bar.get_width() / 2, 3.0, f"n={spawn}", ha="center", fontsize=8.8, color="#e2e7ff", alpha=0.85)

    fig.text(0.5, 0.93, "Redundant Read Rate by Model", ha="center", fontsize=25, weight="bold")
    fig.text(
        0.72,
        0.80,
        "Primary: 5.5%\nSubagent: 80.8%\n(15× higher)",
        ha="left",
        va="top",
        fontsize=11,
        color=FG,
        bbox={"boxstyle": "round,pad=0.35", "facecolor": "#2b2d42", "edgecolor": "#555a7a", "alpha": 0.95},
    )

    _save(fig, path)


def chart_5_daily_cost(path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    days = ["May 5", "May 6", "May 7", "May 8", "May 9"]
    totals = np.array([421, 943, 997, 2673, 942], dtype=float)

    claude_opus = totals * 0.65
    gpt54 = totals * 0.17
    gpt55 = totals * 0.07
    codex = totals * 0.07
    claude_sonnet = totals * 0.03
    other = totals * 0.01

    x = np.arange(len(days))
    w = 0.62
    ax.bar(x, claude_opus, width=w, color=CORAL, label="Claude Opus")
    ax.bar(x, gpt54, width=w, bottom=claude_opus, color=ORANGE, label="GPT-5.4")
    ax.bar(x, gpt55, width=w, bottom=claude_opus + gpt54, color=BLUE, label="GPT-5.5")
    ax.bar(x, codex, width=w, bottom=claude_opus + gpt54 + gpt55, color="#8ec5ff", label="Codex (GPT-5.3)")
    ax.bar(x, claude_sonnet, width=w, bottom=claude_opus + gpt54 + gpt55 + codex, color=TEAL, label="Claude Sonnet")
    ax.bar(x, other, width=w, bottom=claude_opus + gpt54 + gpt55 + codex + claude_sonnet, color=GRAY, label="Other")

    ax.set_xticks(x)
    ax.set_xticklabels(days, fontsize=11)
    ax.set_ylabel("Estimated cost (USD)")
    _clean_axes(ax, grid_y=True)
    ax.legend(ncol=3, loc="upper left", frameon=False, fontsize=9.5)

    for xi, total in zip(x, totals):
        ax.text(xi, total + totals.max() * 0.02, f"${int(total)}", ha="center", va="bottom", fontsize=11.5, weight="bold")

    ax.set_ylim(0, totals.max() * 1.16)

    fig.text(0.5, 0.93, "Peak Week: Daily Agent Spend", ha="center", fontsize=25, weight="bold")
    fig.text(0.5, 0.875, "~$5,976 in 5 days (1,339 sessions)", ha="center", fontsize=12.5, color="#d8deff")

    _save(fig, path)


def chart_6_model_spread(path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    labels = [
        "Claude Opus 4-6",
        "Claude Haiku 4.5",
        "Claude Sonnet 4-6",
        "GPT-5.4",
        "Claude Opus 4-5",
        "Codex (GPT-5.3)",
        "GPT-5.4-mini",
        "GPT-5.5",
        "Other",
    ]
    spawns = np.array([1204, 781, 642, 636, 198, 140, 80, 47, 611])
    pcts = [27.7, 18.0, 14.8, 14.7, 4.6, 3.2, 1.8, 1.1, 14.1]

    colors = [CORAL, "#9ae6dd", TEAL, BLUE, ORANGE, "#8ec5ff", "#6db4ff", "#b3d9ff", GRAY]

    wedges, _ = ax.pie(
        spawns,
        colors=colors,
        startangle=92,
        counterclock=False,
        wedgeprops={"width": 0.34, "edgecolor": BG, "linewidth": 2},
    )

    # Label outside with leaders for readability
    for w, label, spawn, pct in zip(wedges, labels, spawns, pcts):
        ang = (w.theta2 + w.theta1) / 2
        ang_rad = math.radians(ang)
        x = math.cos(ang_rad)
        y = math.sin(ang_rad)
        ax.plot([0.86 * x, 1.07 * x], [0.86 * y, 1.07 * y], color="#cfd3eb", linewidth=0.8)
        ha = "left" if x >= 0 else "right"
        ax.text(1.12 * x, 1.12 * y, f"{label}\n{spawn} ({pct:.1f}%)", ha=ha, va="center", fontsize=9.8)

    ax.set_aspect("equal")
    fig.text(0.5, 0.93, "Model Mix Across Sessions", ha="center", fontsize=25, weight="bold")

    _save(fig, path)


def chart_model_as_role(role: str, data: list[tuple[str, float, int]], path: str) -> None:
    fig, ax = plt.subplots(figsize=FIGSIZE, dpi=DPI)

    sorted_data = sorted(data, key=lambda x: x[1], reverse=True)
    models = [m for m, _, _ in sorted_data]
    ratios = np.array([r for _, r, _ in sorted_data], dtype=float)
    samples = [n for _, _, n in sorted_data]

    cmap = mcolors.LinearSegmentedColormap.from_list("role_bar_waste", [TEAL, CORAL])
    rmin, rmax = ratios.min(), ratios.max()
    denom = (rmax - rmin) if (rmax - rmin) > 0 else 1.0
    bar_colors = [cmap((ratio - rmin) / denom) for ratio in ratios]

    y = np.arange(len(models))
    bars = ax.barh(y, ratios, color=bar_colors, height=0.62)
    ax.set_yticks(y)
    ax.set_yticklabels(models, fontsize=11)
    ax.set_xlabel("Redundant read rate (%)")
    ax.grid(axis="x", color="#40445f", alpha=0.35, linewidth=0.8)
    ax.set_axisbelow(True)
    _clean_axes(ax, grid_y=False)
    ax.invert_yaxis()

    xpad = ratios.max() * 0.02
    for bar, ratio, n in zip(bars, ratios, samples):
        y_mid = bar.get_y() + bar.get_height() / 2
        ax.text(ratio + xpad, y_mid, f"{ratio:.1f}%", va="center", ha="left", fontsize=11, color=FG, weight="bold")
        label_x = max(ratio * 0.06, 1.5)
        ax.text(label_x, y_mid, f"n={n}", va="center", ha="left", fontsize=9.5, color="#e7ecff")

    ax.set_xlim(0, max(ratios) * 1.22)

    role_title = role.replace("-", " ").title()
    fig.text(0.5, 0.93, f"Which Model is the Best {role_title}?", ha="center", fontsize=25, weight="bold")
    fig.text(0.5, 0.875, f"Redundant read rate when assigned as {role}", ha="center", fontsize=12.5, color="#d8deff")

    _save(fig, path)


def main() -> None:
    model_as_role_data: dict[str, list[tuple[str, float, int]]] = {
        "coder": [
            ("Codex (GPT-5.3)", 71.6, 120),
            ("Claude Sonnet 4-6", 70.4, 103),
            ("Claude Opus 4-6", 54.0, 100),
            ("Claude Haiku 4.5", 53.6, 112),
        ],
        "reviewer": [
            ("GPT-5.4", 74.2, 200),
            ("Claude Opus 4-6", 60.2, 156),
            ("Claude Haiku 4.5", 47.4, 100),
            ("Claude Sonnet 4-6", 41.5, 40),
        ],
        "architect": [
            ("Claude Haiku 4.5", 65.0, 325),
            ("Claude Opus 4-6", 56.1, 297),
            ("Claude Sonnet 4-5", 49.5, 33),
            ("Claude Sonnet 4-6", 36.3, 32),
            ("GPT-5.4", 32.8, 13),
        ],
        "explorer": [
            ("GPT-5.4-mini", 47.7, 65),
            ("Claude Haiku 4.5", 25.4, 19),
            ("Claude Opus 4-6", 16.6, 21),
        ],
    }

    outputs = {
        "headline": "/tmp/final-headline.png",
        "delegation_tax": "/tmp/final-delegation-tax.png",
        "agent_roles": "/tmp/final-agent-roles.png",
        "models": "/tmp/final-models.png",
        "daily_cost": "/tmp/final-daily-cost.png",
        "model_spread": "/tmp/final-model-spread.png",
        **{f"model_as_{role}": f"/tmp/final-model-as-{role}.png" for role in model_as_role_data},
    }

    chart_1_headline(outputs["headline"])
    chart_2_delegation_tax(outputs["delegation_tax"])
    chart_3_agent_roles(outputs["agent_roles"])
    chart_4_models(outputs["models"])
    chart_5_daily_cost(outputs["daily_cost"])
    chart_6_model_spread(outputs["model_spread"])
    for role, role_data in model_as_role_data.items():
        chart_model_as_role(role, role_data, outputs[f"model_as_{role}"])

    print("Generated charts:")
    for p in outputs.values():
        print(f" - {p}")


if __name__ == "__main__":
    main()
