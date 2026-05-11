#!/usr/bin/env python3
"""Compare Claude Code read-after-edit rates before/after the v2.1.91 date.

Default cut:
  before = sessions started before 2026-04-03
  after  = sessions started on or after 2026-04-03

This is meant to answer a narrow question: did Claude Code's injected
"don't re-read after edit" instruction correlate with a visible RAE drop by
role? It does not prove causality; role/task/model mix can still confound it.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ANALYZER_PATH = SCRIPT_DIR / "session-read-analyzer.py"

spec = importlib.util.spec_from_file_location("session_read_analyzer", ANALYZER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Could not import analyzer from {ANALYZER_PATH}")
analyzer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = analyzer
spec.loader.exec_module(analyzer)


@dataclass
class Metrics:
    sessions: int = 0
    reads: int = 0
    unique_files: int = 0
    edits: int = 0
    rae: int = 0

    def add(self, stat) -> None:
        self.sessions += 1
        self.reads += sum(stat.read_counts.values())
        self.unique_files += len(stat.read_counts)
        self.edits += stat.edit_count
        self.rae += stat.read_after_edit

    @property
    def rae_rate(self) -> float:
        return self.rae / self.edits * 100 if self.edits else 0.0

    @property
    def redundancy_rate(self) -> float:
        return max(self.reads - self.unique_files, 0) / self.reads * 100 if self.reads else 0.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--cut-date", default="2026-04-03", help="YYYY-MM-DD; after bucket is on/after this date")
    p.add_argument("--harness", default="claude", help="Harness to analyze; use 'all' for no harness filter")
    p.add_argument("--source", default="all", choices=analyzer.KNOWN_SOURCES)
    p.add_argument("--min-edits", type=int, default=25, help="Minimum total edits per role to include in CSV")
    p.add_argument("--min-side-edits", type=int, default=25, help="Minimum edits on both sides of the cut for chart inclusion")
    p.add_argument("--top", type=int, default=12, help="Max roles to include in chart, ranked by total edits")
    p.add_argument("--csv", default=str(SCRIPT_DIR / "data" / "april3-before-after-by-role.csv"))
    p.add_argument("--chart", default=str(SCRIPT_DIR / "images" / "april3-before-after-by-role.svg"))
    p.add_argument("--projects-root", default=str(Path.home() / ".meridian" / "projects"))
    p.add_argument("--claude-root", default=str(Path.home() / ".claude" / "projects"))
    p.add_argument("--opencode-db", default=str(Path.home() / ".local" / "share" / "opencode" / "opencode.db"))
    p.add_argument("--codex-root", default=str(Path.home() / ".codex" / "sessions"))
    p.add_argument("--codex-state-db", default=str(Path.home() / ".codex" / "state_5.sqlite"))
    return p.parse_args()


def bucket_for_started_at(started_at: str | None, cut: date) -> str | None:
    if not started_at:
        return None
    try:
        d = date.fromisoformat(started_at[:10])
    except ValueError:
        return None
    return "after" if d >= cut else "before"


def esc(text: object) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_svg_chart(path: Path, rows: list[dict], args: argparse.Namespace) -> None:
    """Dependency-free grouped bar chart."""
    width = max(900, 120 + len(rows) * 95)
    height = 560
    left = 70
    right = 30
    top = 70
    bottom = 145
    plot_w = width - left - right
    plot_h = height - top - bottom
    ymax = max([r["before_rae_rate"] for r in rows] + [r["after_rae_rate"] for r in rows] + [5])
    ymax = ((int(ymax / 10) + 1) * 10) or 10
    group_w = plot_w / len(rows)
    bar_w = min(28, group_w * 0.28)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{left}" y="32" font-family="Arial, sans-serif" font-size="22" font-weight="700">Read-after-edit rate by role ({esc(args.harness)} harness)</text>',
        f'<text x="{left}" y="55" font-family="Arial, sans-serif" font-size="12" fill="#555">Before &lt; {esc(args.cut_date)} vs. on/after {esc(args.cut_date)}. Chart includes roles with at least {args.min_side_edits} edits on both sides.</text>',
    ]

    # axes/grid
    for tick in range(0, int(ymax) + 1, 10):
        y = top + plot_h - (tick / ymax) * plot_h
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{width-right}" y2="{y:.1f}" stroke="#e6e6e6"/>')
        parts.append(f'<text x="{left-10}" y="{y+4:.1f}" text-anchor="end" font-family="Arial, sans-serif" font-size="11" fill="#666">{tick}%</text>')
    parts.append(f'<line x1="{left}" y1="{top}" x2="{left}" y2="{top+plot_h}" stroke="#333"/>')
    parts.append(f'<line x1="{left}" y1="{top+plot_h}" x2="{width-right}" y2="{top+plot_h}" stroke="#333"/>')
    parts.append(f'<text x="18" y="{top + plot_h/2}" transform="rotate(-90 18 {top + plot_h/2})" text-anchor="middle" font-family="Arial, sans-serif" font-size="12" fill="#333">RAE rate (% of edits followed by reread)</text>')

    before_color = "#4c78a8"
    after_color = "#f58518"
    for i, r in enumerate(rows):
        cx = left + group_w * i + group_w / 2
        vals = [("before", r["before_rae_rate"], -bar_w * 0.6, before_color), ("after", r["after_rae_rate"], bar_w * 0.6, after_color)]
        for label, val, dx, color in vals:
            h = (val / ymax) * plot_h
            x = cx + dx - bar_w / 2
            y = top + plot_h - h
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{color}"/>')
            parts.append(f'<text x="{x+bar_w/2:.1f}" y="{y-4:.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" fill="#333">{val:.1f}%</text>')
        label_y = top + plot_h + 18
        parts.append(f'<text x="{cx:.1f}" y="{label_y}" transform="rotate(35 {cx:.1f} {label_y})" font-family="Arial, sans-serif" font-size="11" fill="#333">{esc(r["role"])}</text>')
        parts.append(f'<text x="{cx:.1f}" y="{height-40}" text-anchor="middle" font-family="Arial, sans-serif" font-size="10" fill="#777">{r["before_edits"]}/{r["after_edits"]} edits</text>')

    # legend
    lx = width - right - 260
    parts.extend([
        f'<rect x="{lx}" y="24" width="14" height="14" fill="{before_color}"/>',
        f'<text x="{lx+20}" y="36" font-family="Arial, sans-serif" font-size="12">Before {esc(args.cut_date)}</text>',
        f'<rect x="{lx+135}" y="24" width="14" height="14" fill="{after_color}"/>',
        f'<text x="{lx+155}" y="36" font-family="Arial, sans-serif" font-size="12">On/after</text>',
        f'<text x="{left}" y="{height-16}" font-family="Arial, sans-serif" font-size="11" fill="#666">CSV includes roles with at least {args.min_edits} total edits. Chart labels show before/after edit counts.</text>',
        '</svg>',
    ])
    path.write_text("\n".join(parts))


def main() -> int:
    args = parse_args()
    cut = date.fromisoformat(args.cut_date)

    collect_args = argparse.Namespace(
        projects_root=args.projects_root,
        claude_root=args.claude_root,
        opencode_db=args.opencode_db,
        codex_root=args.codex_root,
        codex_state_db=args.codex_state_db,
        source=args.source,
    )

    print("Collecting sessions...", file=sys.stderr)
    stats = analyzer.collect_all_stats(collect_args)
    print(f"Collected {len(stats)} succeeded sessions.", file=sys.stderr)

    by_role: dict[str, dict[str, Metrics]] = {}
    totals = {"before": Metrics(), "after": Metrics()}

    for stat in stats:
        if args.harness != "all" and stat.meta.harness != args.harness:
            continue
        bucket = bucket_for_started_at(stat.meta.started_at, cut)
        if bucket is None:
            continue
        role = stat.meta.agent or "?"
        by_role.setdefault(role, {"before": Metrics(), "after": Metrics()})[bucket].add(stat)
        totals[bucket].add(stat)

    rows = []
    for role, pair in by_role.items():
        before = pair["before"]
        after = pair["after"]
        total_edits = before.edits + after.edits
        if total_edits < args.min_edits:
            continue
        rows.append({
            "role": role,
            "before_sessions": before.sessions,
            "before_reads": before.reads,
            "before_redundancy_rate": round(before.redundancy_rate, 1),
            "before_edits": before.edits,
            "before_rae": before.rae,
            "before_rae_rate": round(before.rae_rate, 1),
            "after_sessions": after.sessions,
            "after_reads": after.reads,
            "after_redundancy_rate": round(after.redundancy_rate, 1),
            "after_edits": after.edits,
            "after_rae": after.rae,
            "after_rae_rate": round(after.rae_rate, 1),
            "delta_rae_rate": round(after.rae_rate - before.rae_rate, 1),
            "total_edits": total_edits,
        })

    rows.sort(key=lambda r: r["total_edits"], reverse=True)

    csv_path = Path(args.csv)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "role",
        "before_sessions", "before_reads", "before_redundancy_rate", "before_edits", "before_rae", "before_rae_rate",
        "after_sessions", "after_reads", "after_redundancy_rate", "after_edits", "after_rae", "after_rae_rate",
        "delta_rae_rate", "total_edits",
    ]
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    chart_rows = [
        r for r in rows
        if r["before_edits"] >= args.min_side_edits and r["after_edits"] >= args.min_side_edits
    ][: args.top]
    chart_path = Path(args.chart)
    chart_path.parent.mkdir(parents=True, exist_ok=True)
    if chart_rows:
        write_svg_chart(chart_path, chart_rows, args)

    print()
    print(f"Cut date: {args.cut_date} (before < cut, after >= cut)")
    print(f"Harness: {args.harness}")
    for bucket in ("before", "after"):
        m = totals[bucket]
        print(f"{bucket:>6}: sessions={m.sessions:,} edits={m.edits:,} RAE={m.rae:,} RAE%={m.rae_rate:.1f} reads={m.reads:,} redund%={m.redundancy_rate:.1f}")
    print()
    print(f"Wrote CSV: {csv_path}")
    if chart_path:
        print(f"Wrote chart: {chart_path}")
    print()
    print(f"Top comparable roles by edit volume (min_side_edits={args.min_side_edits}):")
    print(f"{'role':<20} {'before edits':>12} {'before RAE%':>12} {'after edits':>11} {'after RAE%':>11} {'delta':>8}")
    for r in chart_rows:
        print(f"{r['role']:<20} {r['before_edits']:>12,} {r['before_rae_rate']:>11.1f}% {r['after_edits']:>11,} {r['after_rae_rate']:>10.1f}% {r['delta_rae_rate']:>+7.1f}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
