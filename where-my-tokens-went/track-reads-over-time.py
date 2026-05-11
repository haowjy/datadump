#!/usr/bin/env python3
"""
Track redundant file-read metrics over time.

Runs the session-read-analyzer's collection logic, buckets by ISO week, and
writes a JSONL time-series file. Each record includes:

  - Overall: sessions, reads, redundancy rate, RAE rate
  - Role-controlled: per-role metrics so staffing changes don't look like
    behavior changes
  - Harness split: RAE rate by harness/model for the harness-design analysis
  - Compaction stats

Re-run periodically. Idempotent — overwrites the output with the full
historical series from current data on disk.

Usage:
    python track-reads-over-time.py --summary
    python track-reads-over-time.py --since 2026-04-01 --summary
    python track-reads-over-time.py -o custom-output.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date as dt_date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from session_read_analyzer import (
    SpawnStats,
    collect_all_stats,
    estimate_spawn_cost,
)


DEFAULT_OUTPUT = Path(__file__).parent.parent / "runs" / "time-series.jsonl"

# Roles used in the blog posts — track these specifically for role-controlled
# comparisons over time.
TRACKED_ROLES = [
    "coder", "reviewer", "architect", "explorer", "refactor-coder",
    "tech-writer", "smoke-tester", "design-lead", "product-lead",
    "investigator", "qa-lead",
]


@dataclass
class RoleMetrics:
    """Per-role metrics for role-controlled comparisons."""
    sessions: int = 0
    reads: int = 0
    unique_files: int = 0
    edits: int = 0
    read_after_edit: int = 0

    def add(self, stat: SpawnStats) -> None:
        total = sum(stat.read_counts.values())
        self.sessions += 1
        self.reads += total
        self.unique_files += len(stat.read_counts)
        self.edits += stat.edit_count
        self.read_after_edit += stat.read_after_edit

    def to_dict(self) -> dict:
        redundant = max(self.reads - self.unique_files, 0)
        return {
            "sessions": self.sessions,
            "reads": self.reads,
            "redundancy_rate": round(redundant / self.reads * 100, 1) if self.reads > 0 else 0.0,
            "edits": self.edits,
            "rae": self.read_after_edit,
            "rae_rate": round(self.read_after_edit / self.edits * 100, 1) if self.edits > 0 else 0.0,
        }


@dataclass
class WeekRecord:
    week: str

    # Aggregate
    sessions: int = 0
    reads: int = 0
    unique_files: int = 0
    edits: int = 0
    read_after_edit: int = 0
    compactions: int = 0
    sessions_with_compaction: int = 0
    estimated_cost_usd: float = 0.0

    # Breakdowns
    by_role: dict[str, RoleMetrics] = field(default_factory=lambda: defaultdict(RoleMetrics))
    by_harness: dict[str, RoleMetrics] = field(default_factory=lambda: defaultdict(RoleMetrics))
    by_model: dict[str, RoleMetrics] = field(default_factory=lambda: defaultdict(RoleMetrics))
    by_kind: dict[str, RoleMetrics] = field(default_factory=lambda: defaultdict(RoleMetrics))

    # RAE by harness/model — the key comparison from the blog post
    rae_by_harness_model: dict[str, dict] = field(
        default_factory=lambda: defaultdict(lambda: {"edits": 0, "rae": 0, "sessions": 0})
    )

    # Role × harness for controlled comparison
    role_by_harness: dict[str, dict[str, RoleMetrics]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(RoleMetrics))
    )

    def add(self, stat: SpawnStats, cost: float | None) -> None:
        total = sum(stat.read_counts.values())
        self.sessions += 1
        self.reads += total
        self.unique_files += len(stat.read_counts)
        self.edits += stat.edit_count
        self.read_after_edit += stat.read_after_edit
        self.compactions += stat.meta.compaction_count
        if stat.meta.compaction_count > 0:
            self.sessions_with_compaction += 1
        if cost is not None:
            self.estimated_cost_usd += cost

        m = stat.meta
        self.by_role[m.agent].add(stat)
        self.by_harness[m.harness].add(stat)
        self.by_model[m.model].add(stat)
        self.by_kind[m.kind].add(stat)

        hm = f"{m.harness}/{m.model}"
        self.rae_by_harness_model[hm]["edits"] += stat.edit_count
        self.rae_by_harness_model[hm]["rae"] += stat.read_after_edit
        self.rae_by_harness_model[hm]["sessions"] += 1

        self.role_by_harness[m.agent][m.harness].add(stat)

    def to_dict(self) -> dict:
        redundant = max(self.reads - self.unique_files, 0)
        redundancy_rate = round(redundant / self.reads * 100, 1) if self.reads > 0 else 0.0
        rae_rate = round(self.read_after_edit / self.edits * 100, 1) if self.edits > 0 else 0.0

        # Only include tracked roles with data
        role_data = {}
        for role in TRACKED_ROLES:
            if role in self.by_role and self.by_role[role].sessions > 0:
                role_data[role] = self.by_role[role].to_dict()

        # Role × harness: same role on different harnesses
        role_harness_data = {}
        for role in TRACKED_ROLES:
            if role in self.role_by_harness:
                harness_data = {}
                for harness, metrics in self.role_by_harness[role].items():
                    if metrics.sessions > 0:
                        harness_data[harness] = metrics.to_dict()
                if harness_data:
                    role_harness_data[role] = harness_data

        return {
            "week": self.week,
            "sessions": self.sessions,
            "reads": self.reads,
            "redundancy_rate": redundancy_rate,
            "edits": self.edits,
            "read_after_edit": self.read_after_edit,
            "rae_rate": rae_rate,
            "compactions": self.compactions,
            "sessions_with_compaction": self.sessions_with_compaction,
            "estimated_cost_usd": round(self.estimated_cost_usd, 2),
            "by_role": role_data,
            "by_harness": {k: v.to_dict() for k, v in sorted(self.by_harness.items()) if v.sessions > 0},
            "by_kind": {k: v.to_dict() for k, v in sorted(self.by_kind.items()) if v.sessions > 0},
            "rae_by_harness_model": {
                k: {
                    "edits": v["edits"],
                    "rae": v["rae"],
                    "rae_rate": round(v["rae"] / v["edits"] * 100, 1) if v["edits"] > 0 else 0.0,
                    "sessions": v["sessions"],
                }
                for k, v in sorted(self.rae_by_harness_model.items(), key=lambda kv: -kv[1]["edits"])
                if v["edits"] > 0
            },
            "role_by_harness": role_harness_data,
        }


def parse_tracking_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track redundant file-read metrics over time as a JSONL time series."
    )
    parser.add_argument("-o", "--output", type=str, default=str(DEFAULT_OUTPUT))
    parser.add_argument("--since", type=str, default=None, help="Only weeks on/after YYYY-MM-DD")
    parser.add_argument("--summary", action="store_true", help="Print human-readable summary")
    parser.add_argument("--projects-root", type=str, default=str(Path.home() / ".meridian" / "projects"))
    parser.add_argument("--claude-root", type=str, default=str(Path.home() / ".claude" / "projects"))
    parser.add_argument("--opencode-db", type=str, default=str(Path.home() / ".local" / "share" / "opencode" / "opencode.db"))
    parser.add_argument("--codex-root", type=str, default=str(Path.home() / ".codex" / "sessions"))
    parser.add_argument("--codex-state-db", type=str, default=str(Path.home() / ".codex" / "state_5.sqlite"))
    parser.add_argument("--source", type=str, default="all", choices=("all", "meridian", "claude", "opencode", "codex-native"))
    parser.add_argument("--estimate-costs", dest="estimate_costs", action="store_true", default=True)
    parser.add_argument("--no-estimate-costs", dest="estimate_costs", action="store_false")
    return parser.parse_args()


def main() -> int:
    args = parse_tracking_args()

    since_week: str | None = None
    if args.since:
        try:
            d = dt_date.fromisoformat(args.since)
            yr, wk, _ = d.isocalendar()
            since_week = f"{yr}-W{wk:02d}"
        except ValueError:
            print(f"Error: invalid --since date: {args.since}", file=sys.stderr)
            return 1

    print("Collecting sessions...", file=sys.stderr)
    all_stats = collect_all_stats(args)
    print(f"Collected {len(all_stats)} succeeded sessions.", file=sys.stderr)

    # Bucket by ISO week
    weeks: dict[str, WeekRecord] = {}
    for stat in all_stats:
        if not stat.meta.started_at:
            continue
        try:
            d = dt_date.fromisoformat(stat.meta.started_at[:10])
            yr, wk, _ = d.isocalendar()
            week_key = f"{yr}-W{wk:02d}"
        except (ValueError, TypeError):
            continue

        if since_week and week_key < since_week:
            continue

        if week_key not in weeks:
            weeks[week_key] = WeekRecord(week=week_key)

        actual_cost = stat.meta.cost
        estimated_cost = None
        if actual_cost is None and args.estimate_costs:
            estimated_cost = estimate_spawn_cost(stat.meta)
        effective_cost = actual_cost if actual_cost is not None else estimated_cost

        weeks[week_key].add(stat, effective_cost)

    # Write JSONL
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        for week_key in sorted(weeks.keys()):
            f.write(json.dumps(weeks[week_key].to_dict()) + "\n")

    print(f"Wrote {len(weeks)} weekly records to {output_path}", file=sys.stderr)

    if args.summary:
        print_summary(weeks)

    return 0


def print_summary(weeks: dict[str, WeekRecord]) -> None:
    print()
    print("=" * 78)
    print("REDUNDANT FILE READS — WEEKLY TIME SERIES")
    print("=" * 78)

    # Overall trend
    print()
    print(f"{'WEEK':<12} {'SESS':>6} {'READS':>7} {'REDUND%':>8} "
          f"{'EDITS':>6} {'RAE':>5} {'RAE%':>6} {'COST':>9}")
    print("-" * 78)

    for week_key in sorted(weeks.keys()):
        w = weeks[week_key]
        redundant = max(w.reads - w.unique_files, 0)
        rate = (redundant / w.reads * 100) if w.reads > 0 else 0.0
        rae_rate = (w.read_after_edit / w.edits * 100) if w.edits > 0 else 0.0
        print(f"{w.week:<12} {w.sessions:>6} {w.reads:>7} {rate:>7.1f}% "
              f"{w.edits:>6} {w.read_after_edit:>5} {rae_rate:>5.1f}% "
              f"${w.estimated_cost_usd:>8,.0f}")

    # RAE by harness (the harness-matters comparison)
    print()
    print("RAE RATE BY HARNESS (weekly)")
    print("-" * 78)
    harnesses: set[str] = set()
    for w in weeks.values():
        harnesses.update(w.by_harness.keys())
    harness_list = sorted(harnesses)
    header = f"{'WEEK':<12}"
    for h in harness_list:
        header += f" {h + ' RAE%':>14}"
    print(header)
    for week_key in sorted(weeks.keys()):
        w = weeks[week_key]
        row = f"{week_key:<12}"
        for h in harness_list:
            m = w.by_harness.get(h)
            if m and m.edits > 0:
                rate = m.read_after_edit / m.edits * 100
                row += f" {rate:>13.1f}%"
            else:
                row += f" {'—':>14}"
        print(row)

    # Role-controlled: same role across weeks
    print()
    print("ROLE-CONTROLLED REDUNDANCY (weekly, coder role only)")
    print("-" * 78)
    print(f"{'WEEK':<12} {'SESS':>6} {'REDUND%':>8} {'RAE%':>6}", end="")
    for h in harness_list:
        print(f"  {h + ' RAE%':>12}", end="")
    print()

    for week_key in sorted(weeks.keys()):
        w = weeks[week_key]
        coder = w.by_role.get("coder")
        if not coder or coder.sessions == 0:
            continue
        redundant = max(coder.reads - coder.unique_files, 0)
        rate = (redundant / coder.reads * 100) if coder.reads > 0 else 0.0
        rae = (coder.read_after_edit / coder.edits * 100) if coder.edits > 0 else 0.0
        row = f"{week_key:<12} {coder.sessions:>6} {rate:>7.1f}% {rae:>5.1f}%"

        for h in harness_list:
            hm = w.role_by_harness.get("coder", {}).get(h)
            if hm and hm.edits > 0:
                h_rae = hm.read_after_edit / hm.edits * 100
                row += f"  {h_rae:>11.1f}%"
            else:
                row += f"  {'—':>12}"
        print(row)

    print()


if __name__ == "__main__":
    raise SystemExit(main())
