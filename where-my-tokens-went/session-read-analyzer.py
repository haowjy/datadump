#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date as dt_date
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

READ_COMMANDS = {"cat", "sed", "head", "tail"}
GREP_COMMANDS = {"grep", "rg", "find"}
SHELL_WRAPPERS = {"bash", "sh", "zsh", "fish", "dash"}
KNOWN_SOURCES = ("all", "meridian", "claude", "opencode", "codex-native")
KNOWN_AGENT_NAMES = (
    "coder",
    "reviewer",
    "tech-lead",
    "product-lead",
    "design-lead",
    "explorer",
    "investigator",
    "smoke-tester",
    "planner",
    "refactor-coder",
    "refactor-reviewer",
    "alignment-reviewer",
    "architect",
    "qa-lead",
    "kb-writer",
    "tech-writer",
    "session-explorer",
    "prompt-dev",
    "integration-tester",
    "unit-tester",
    "verifier",
    "frontend-coder",
    "browser-tester",
)
KNOWN_AGENT_NAMES_SORTED = tuple(sorted(KNOWN_AGENT_NAMES, key=len, reverse=True))

COST_RATES_PER_MILLION: dict[str, dict[str, dict[str, float]]] = {
    "codex": {
        "gpt-5.4": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
        "gpt-5.3-codex": {"input": 2.00, "cached_input": 0.50, "output": 8.00},
        "gpt-5.4-mini": {"input": 0.30, "cached_input": 0.075, "output": 1.20},
        "gpt-5.5": {"input": 3.00, "cached_input": 0.75, "output": 12.00},
        "gpt-5.3-codex-spark": {"input": 0.50, "cached_input": 0.125, "output": 2.00},
    },
    "claude": {
        "claude-sonnet-4-6": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
        "claude-opus-4-6": {"input": 15.00, "cached_input": 1.50, "output": 75.00},
        "claude-haiku-4-5": {"input": 0.80, "cached_input": 0.08, "output": 4.00},
    },
}


@dataclass
class SpawnMeta:
    key: tuple[str, str]  # (project_id, spawn_id)
    source: str
    project_id: str
    project_label: str
    spawn_id: str
    model: str
    harness: str
    duration_secs: float
    status: str
    kind: str
    agent: str
    parent_id: str | None
    dedupe_spawn_id: str | None
    execution_cwd: str | None
    state_path: Path
    history_path: Path
    started_at: str | None = None  # ISO timestamp
    compaction_count: int = 0
    cost: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    harness_session_id: str | None = None


@dataclass
class SpawnStats:
    meta: SpawnMeta
    read_counts: Counter[str] = field(default_factory=Counter)
    edit_count: int = 0
    grep_count: int = 0
    read_after_edit: int = 0
    malformed_lines: int = 0
    _last_action_by_file: dict[str, str] = field(default_factory=dict)

    def add_read(self, path: str) -> None:
        if not path:
            return
        if self._last_action_by_file.get(path) == "edit":
            self.read_after_edit += 1
        self._last_action_by_file[path] = "read"
        self.read_counts[path] += 1

    def add_edit(self, path: str) -> None:
        if not path:
            return
        self._last_action_by_file[path] = "edit"
        self.edit_count += 1


@dataclass
class GroupStats:
    spawns: int = 0
    reads: int = 0
    edit_count: int = 0
    grep_count: int = 0
    read_after_edit: int = 0
    files: Counter[str] = field(default_factory=Counter)

    def add_spawn(self, stat: SpawnStats) -> None:
        self.spawns += 1
        self.reads += sum(stat.read_counts.values())
        self.edit_count += stat.edit_count
        self.grep_count += stat.grep_count
        self.read_after_edit += stat.read_after_edit
        self.files.update(stat.read_counts)


@dataclass
class CostRollup:
    spawns: int = 0
    total_cost: float = 0.0
    actual_cost: float = 0.0
    estimated_cost: float = 0.0
    estimated_spawns: int = 0
    unknown_spawns: int = 0

    def add(self, effective_cost: float | None, actual_cost: float | None, estimated_cost: float | None) -> None:
        self.spawns += 1
        if effective_cost is not None:
            self.total_cost += effective_cost
        if actual_cost is not None:
            self.actual_cost += actual_cost
        if estimated_cost is not None:
            self.estimated_cost += estimated_cost
            self.estimated_spawns += 1
        if actual_cost is None and estimated_cost is None:
            self.unknown_spawns += 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze Meridian spawn history file-read patterns.")
    parser.add_argument("--limit", type=int, default=None, help="Maximum number of succeeded spawns to analyze")
    parser.add_argument(
        "--project-root",
        type=str,
        default=None,
        help="Optional project root prefix to strip from paths (auto-detected if omitted)",
    )
    parser.add_argument(
        "--projects-root",
        type=str,
        default=str(Path.home() / ".meridian" / "projects"),
        help="Root directory containing Meridian projects",
    )
    parser.add_argument(
        "--claude-root",
        type=str,
        default=str(Path.home() / ".claude" / "projects"),
        help="Root directory containing Claude Code session JSONL files",
    )
    parser.add_argument(
        "--opencode-db",
        type=str,
        default=str(Path.home() / ".local" / "share" / "opencode" / "opencode.db"),
        help="Path to OpenCode SQLite database",
    )
    parser.add_argument(
        "--codex-root",
        type=str,
        default=str(Path.home() / ".codex" / "sessions"),
        help="Root directory containing Codex native session JSONL files",
    )
    parser.add_argument(
        "--codex-state-db",
        type=str,
        default=str(Path.home() / ".codex" / "state_5.sqlite"),
        help="Path to Codex state SQLite DB (threads table used for model lookup)",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="all",
        choices=KNOWN_SOURCES,
        help="Data source filter: all|meridian|claude|opencode|codex-native (default: all)",
    )
    parser.set_defaults(estimate_costs=True)
    parser.add_argument(
        "--estimate-costs",
        dest="estimate_costs",
        action="store_true",
        help="Estimate missing costs from token usage (default: enabled)",
    )
    parser.add_argument(
        "--no-estimate-costs",
        dest="estimate_costs",
        action="store_false",
        help="Disable missing-cost estimation; only use reported total_cost_usd",
    )
    return parser.parse_args()


def safe_json_loads(line: str) -> dict | None:
    try:
        return json.loads(line)
    except Exception:
        return None


def parse_ms_timestamp(ms: object) -> str | None:
    try:
        value = int(ms)
    except Exception:
        return None
    if value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def parse_iso_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def parse_compaction_signal(obj: dict) -> bool:
    event_type = str(obj.get("type") or obj.get("event_type") or "").lower()
    subtype = str(obj.get("subtype") or "").lower()
    if event_type == "compaction":
        return True
    if subtype == "compact_boundary":
        return True
    if event_type == "system":
        content = obj.get("content")
        if isinstance(content, str) and "compact" in content.lower():
            return True
        compact_meta = obj.get("compactMetadata")
        if isinstance(compact_meta, dict):
            return True
    return False


def flatten_strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from flatten_strings(item)
    elif isinstance(value, dict):
        for sub in value.values():
            yield from flatten_strings(sub)


def detect_agent_name(text: str) -> str | None:
    lowered = text.lower()
    for agent in KNOWN_AGENT_NAMES_SORTED:
        if re.search(rf"(?<![a-z0-9]){re.escape(agent)}(?![a-z0-9])", lowered):
            return agent
    if "# coder" in lowered:
        return "coder"
    if "# reviewer" in lowered:
        return "reviewer"
    if "# tech lead" in lowered:
        return "tech-lead"
    if "you are a reviewer" in lowered:
        return "reviewer"
    if "you are a coder" in lowered:
        return "coder"
    return None


def extract_agent_from_codex_instructions(text: str) -> str | None:
    if not text:
        return None

    lines = text.splitlines()
    first_nonempty = next((ln.strip() for ln in lines if ln.strip()), "")
    if first_nonempty.startswith("#"):
        hdr = first_nonempty.lstrip("#").strip()
        if hdr:
            detected = detect_agent_name(hdr)
            if detected:
                return detected

    for raw in lines:
        line = raw.strip()
        if not line.startswith("#"):
            continue
        hdr = line.lstrip("#").strip()
        if not hdr:
            continue
        detected = detect_agent_name(hdr)
        if detected:
            return detected

    return detect_agent_name(text)


def extract_agent_from_codex_profile_header(header: str) -> str | None:
    clean = header.strip()
    if not clean:
        return None

    detected = detect_agent_name(clean)
    if detected:
        return detected

    normalized = re.sub(r"[^a-z0-9]+", "-", clean.lower()).strip("-")
    if not normalized:
        return None
    if normalized in {"skill", "skills", "reference-files", "reference", "report"}:
        return None
    if normalized.startswith("skill-"):
        return None
    return normalized


def extract_agent_mention_from_text(text: str) -> str | None:
    for mention in re.findall(r"@([A-Za-z][A-Za-z0-9-]*)", text):
        normalized = mention.lower()
        if normalized in KNOWN_AGENT_NAMES:
            return normalized
    return None


def extract_agent_from_codex_user_message(text: str) -> str | None:
    if not text:
        return None

    marker = re.search(r"(?mi)^#\s*Agent Profile\s*$", text)
    if not marker:
        return extract_agent_mention_from_text(text)

    tail = text[marker.end() :]
    header_match = re.search(r"(?mi)^#\s+([A-Za-z][A-Za-z0-9 -]+)\s*$", tail)
    if header_match:
        return extract_agent_from_codex_profile_header(header_match.group(1))

    return extract_agent_mention_from_text(text)


def decode_claude_project_label(dir_name: str) -> str:
    if not dir_name:
        return "?"
    # Claude stores project path as hyphen-escaped directory names:
    # /home/user/repo -> -home-user-repo and literal '-' -> '--'.
    sentinel = "\0"
    decoded = dir_name.replace("--", sentinel).replace("-", "/").replace(sentinel, "-")
    return decoded if decoded else dir_name


def detect_spawn_id_in_text(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"(?:^|[^A-Za-z0-9])(p\d{1,8})(?:$|[^A-Za-z0-9])", text)
    if match:
        return match.group(1)
    return None


def split_shell_segments(command: str) -> list[str]:
    segments: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    escape = False
    i = 0
    while i < len(command):
        ch = command[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in {"'", '"'}:
            quote = ch
            buf.append(ch)
            i += 1
            continue

        # Split on command separators and pipeline/logical operators.
        if ch in {";", "\n", "|", "&"}:
            segment = "".join(buf).strip()
            if segment:
                segments.append(segment)
            buf = []
            if i + 1 < len(command) and command[i + 1] == ch and ch in {"|", "&"}:
                i += 2
            else:
                i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        segments.append(tail)
    return segments


def safe_shlex_split(segment: str) -> list[str]:
    try:
        return shlex.split(segment, posix=True)
    except Exception:
        return []


def strip_redirection_token(token: str) -> str:
    token = token.strip().rstrip(",:;")
    if not token:
        return ""
    # Handle inline redirects like 2>/dev/null
    if re.fullmatch(r"\d*>.*", token) or token.startswith(">"):
        return ""
    return token


def is_env_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=.*$", token))


def is_probable_path_token(token: str) -> bool:
    if not token:
        return False
    if token in {"-", "--"}:
        return False
    if token.startswith("-"):
        return False
    if token.startswith(("<<", "<", ">")):
        return False
    if token.upper() in {"EOF", "PY", "END"}:
        return False
    if re.fullmatch(r"\\d+", token):
        return False
    if re.fullmatch(r"\\d*[<>].*", token):
        return False
    return True


def extract_cat_paths(args: list[str]) -> list[str]:
    paths: list[str] = []
    for tok in args:
        tok = strip_redirection_token(tok)
        if not is_probable_path_token(tok):
            continue
        paths.append(tok)
    return paths


def extract_head_tail_paths(args: list[str]) -> list[str]:
    paths: list[str] = []
    i = 0
    while i < len(args):
        tok = strip_redirection_token(args[i])
        if not tok:
            i += 1
            continue
        if tok in {"-n", "-c"}:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if is_probable_path_token(tok):
            paths.append(tok)
        i += 1
    return paths


def extract_sed_paths(args: list[str]) -> list[str]:
    paths: list[str] = []
    saw_script = False
    i = 0
    while i < len(args):
        tok = strip_redirection_token(args[i])
        if not tok:
            i += 1
            continue
        if tok in {"-e", "-f"}:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if not saw_script:
            saw_script = True
            i += 1
            continue
        if is_probable_path_token(tok):
            paths.append(tok)
        i += 1
    return paths


def extract_from_command_segment(segment: str) -> tuple[list[str], int]:
    reads: list[str] = []
    greps = 0
    tokens = safe_shlex_split(segment)
    if not tokens:
        return reads, greps

    i = 0
    while i < len(tokens) and is_env_assignment(tokens[i]):
        i += 1
    if i >= len(tokens):
        return reads, greps

    cmd = os.path.basename(tokens[i])
    args = tokens[i + 1 :]

    # Unwrap shell wrappers like /bin/bash -lc "...".
    if cmd in SHELL_WRAPPERS:
        for j, tok in enumerate(args):
            if tok in {"-c", "-lc", "-cl"} and j + 1 < len(args):
                return extract_from_shell(args[j + 1])

    if cmd in GREP_COMMANDS:
        greps += 1
    if cmd == "cat":
        reads.extend(extract_cat_paths(args))
    elif cmd == "sed":
        reads.extend(extract_sed_paths(args))
    elif cmd in {"head", "tail"}:
        reads.extend(extract_head_tail_paths(args))

    return reads, greps


def extract_from_shell(command: str) -> tuple[list[str], int]:
    all_reads: list[str] = []
    greps = 0
    for segment in split_shell_segments(command):
        reads, seg_greps = extract_from_command_segment(segment)
        all_reads.extend(reads)
        greps += seg_greps
    return all_reads, greps


def extract_reference_paths(text: str) -> list[str]:
    paths: list[str] = []
    for line in text.splitlines():
        if line.startswith("# Reference:"):
            path = line.split(":", 1)[1].strip()
            if path:
                paths.append(path)
    return paths


def canonicalize_path(path: str, global_root: Path | None, spawn_root: Path | None) -> str:
    path = (path or "").strip().strip("\"").strip("'")
    path = path.rstrip(",:;")
    if not path:
        return ""

    path_obj = Path(path).expanduser()
    if not path_obj.is_absolute():
        return os.path.normpath(path)

    for root in (global_root, spawn_root):
        if root is None:
            continue
        try:
            rel = path_obj.relative_to(root)
            return os.path.normpath(str(rel))
        except Exception:
            continue

    return os.path.normpath(str(path_obj))


def parse_claude_history(
    history_path: Path,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    with history_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            obj = safe_json_loads(line)
            if obj is None:
                stat.malformed_lines += 1
                continue
            if parse_compaction_signal(obj):
                stat.meta.compaction_count += 1
            if obj.get("event_type") != "assistant":
                continue

            content = obj.get("payload", {}).get("message", {}).get("content", [])
            if not isinstance(content, list):
                continue
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                name = item.get("name")
                tool_input = item.get("input", {})
                if not isinstance(tool_input, dict):
                    continue
                if name == "Read":
                    p = canonicalize_path(str(tool_input.get("file_path", "")), global_root, spawn_root)
                    stat.add_read(p)
                elif name == "Edit":
                    p = canonicalize_path(str(tool_input.get("file_path", "")), global_root, spawn_root)
                    stat.add_edit(p)
                elif name == "Bash":
                    cmd = str(tool_input.get("command", ""))
                    read_paths, greps = extract_from_shell(cmd)
                    stat.grep_count += greps
                    for rp in read_paths:
                        p = canonicalize_path(rp, global_root, spawn_root)
                        stat.add_read(p)


def parse_codex_history(
    history_path: Path,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    with history_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            obj = safe_json_loads(line)
            if obj is None:
                stat.malformed_lines += 1
                continue
            if parse_compaction_signal(obj):
                stat.meta.compaction_count += 1

            event_type = obj.get("event_type")
            if event_type == "item/started":
                item = obj.get("payload", {}).get("item", {})
                parse_codex_item(item, stat, global_root, spawn_root)


def parse_codex_item(
    item: object,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    if not isinstance(item, dict):
        return

    command = item.get("command")
    if isinstance(command, str) and command:
        read_paths, greps = extract_from_shell(command)
        stat.grep_count += greps
        for rp in read_paths:
            p = canonicalize_path(rp, global_root, spawn_root)
            stat.add_read(p)

    item_type = item.get("type")
    if item_type == "fileChange":
        for change in item.get("changes", []) or []:
            if not isinstance(change, dict):
                continue
            cp = change.get("path")
            if isinstance(cp, str) and cp:
                p = canonicalize_path(cp, global_root, spawn_root)
                stat.add_edit(p)

    # Reference injection appears in userMessage content.
    content = item.get("content")
    if isinstance(content, list):
        for c in content:
            if not isinstance(c, dict):
                continue
            txt = c.get("text")
            if not isinstance(txt, str) or "# Reference:" not in txt:
                continue
            for rp in extract_reference_paths(txt):
                p = canonicalize_path(rp, global_root, spawn_root)
                stat.add_read(p)


def parse_codex_patch_paths(patch_text: str) -> list[str]:
    paths: list[str] = []
    for line in patch_text.splitlines():
        for prefix in ("*** Update File:", "*** Add File:", "*** Delete File:", "*** Move to:"):
            if line.startswith(prefix):
                raw = line[len(prefix) :].strip()
                if raw:
                    paths.append(raw)
    return paths


def parse_codex_function_call(
    tool_name: str,
    arguments: object,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    if not isinstance(arguments, dict):
        return

    lowered_name = tool_name.lower()
    cmd = arguments.get("command")
    if not isinstance(cmd, str):
        cmd = arguments.get("cmd")
    if isinstance(cmd, str) and cmd:
        read_paths, greps = extract_from_shell(cmd)
        stat.grep_count += greps
        for rp in read_paths:
            p = canonicalize_path(rp, global_root, spawn_root)
            stat.add_read(p)
    elif lowered_name in GREP_COMMANDS:
        stat.grep_count += 1

    read_path = (
        arguments.get("file_path")
        or arguments.get("filePath")
        or arguments.get("path")
    )
    if lowered_name in {"read", "open", "view_image", "read_mcp_resource"} and isinstance(read_path, str):
        p = canonicalize_path(read_path, global_root, spawn_root)
        stat.add_read(p)

    edit_path = (
        arguments.get("file_path")
        or arguments.get("filePath")
        or arguments.get("path")
    )
    if lowered_name in {"edit", "write"} and isinstance(edit_path, str):
        p = canonicalize_path(edit_path, global_root, spawn_root)
        stat.add_edit(p)

    if lowered_name == "apply_patch":
        patch_text = arguments.get("patch")
        if isinstance(patch_text, str):
            for rp in parse_codex_patch_paths(patch_text):
                p = canonicalize_path(rp, global_root, spawn_root)
                stat.add_edit(p)


def parse_codex_native_event(
    obj: dict,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    evt_type = str(obj.get("type") or "")
    payload = obj.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}

    if evt_type == "response_item":
        payload_type = str(payload.get("type") or "")
        if payload_type == "function_call":
            args: object = payload.get("arguments")
            if isinstance(args, str):
                parsed = safe_json_loads(args)
                args = parsed if isinstance(parsed, dict) else {}
            parse_codex_function_call(str(payload.get("name") or ""), args, stat, global_root, spawn_root)
        elif payload_type == "message" and str(payload.get("role") or "") == "user":
            content = payload.get("content")
            if isinstance(content, list):
                for c in content:
                    if not isinstance(c, dict):
                        continue
                    txt = c.get("text")
                    if not isinstance(txt, str) or "# Reference:" not in txt:
                        continue
                    for rp in extract_reference_paths(txt):
                        p = canonicalize_path(rp, global_root, spawn_root)
                        stat.add_read(p)
    elif evt_type == "event_msg" and str(payload.get("type") or "") == "patch_apply_end":
        changes = payload.get("changes")
        if isinstance(changes, dict):
            for cp in changes.keys():
                p = canonicalize_path(str(cp), global_root, spawn_root)
                stat.add_edit(p)


def parse_tool_use_items(
    content: object,
    stat: SpawnStats,
    global_root: Path | None,
    spawn_root: Path | None,
) -> None:
    if not isinstance(content, list):
        return
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "tool_use":
            continue
        name = str(item.get("name") or "")
        tool_input = item.get("input", {})
        if not isinstance(tool_input, dict):
            continue
        if name == "Read":
            p = canonicalize_path(str(tool_input.get("file_path", "")), global_root, spawn_root)
            stat.add_read(p)
        elif name == "Edit":
            p = canonicalize_path(str(tool_input.get("file_path", "")), global_root, spawn_root)
            stat.add_edit(p)
        elif name == "Bash":
            cmd = str(tool_input.get("command", ""))
            read_paths, greps = extract_from_shell(cmd)
            stat.grep_count += greps
            for rp in read_paths:
                p = canonicalize_path(rp, global_root, spawn_root)
                stat.add_read(p)


def find_claude_session_files(claude_root: Path) -> list[Path]:
    if not claude_root.exists():
        return []
    return sorted(p for p in claude_root.rglob("*.jsonl") if p.is_file())


def load_claude_sessions(claude_root: Path, global_root: Path | None) -> list[SpawnStats]:
    stats: list[SpawnStats] = []
    for session_path in find_claude_session_files(claude_root):
        parsed = parse_claude_session(session_path, claude_root, global_root)
        if parsed is not None:
            stats.append(parsed)
    return stats


def parse_claude_session(
    session_path: Path,
    claude_root: Path,
    global_root: Path | None,
) -> SpawnStats | None:
    try:
        rel_parts = session_path.relative_to(claude_root).parts
    except Exception:
        rel_parts = session_path.parts
    session_id = session_path.stem
    project_dir = rel_parts[0] if len(rel_parts) >= 1 else session_path.parent.name
    spawn_id_hint = detect_spawn_id_in_text(project_dir)
    project_label = decode_claude_project_label(project_dir)

    meta = SpawnMeta(
        key=(f"claude:{project_dir}", session_id),
        source="claude-code",
        project_id=f"claude:{project_dir}",
        project_label=project_label,
        spawn_id=session_id,
        model="?",
        harness="claude",
        duration_secs=0.0,
        status="succeeded",
        kind="session",
        agent="?",
        parent_id=spawn_id_hint,
        dedupe_spawn_id=spawn_id_hint,
        execution_cwd=None,
        state_path=session_path,
        history_path=session_path,
    )
    stat = SpawnStats(meta=meta)

    first_ts: datetime | None = None
    last_ts: datetime | None = None
    usage_seen: set[str] = set()
    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_create_tokens = 0
    compactions = 0

    with session_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            obj = safe_json_loads(line)
            if obj is None:
                stat.malformed_lines += 1
                continue

            if parse_compaction_signal(obj):
                compactions += 1

            ts = parse_iso_timestamp(obj.get("timestamp"))
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts
            if meta.execution_cwd is None:
                cwd = obj.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    meta.execution_cwd = cwd.strip()

            event_type = str(obj.get("type") or "")
            if event_type == "agent-setting" and meta.agent == "?":
                setting = obj.get("agentSetting")
                if isinstance(setting, str) and setting.strip():
                    meta.agent = setting.strip()

            if event_type == "assistant":
                message = obj.get("message", {})
                if isinstance(message, dict):
                    model = message.get("model")
                    if isinstance(model, str) and model and meta.model == "?":
                        meta.model = model
                    content = message.get("content", [])
                    spawn_root = Path(meta.execution_cwd).expanduser() if meta.execution_cwd else None
                    parse_tool_use_items(content, stat, global_root, spawn_root)
                    if meta.agent == "?":
                        for text in flatten_strings(content):
                            detected = detect_agent_name(text)
                            if detected:
                                meta.agent = detected
                                break

                    usage = message.get("usage")
                    usage_key = str(obj.get("requestId") or message.get("id") or obj.get("uuid") or "")
                    if isinstance(usage, dict) and usage_key not in usage_seen:
                        usage_seen.add(usage_key)
                        input_tokens += int(usage.get("input_tokens") or 0)
                        output_tokens += int(usage.get("output_tokens") or 0)
                        cache_read_tokens += int(usage.get("cache_read_input_tokens") or 0)
                        cache_create_tokens += int(usage.get("cache_creation_input_tokens") or 0)

            if event_type == "user":
                message = obj.get("message", {})
                if isinstance(message, dict):
                    content = message.get("content")
                    if meta.agent == "?":
                        for text in flatten_strings(content):
                            detected = detect_agent_name(text)
                            if detected:
                                meta.agent = detected
                                break

            if event_type == "system" and meta.agent == "?":
                for text in flatten_strings(obj):
                    detected = detect_agent_name(text)
                    if detected:
                        meta.agent = detected
                        break

    if first_ts is None:
        try:
            file_dt = datetime.fromtimestamp(session_path.stat().st_mtime, tz=timezone.utc)
            first_ts = file_dt
            last_ts = file_dt
        except Exception:
            pass
    if meta.execution_cwd:
        meta.project_label = meta.execution_cwd
    if first_ts is not None:
        meta.started_at = first_ts.isoformat().replace("+00:00", "Z")
    if first_ts is not None and last_ts is not None:
        meta.duration_secs = max((last_ts - first_ts).total_seconds(), 0.0)
    meta.input_tokens = input_tokens
    meta.output_tokens = output_tokens
    meta.cache_read_tokens = cache_read_tokens
    meta.cache_creation_tokens = cache_create_tokens
    meta.compaction_count = compactions
    if meta.agent == "?":
        meta.agent = "claude-code"
    return stat


def load_codex_thread_models(codex_state_db: Path) -> dict[str, str]:
    if not codex_state_db.exists():
        return {}

    models: dict[str, str] = {}
    conn = sqlite3.connect(codex_state_db)
    try:
        query = "SELECT id, model FROM threads"
        for row in conn.execute(query):
            thread_id = str(row[0] or "")
            model = str(row[1] or "")
            if thread_id and model:
                models[thread_id] = model
    except Exception:
        return {}
    finally:
        conn.close()
    return models


def find_codex_native_session_files(codex_root: Path) -> list[Path]:
    if not codex_root.exists():
        return []
    return sorted(p for p in codex_root.rglob("*.jsonl") if p.is_file())


def parse_codex_native_session(
    session_path: Path,
    codex_models: dict[str, str],
    global_root: Path | None,
) -> SpawnStats | None:
    session_id = session_path.stem
    meta = SpawnMeta(
        key=(f"codex-native:{session_path.parent}", session_id),
        source="codex-native",
        project_id=f"codex-native:{session_path.parent}",
        project_label=session_path.parent.as_posix(),
        spawn_id=session_id,
        model=codex_models.get(session_id, "?"),
        harness="codex",
        duration_secs=0.0,
        status="succeeded",
        kind="session",
        agent="?",
        parent_id=None,
        dedupe_spawn_id=session_id,
        execution_cwd=None,
        state_path=session_path,
        history_path=session_path,
        started_at=None,
        harness_session_id=session_id,
    )
    stat = SpawnStats(meta=meta)

    first_ts: datetime | None = None
    last_ts: datetime | None = None
    meta_event_seen = False
    last_usage: dict[str, object] | None = None
    originator: str | None = None
    inspected_events = 0

    with session_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            obj = safe_json_loads(line)
            if obj is None:
                stat.malformed_lines += 1
                continue

            if parse_compaction_signal(obj):
                meta.compaction_count += 1

            ts = parse_iso_timestamp(obj.get("timestamp"))
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

            evt_type = str(obj.get("type") or "")
            payload = obj.get("payload", {})
            if not isinstance(payload, dict):
                payload = {}
            inspected_events += 1

            if evt_type == "session_meta":
                meta_event_seen = True
                session_id = str(payload.get("id") or session_path.stem)
                meta.spawn_id = session_id
                meta.key = (f"codex-native:{session_path.parent}", session_id)
                meta.dedupe_spawn_id = session_id
                meta.harness_session_id = session_id
                meta.model = codex_models.get(session_id, meta.model)
                meta_model = payload.get("model")
                if isinstance(meta_model, str) and meta_model and meta.model == "?":
                    meta.model = meta_model
                payload_originator = payload.get("originator")
                if isinstance(payload_originator, str) and payload_originator:
                    originator = payload_originator
                ts_text = payload.get("timestamp")
                if isinstance(ts_text, str) and ts_text.strip():
                    meta.started_at = ts_text.strip()
                cwd = payload.get("cwd")
                if isinstance(cwd, str) and cwd.strip():
                    meta.execution_cwd = cwd.strip()
                    meta.project_label = meta.execution_cwd
                base_text = (
                    payload.get("base_instructions", {}).get("text")
                    if isinstance(payload.get("base_instructions"), dict)
                    else None
                )
                if isinstance(base_text, str):
                    detected = extract_agent_from_codex_instructions(base_text)
                    if detected:
                        meta.agent = detected
            elif evt_type == "turn_context":
                model = payload.get("model")
                if isinstance(model, str) and model and meta.model == "?":
                    meta.model = model

            if meta.agent == "?" and inspected_events <= 8:
                if evt_type == "event_msg" and str(payload.get("type") or "") == "user_message":
                    user_text = "\n".join(flatten_strings(payload))
                    detected = extract_agent_from_codex_user_message(user_text)
                    if detected:
                        meta.agent = detected

            info = payload.get("info")
            if isinstance(info, dict):
                usage = info.get("total_token_usage")
                if isinstance(usage, dict):
                    last_usage = usage

            spawn_root = Path(meta.execution_cwd).expanduser() if meta.execution_cwd else None
            parse_codex_native_event(obj, stat, global_root, spawn_root)

    if not meta_event_seen:
        return None

    if meta.started_at is None and first_ts is not None:
        meta.started_at = first_ts.isoformat().replace("+00:00", "Z")
    if first_ts is not None and last_ts is not None:
        meta.duration_secs = max((last_ts - first_ts).total_seconds(), 0.0)

    if last_usage:
        total_input = int(last_usage.get("input_tokens") or 0)
        cached_input = int(last_usage.get("cached_input_tokens") or 0)
        meta.input_tokens = max(total_input - cached_input, 0)
        meta.cache_read_tokens = cached_input
        meta.output_tokens = int(last_usage.get("output_tokens") or 0)
        meta.cache_creation_tokens = 0

    if meta.model == "?" and meta.harness_session_id:
        meta.model = codex_models.get(meta.harness_session_id, "?")
    if meta.agent == "?" and originator == "codex_exec":
        meta.agent = "codex-cli"

    return stat


def collect_codex_native_sessions(
    codex_root: Path,
    codex_state_db: Path,
    global_root: Path | None,
) -> list[SpawnStats]:
    stats: list[SpawnStats] = []
    codex_models = load_codex_thread_models(codex_state_db)
    for session_path in find_codex_native_session_files(codex_root):
        parsed = parse_codex_native_session(session_path, codex_models, global_root)
        if parsed is not None:
            stats.append(parsed)
    return stats


def parse_opencode_session(
    session_row: sqlite3.Row,
    conn: sqlite3.Connection,
    global_root: Path | None,
    db_path: Path,
) -> SpawnStats:
    session_id = str(session_row["id"])
    directory = str(session_row["directory"] or "")
    slug = str(session_row["slug"] or "")
    title = str(session_row["title"] or "")
    parent_id = str(session_row["parent_id"]) if session_row["parent_id"] else None
    spawn_ref = detect_spawn_id_in_text(" ".join([slug, title, parent_id or "", directory]))
    started_at = parse_ms_timestamp(session_row["time_created"])
    time_updated_iso = parse_ms_timestamp(session_row["time_updated"])

    meta = SpawnMeta(
        key=(f"opencode:{session_row['project_id']}", session_id),
        source="opencode",
        project_id=f"opencode:{session_row['project_id']}",
        project_label=directory or f"opencode:{session_row['project_id']}",
        spawn_id=session_id,
        model=str(session_row["model"] or "?"),
        harness="opencode",
        duration_secs=0.0,
        status="succeeded",
        kind="session",
        agent=str(session_row["agent"] or "?"),
        parent_id=parent_id,
        dedupe_spawn_id=spawn_ref,
        execution_cwd=directory or None,
        state_path=db_path,
        history_path=db_path,
        started_at=started_at,
    )
    stat = SpawnStats(meta=meta)

    if started_at and time_updated_iso:
        s_dt = parse_iso_timestamp(started_at)
        e_dt = parse_iso_timestamp(time_updated_iso)
        if s_dt and e_dt:
            meta.duration_secs = max((e_dt - s_dt).total_seconds(), 0.0)

    input_tokens = 0
    output_tokens = 0
    cache_read_tokens = 0
    cache_create_tokens = 0
    total_cost = 0.0
    has_cost = False
    compactions = 0

    query = """
        SELECT data
        FROM part
        WHERE session_id = ?
        ORDER BY time_created ASC, id ASC
    """
    for (data_text,) in conn.execute(query, (session_id,)):
        obj = safe_json_loads(data_text)
        if obj is None:
            stat.malformed_lines += 1
            continue

        part_type = str(obj.get("type") or "")
        if part_type == "compaction":
            compactions += 1
        elif part_type == "step-finish":
            tokens = obj.get("tokens", {})
            if isinstance(tokens, dict):
                input_tokens += int(tokens.get("input") or 0)
                output_tokens += int(tokens.get("output") or 0)
                cache = tokens.get("cache", {})
                if isinstance(cache, dict):
                    cache_read_tokens += int(cache.get("read") or 0)
                    cache_create_tokens += int(cache.get("write") or 0)
            cost = obj.get("cost")
            if cost is not None:
                try:
                    total_cost += float(cost)
                    has_cost = True
                except Exception:
                    pass
        elif part_type == "text" and meta.agent == "?":
            txt = obj.get("text")
            if isinstance(txt, str):
                detected = detect_agent_name(txt)
                if detected:
                    meta.agent = detected
        elif part_type == "patch":
            raw_path = obj.get("filePath") or obj.get("file_path") or obj.get("path")
            if raw_path is None:
                state = obj.get("state")
                if isinstance(state, dict):
                    raw_path = state.get("filePath") or state.get("file_path") or state.get("path")
            p = canonicalize_path(str(raw_path or ""), global_root, Path(directory).expanduser() if directory else None)
            stat.add_edit(p)
        elif part_type == "tool":
            tool = str(obj.get("tool") or "").lower()
            state = obj.get("state", {})
            tool_input = state.get("input", {}) if isinstance(state, dict) else {}
            if not isinstance(tool_input, dict):
                tool_input = {}

            if tool == "bash":
                cmd = str(tool_input.get("command") or "")
                read_paths, greps = extract_from_shell(cmd)
                stat.grep_count += greps
                for rp in read_paths:
                    p = canonicalize_path(rp, global_root, Path(directory).expanduser() if directory else None)
                    stat.add_read(p)
            elif tool in {"grep", "glob"}:
                stat.grep_count += 1
            elif tool == "read":
                raw_path = tool_input.get("filePath") or tool_input.get("file_path") or tool_input.get("path")
                p = canonicalize_path(str(raw_path or ""), global_root, Path(directory).expanduser() if directory else None)
                stat.add_read(p)
            elif tool in {"edit", "write", "patch"}:
                raw_path = tool_input.get("filePath") or tool_input.get("file_path") or tool_input.get("path")
                p = canonicalize_path(str(raw_path or ""), global_root, Path(directory).expanduser() if directory else None)
                stat.add_edit(p)

    meta.input_tokens = input_tokens
    meta.output_tokens = output_tokens
    meta.cache_read_tokens = cache_read_tokens
    meta.cache_creation_tokens = cache_create_tokens
    meta.compaction_count = compactions
    if has_cost:
        meta.cost = total_cost
    if meta.agent == "?":
        meta.agent = "opencode"
    return stat


def load_opencode_sessions(opencode_db: Path, global_root: Path | None) -> list[SpawnStats]:
    if not opencode_db.exists():
        return []

    stats: list[SpawnStats] = []
    conn = sqlite3.connect(opencode_db)
    conn.row_factory = sqlite3.Row
    try:
        query = """
            SELECT id, project_id, parent_id, slug, directory, title, time_created, time_updated, agent, model
            FROM session
            ORDER BY time_created ASC
        """
        for row in conn.execute(query):
            stats.append(parse_opencode_session(row, conn, global_root, opencode_db))
    finally:
        conn.close()
    return stats


def detect_history_format(history_path: Path) -> str | None:
    with history_path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            obj = safe_json_loads(line)
            if obj is None:
                continue
            et = obj.get("event_type", "")
            if et in {"assistant", "user", "system", "rate_limit_event"}:
                return "claude"
            return "codex"
    return None


def find_spawn_state_files(projects_root: Path) -> list[Path]:
    return sorted(projects_root.glob("*/spawns/*/state.json"))


def normalize_model_name(model: str) -> str:
    return re.sub(r"-+", "-", model.strip().lower().replace("_", "-").replace(" ", "-"))


def resolve_rates(harness: str, model: str) -> dict[str, float] | None:
    harness_key = normalize_model_name(harness)
    model_key = normalize_model_name(model)
    harness_rates = COST_RATES_PER_MILLION.get(harness_key)
    if not harness_rates:
        return None
    rates = harness_rates.get(model_key)
    if rates:
        return rates

    # Handle common alias prefixes, e.g. "openai/gpt-5.4", "anthropic/claude-sonnet-4-6".
    if "/" in model_key:
        rates = harness_rates.get(model_key.split("/")[-1])
        if rates:
            return rates
    return None


def estimate_spawn_cost(meta: SpawnMeta) -> float | None:
    rates = resolve_rates(meta.harness, meta.model)
    if not rates:
        return None

    input_tokens = meta.input_tokens or 0
    cache_read_tokens = meta.cache_read_tokens or 0
    output_tokens = meta.output_tokens or 0

    return (
        input_tokens * rates["input"]
        + cache_read_tokens * rates["cached_input"]
        + output_tokens * rates["output"]
    ) / 1_000_000.0


def load_spawn_meta(state_path: Path) -> SpawnMeta | None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    spawn_id = str(data.get("id") or state_path.parent.name)
    project_id = state_path.parents[2].name
    history_path = state_path.with_name("history.jsonl")

    def get_float(key: str) -> float | None:
        value = data.get(key)
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    def get_int(key: str) -> int | None:
        value = data.get(key)
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    execution_cwd = str(data["execution_cwd"]) if data.get("execution_cwd") else None

    return SpawnMeta(
        key=(project_id, spawn_id),
        source="meridian",
        project_id=project_id,
        project_label=execution_cwd or project_id,
        spawn_id=spawn_id,
        model=str(data.get("model") or "?"),
        harness=str(data.get("harness") or "?"),
        duration_secs=float(data.get("duration_secs") or 0.0),
        status=str(data.get("status") or "?"),
        kind=str(data.get("kind") or "?"),
        agent=str(data.get("agent") or "?"),
        parent_id=(str(data["parent_id"]) if data.get("parent_id") else None),
        dedupe_spawn_id=spawn_id,
        execution_cwd=execution_cwd,
        state_path=state_path,
        history_path=history_path,
        started_at=str(data["started_at"]) if data.get("started_at") else None,
        cost=get_float("total_cost_usd"),
        input_tokens=get_int("input_tokens"),
        output_tokens=get_int("output_tokens"),
        cache_read_tokens=get_int("cache_read_input_tokens"),
        cache_creation_tokens=get_int("cache_creation_input_tokens"),
        harness_session_id=(str(data["harness_session_id"]) if data.get("harness_session_id") else None),
    )


def compute_depths(all_meta: dict[tuple[str, str], SpawnMeta]) -> dict[tuple[str, str], int | None]:
    memo: dict[tuple[str, str], int | None] = {}
    visiting: set[tuple[str, str]] = set()

    def depth_for(key: tuple[str, str]) -> int | None:
        if key in memo:
            return memo[key]
        if key in visiting:
            memo[key] = None
            return None
        visiting.add(key)
        meta = all_meta.get(key)
        if meta is None:
            memo[key] = None
            visiting.remove(key)
            return None

        if not meta.parent_id:
            memo[key] = 0
            visiting.remove(key)
            return 0

        parent_key = (meta.project_id, meta.parent_id)
        parent_depth = depth_for(parent_key)
        if parent_depth is None:
            memo[key] = None
        else:
            memo[key] = parent_depth + 1

        visiting.remove(key)
        return memo[key]

    for k in all_meta:
        depth_for(k)
    return memo


def detect_project_root(metas: list[SpawnMeta], override: str | None) -> Path | None:
    if override:
        return Path(override).expanduser().resolve()

    roots = [m.execution_cwd for m in metas if m.execution_cwd]
    if not roots:
        return None
    unique_roots = sorted(set(roots))
    if len(unique_roots) == 1:
        return Path(unique_roots[0]).expanduser()

    try:
        common = Path(os.path.commonpath(unique_roots))
    except Exception:
        return None

    # Ignore overly broad prefixes like '/' or '/home'.
    if len(common.parts) < 4:
        return None
    return common


def dedupe_external_sessions(
    meridian_stats: list[SpawnStats],
    claude_stats: list[SpawnStats],
    opencode_stats: list[SpawnStats],
    codex_native_stats: list[SpawnStats],
) -> tuple[list[SpawnStats], int, int, int]:
    meridian_spawn_ids = {s.meta.spawn_id for s in meridian_stats if s.meta.source == "meridian"}
    meridian_codex_session_ids = {
        s.meta.harness_session_id
        for s in meridian_stats
        if s.meta.source == "meridian" and s.meta.harness == "codex" and s.meta.harness_session_id
    }

    meridian_codex_fingerprints: dict[tuple[str, str], list[float]] = defaultdict(list)
    for stat in meridian_stats:
        if stat.meta.source != "meridian" or stat.meta.harness != "codex":
            continue
        if stat.meta.harness_session_id:
            continue
        if not stat.meta.execution_cwd or not stat.meta.started_at:
            continue
        ts_dt = parse_iso_timestamp(stat.meta.started_at)
        if ts_dt is None:
            continue
        fp_key = (os.path.normpath(stat.meta.execution_cwd), normalize_model_name(stat.meta.model))
        meridian_codex_fingerprints[fp_key].append(ts_dt.timestamp())
    for key in meridian_codex_fingerprints:
        meridian_codex_fingerprints[key].sort()

    kept_claude: list[SpawnStats] = []
    skipped_claude = 0
    for stat in claude_stats:
        spawn_ref = stat.meta.dedupe_spawn_id
        if spawn_ref and spawn_ref in meridian_spawn_ids:
            skipped_claude += 1
            continue
        kept_claude.append(stat)

    kept_opencode: list[SpawnStats] = []
    skipped_opencode = 0
    for stat in opencode_stats:
        spawn_ref = stat.meta.dedupe_spawn_id or detect_spawn_id_in_text(
            " ".join([stat.meta.spawn_id, stat.meta.parent_id or "", stat.meta.project_label])
        )
        if spawn_ref and spawn_ref in meridian_spawn_ids:
            skipped_opencode += 1
            continue
        kept_opencode.append(stat)

    kept_codex_native: list[SpawnStats] = []
    skipped_codex_native = 0
    for stat in codex_native_stats:
        sid = stat.meta.harness_session_id or stat.meta.spawn_id
        if sid and sid in meridian_codex_session_ids:
            skipped_codex_native += 1
            continue

        matched = False
        if stat.meta.execution_cwd and stat.meta.started_at:
            ts_dt = parse_iso_timestamp(stat.meta.started_at)
            if ts_dt is not None:
                fp_key = (os.path.normpath(stat.meta.execution_cwd), normalize_model_name(stat.meta.model))
                ts = ts_dt.timestamp()
                for m_ts in meridian_codex_fingerprints.get(fp_key, []):
                    if abs(ts - m_ts) <= 5.0:
                        matched = True
                        break
        if matched:
            skipped_codex_native += 1
            continue

        kept_codex_native.append(stat)

    combined = [*meridian_stats, *kept_claude, *kept_opencode, *kept_codex_native]
    return combined, skipped_claude, skipped_opencode, skipped_codex_native


def format_ratio(reads: int, unique: int) -> str:
    if reads <= 0:
        return "0.0%"
    redundant = max(reads - unique, 0)
    return f"{(100.0 * redundant / reads):.1f}%"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt(row: Iterable[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))

    lines = [fmt(headers)]
    for row in rows:
        lines.append(fmt(row))
    return "\n".join(lines)


def summarize_group(group: GroupStats) -> tuple[int, int, int, str, int, int, int]:
    reads = group.reads
    unique = len(group.files)
    redundant = max(reads - unique, 0)
    ratio = format_ratio(reads, unique)
    return reads, unique, redundant, ratio, group.edit_count, group.grep_count, group.read_after_edit


def collect_all_stats(args: argparse.Namespace) -> list[SpawnStats]:
    """Collect and parse all sessions from configured sources.

    Extracted from main() so the tracking script can reuse it.
    Returns a list of SpawnStats for all succeeded sessions.
    """
    projects_root = Path(args.projects_root).expanduser()
    claude_root = Path(args.claude_root).expanduser()
    opencode_db = Path(args.opencode_db).expanduser()
    codex_root = Path(args.codex_root).expanduser()
    codex_state_db = Path(args.codex_state_db).expanduser()

    state_files = find_spawn_state_files(projects_root)
    all_meta: dict[tuple[str, str], SpawnMeta] = {}
    for sf in state_files:
        meta = load_spawn_meta(sf)
        if meta is not None:
            all_meta[meta.key] = meta

    # Meridian source parse.
    succeeded_meridian: list[SpawnMeta] = []
    for meta in all_meta.values():
        if meta.status != "succeeded":
            continue
        if not meta.history_path.exists() or meta.history_path.stat().st_size == 0:
            continue
        succeeded_meridian.append(meta)

    succeeded_meridian.sort(key=lambda m: (m.project_id, m.spawn_id))
    global_root = detect_project_root(succeeded_meridian, getattr(args, "project_root", None))

    meridian_stats: list[SpawnStats] = []
    for meta in succeeded_meridian:
        fmt = detect_history_format(meta.history_path)
        if fmt is None:
            continue
        stat = SpawnStats(meta=meta)
        spawn_root = Path(meta.execution_cwd).expanduser() if meta.execution_cwd else None
        if fmt == "claude":
            parse_claude_history(meta.history_path, stat, global_root, spawn_root)
        else:
            parse_codex_history(meta.history_path, stat, global_root, spawn_root)
        meridian_stats.append(stat)

    # External sources.
    source = getattr(args, "source", "all")
    claude_stats = load_claude_sessions(claude_root, global_root) if source in {"all", "claude"} else []
    opencode_stats = load_opencode_sessions(opencode_db, global_root) if source in {"all", "opencode"} else []
    codex_native_stats = (
        collect_codex_native_sessions(codex_root, codex_state_db, global_root)
        if source in {"all", "codex-native"}
        else []
    )

    merged_stats, _, _, _ = dedupe_external_sessions(
        meridian_stats,
        claude_stats,
        opencode_stats,
        codex_native_stats,
    )

    if source == "meridian":
        return meridian_stats
    elif source == "claude":
        return [s for s in merged_stats if s.meta.source == "claude-code"]
    elif source == "opencode":
        return [s for s in merged_stats if s.meta.source == "opencode"]
    elif source == "codex-native":
        return [s for s in merged_stats if s.meta.source == "codex-native"]
    else:
        return merged_stats


def main() -> int:
    args = parse_args()
    projects_root = Path(args.projects_root).expanduser()
    claude_root = Path(args.claude_root).expanduser()
    opencode_db = Path(args.opencode_db).expanduser()
    codex_root = Path(args.codex_root).expanduser()
    codex_state_db = Path(args.codex_state_db).expanduser()

    state_files = find_spawn_state_files(projects_root)
    all_meta: dict[tuple[str, str], SpawnMeta] = {}
    for sf in state_files:
        meta = load_spawn_meta(sf)
        if meta is not None:
            all_meta[meta.key] = meta

    # Meridian source parse.
    succeeded_meridian: list[SpawnMeta] = []
    for meta in all_meta.values():
        if meta.status != "succeeded":
            continue
        if not meta.history_path.exists() or meta.history_path.stat().st_size == 0:
            continue
        succeeded_meridian.append(meta)

    succeeded_meridian.sort(key=lambda m: (m.project_id, m.spawn_id))
    global_root = detect_project_root(succeeded_meridian, args.project_root)
    depths = compute_depths(all_meta)

    meridian_stats: list[SpawnStats] = []
    skipped_empty = 0
    for meta in succeeded_meridian:
        fmt = detect_history_format(meta.history_path)
        if fmt is None:
            skipped_empty += 1
            continue
        stat = SpawnStats(meta=meta)
        spawn_root = Path(meta.execution_cwd).expanduser() if meta.execution_cwd else None
        if fmt == "claude":
            parse_claude_history(meta.history_path, stat, global_root, spawn_root)
        else:
            parse_codex_history(meta.history_path, stat, global_root, spawn_root)
        meridian_stats.append(stat)

    # External sources.
    claude_stats = load_claude_sessions(claude_root, global_root) if args.source in {"all", "claude"} else []
    opencode_stats = load_opencode_sessions(opencode_db, global_root) if args.source in {"all", "opencode"} else []
    codex_native_stats = (
        collect_codex_native_sessions(codex_root, codex_state_db, global_root)
        if args.source in {"all", "codex-native"}
        else []
    )

    merged_stats, skipped_claude_dupes, skipped_opencode_dupes, skipped_codex_native_dupes = dedupe_external_sessions(
        meridian_stats,
        claude_stats,
        opencode_stats,
        codex_native_stats,
    )

    if args.source == "meridian":
        spawn_stats = meridian_stats
    elif args.source == "claude":
        spawn_stats = [s for s in merged_stats if s.meta.source == "claude-code"]
    elif args.source == "opencode":
        spawn_stats = [s for s in merged_stats if s.meta.source == "opencode"]
    elif args.source == "codex-native":
        spawn_stats = [s for s in merged_stats if s.meta.source == "codex-native"]
    else:
        spawn_stats = merged_stats

    def sort_key(stat: SpawnStats) -> tuple[int, str]:
        ts_dt = parse_iso_timestamp(stat.meta.started_at) if stat.meta.started_at else None
        ts = ts_dt.timestamp() if ts_dt is not None else 0.0
        return (int(ts), f"{stat.meta.source}:{stat.meta.spawn_id}")

    def limit_sorted(items: list[SpawnStats]) -> list[SpawnStats]:
        ordered = sorted(items, key=sort_key, reverse=True)
        if args.limit is None:
            return ordered
        return ordered[: max(args.limit, 0)]

    if args.source == "all":
        split: dict[str, list[SpawnStats]] = defaultdict(list)
        for stat in spawn_stats:
            split[stat.meta.source].append(stat)
        for key in list(split.keys()):
            split[key] = sorted(split[key], key=sort_key, reverse=True)
        if args.limit is None:
            spawn_stats = [
                *split.get("meridian", []),
                *split.get("claude-code", []),
                *split.get("opencode", []),
                *split.get("codex-native", []),
            ]
            spawn_stats.sort(key=sort_key, reverse=True)
        else:
            limit_value = max(args.limit, 0)
            selected: list[SpawnStats] = []
            # Reserve at most one slot per source so --limit still surfaces all sources.
            for source_key in ("meridian", "claude-code", "opencode", "codex-native"):
                bucket = split.get(source_key, [])
                if bucket and len(selected) < limit_value:
                    selected.append(bucket.pop(0))
            remainder: list[SpawnStats] = []
            for bucket in split.values():
                remainder.extend(bucket)
            remainder.sort(key=sort_key, reverse=True)
            if len(selected) < limit_value:
                selected.extend(remainder[: max(limit_value - len(selected), 0)])
            spawn_stats = selected
    else:
        spawn_stats = limit_sorted(spawn_stats)

    if not spawn_stats:
        print("No analyzable sessions found for requested source/filter.")
        return 0

    per_spawn_rows: list[list[str]] = []
    by_model: dict[str, GroupStats] = defaultdict(GroupStats)
    by_agent: dict[str, GroupStats] = defaultdict(GroupStats)
    by_model_agent: dict[tuple[str, str], GroupStats] = defaultdict(GroupStats)
    by_kind: dict[str, GroupStats] = defaultdict(GroupStats)
    by_source: dict[str, GroupStats] = defaultdict(GroupStats)
    by_project: dict[str, GroupStats] = defaultdict(GroupStats)
    by_harness: dict[str, GroupStats] = defaultdict(GroupStats)
    by_harness_model: dict[str, GroupStats] = defaultdict(GroupStats)
    by_date: dict[str, GroupStats] = defaultdict(GroupStats)
    by_week: dict[str, GroupStats] = defaultdict(GroupStats)
    overall = GroupStats()

    top_files = Counter()
    file_sessions: dict[str, set[str]] = defaultdict(set)

    total_malformed = 0

    for stat in spawn_stats:
        meta = stat.meta
        total_reads = sum(stat.read_counts.values())
        unique_files = len(stat.read_counts)
        redundant = max(total_reads - unique_files, 0)
        depth = depths.get(meta.key)
        depth_str = "?" if depth is None else str(depth)
        session_key = f"{meta.source}:{meta.project_id}:{meta.spawn_id}"

        per_spawn_rows.append(
            [
                meta.source,
                meta.spawn_id,
                meta.project_label,
                meta.kind,
                depth_str,
                meta.agent,
                meta.model,
                f"{int(round(meta.duration_secs))}s",
                str(total_reads),
                str(unique_files),
                str(redundant),
                format_ratio(total_reads, unique_files),
                str(stat.edit_count),
                str(stat.grep_count),
                str(stat.read_after_edit),
                str(meta.compaction_count),
            ]
        )

        overall.add_spawn(stat)
        by_model[meta.model].add_spawn(stat)
        by_agent[meta.agent].add_spawn(stat)
        by_model_agent[(meta.model, meta.agent)].add_spawn(stat)
        by_kind[meta.kind].add_spawn(stat)
        by_source[meta.source].add_spawn(stat)
        by_project[meta.project_label].add_spawn(stat)
        by_harness[meta.harness].add_spawn(stat)
        hm_key = f"{meta.harness}/{meta.model}"
        by_harness_model[hm_key].add_spawn(stat)
        if meta.started_at:
            date_key = meta.started_at[:10]  # YYYY-MM-DD
            by_date[date_key].add_spawn(stat)
            # ISO week
            try:
                d = dt_date.fromisoformat(date_key)
                yr, wk, _ = d.isocalendar()
                by_week[f"{yr}-W{wk:02d}"].add_spawn(stat)
            except Exception:
                pass

        total_malformed += stat.malformed_lines

        top_files.update(stat.read_counts)
        for f in stat.read_counts:
            file_sessions[f].add(session_key)

    headers = [
        "SOURCE",
        "SPAWN",
        "PROJECT",
        "KIND",
        "DEPTH",
        "AGENT",
        "MODEL",
        "DUR",
        "READS",
        "UNIQUE",
        "REDUNDANT",
        "RATIO",
        "EDITS",
        "GREPS",
        "RAE",
        "COMPACTS",
    ]

    print("Per-spawn summary")
    print(render_table(headers, per_spawn_rows))
    print()

    reads, unique, redundant, ratio, edits, greps, rae = summarize_group(overall)
    print("Aggregate")
    print(f"Total spawns analyzed: {overall.spawns}")
    print(
        f"Total reads: {reads}, unique files: {unique}, redundant reads: {redundant}, "
        f"ratio: {ratio}, edits: {edits}, greps: {greps}, read-after-edit: {rae}"
    )
    if global_root:
        print(f"Project root used for stripping: {global_root}")
    if skipped_empty:
        print(f"Skipped empty/unreadable histories during format detection: {skipped_empty}")
    if skipped_claude_dupes:
        print(f"Skipped Claude sessions deduped to meridian spawns: {skipped_claude_dupes}")
    if skipped_opencode_dupes:
        print(f"Skipped OpenCode sessions deduped to meridian spawns: {skipped_opencode_dupes}")
    if skipped_codex_native_dupes:
        print(f"Skipped Codex native sessions deduped to meridian spawns: {skipped_codex_native_dupes}")
    if total_malformed:
        print(f"Malformed JSONL lines skipped: {total_malformed}")
    print()

    def render_group(name: str, groups: dict[str, GroupStats]) -> None:
        rows: list[list[str]] = []
        for key, g in sorted(groups.items(), key=lambda kv: (-kv[1].reads, kv[0])):
            gr, gu, gd, grt, ge, gg, grae = summarize_group(g)
            rows.append(
                [
                    key,
                    str(g.spawns),
                    str(gr),
                    str(gu),
                    str(gd),
                    grt,
                    str(ge),
                    str(gg),
                    str(grae),
                ]
            )

        print(name)
        print(render_table(["KEY", "SPAWNS", "READS", "UNIQUE", "REDUNDANT", "RATIO", "EDITS", "GREPS", "RAE"], rows))
        print()

    def render_model_agent_group(groups: dict[tuple[str, str], GroupStats], min_spawns: int = 5) -> None:
        rows: list[list[str]] = []
        for (model, agent), g in sorted(groups.items(), key=lambda kv: (-kv[1].reads, kv[0][0], kv[0][1])):
            if g.spawns < min_spawns:
                continue
            gr, gu, gd, grt, _, _, _ = summarize_group(g)
            rows.append(
                [
                    model,
                    agent,
                    str(g.spawns),
                    str(gr),
                    str(gu),
                    str(gd),
                    grt,
                ]
            )

        print("By-model/agent")
        print(render_table(["MODEL", "AGENT", "SPAWNS", "READS", "UNIQUE", "REDUNDANT", "RATIO"], rows))
        print()

    render_group("By-model", by_model)
    render_group("By-agent", by_agent)
    render_model_agent_group(by_model_agent)
    render_group("By-kind", by_kind)
    render_group("By-source", by_source)
    render_group("By-project", by_project)
    render_group("By-harness", by_harness)
    render_group("By-harness/model", by_harness_model)
    render_group("By-week", {k: by_week[k] for k in sorted(by_week.keys())})
    render_group("By-date", {k: by_date[k] for k in sorted(by_date.keys())})

    # Compaction reporting.
    compact_sessions = [s for s in spawn_stats if s.meta.compaction_count > 0]
    no_compact_sessions = [s for s in spawn_stats if s.meta.compaction_count == 0]
    print("Compaction tracking")
    print(f"Sessions with compaction:    {len(compact_sessions)}")
    print(f"Sessions without compaction: {len(no_compact_sessions)}")
    print(f"Total compaction events:     {sum(s.meta.compaction_count for s in spawn_stats)}")
    print()

    if compact_sessions:
        rows = [
            [s.meta.source, s.meta.spawn_id, str(s.meta.compaction_count), s.meta.project_label]
            for s in sorted(compact_sessions, key=lambda x: (-x.meta.compaction_count, x.meta.spawn_id))[:30]
        ]
        print("Compaction events by session (top 30)")
        print(render_table(["SOURCE", "SESSION", "COMPACTIONS", "PROJECT"], rows))
        print()

    # Token and cost summary
    total_input = sum(s.meta.input_tokens or 0 for s in spawn_stats)
    total_output = sum(s.meta.output_tokens or 0 for s in spawn_stats)
    total_cache_read = sum(s.meta.cache_read_tokens or 0 for s in spawn_stats)
    total_cache_create = sum(s.meta.cache_creation_tokens or 0 for s in spawn_stats)
    total_cost_reported = sum(s.meta.cost or 0.0 for s in spawn_stats)
    spawns_with_cost = sum(1 for s in spawn_stats if s.meta.cost is not None)
    cache_hit_ratio = (total_cache_read / (total_input + total_cache_read + total_cache_create) * 100) if (total_input + total_cache_read + total_cache_create) > 0 else 0.0

    harness_cost_rollup: dict[str, CostRollup] = defaultdict(CostRollup)
    harness_model_cost_rollup: dict[str, CostRollup] = defaultdict(CostRollup)
    date_cost_rollup: dict[str, CostRollup] = defaultdict(CostRollup)
    week_cost_rollup: dict[str, CostRollup] = defaultdict(CostRollup)
    week_harness_cost_rollup: dict[str, CostRollup] = defaultdict(CostRollup)
    overall_cost_rollup = CostRollup()

    for s in spawn_stats:
        actual_cost = s.meta.cost
        estimated_cost = None
        if actual_cost is None and args.estimate_costs:
            estimated_cost = estimate_spawn_cost(s.meta)
        effective_cost = actual_cost if actual_cost is not None else estimated_cost

        overall_cost_rollup.add(effective_cost, actual_cost, estimated_cost)
        harness_cost_rollup[s.meta.harness].add(effective_cost, actual_cost, estimated_cost)
        harness_model_cost_rollup[f"{s.meta.harness}/{s.meta.model}"].add(effective_cost, actual_cost, estimated_cost)

        if s.meta.started_at:
            date_key = s.meta.started_at[:10]
            date_cost_rollup[date_key].add(effective_cost, actual_cost, estimated_cost)
            try:
                d = dt_date.fromisoformat(date_key)
                yr, wk, _ = d.isocalendar()
                week_cost_rollup[f"{yr}-W{wk:02d}"].add(effective_cost, actual_cost, estimated_cost)
                week_harness_cost_rollup[f"{yr}-W{wk:02d}/{s.meta.harness}"].add(effective_cost, actual_cost, estimated_cost)
            except Exception:
                pass

    print("Token & Cost Summary")
    print(f"Total input tokens:          {total_input:>15,}")
    print(f"Total cache read tokens:     {total_cache_read:>15,}")
    print(f"Total cache creation tokens: {total_cache_create:>15,}")
    print(f"Total output tokens:         {total_output:>15,}")
    print(f"Cache hit ratio:             {cache_hit_ratio:>14.1f}%")
    print(f"Total reported cost (USD):   ${total_cost_reported:>14,.2f}")
    print(f"Estimated total cost (USD):  ${overall_cost_rollup.total_cost:>14,.2f}")
    print(f"Estimated fill-in (USD):     ${overall_cost_rollup.estimated_cost:>14,.2f}")
    print(f"Cost estimation enabled:     {str(args.estimate_costs):>15}")
    print(f"Spawns with cost data:       {spawns_with_cost:>15,}")
    print(f"Spawns using estimate:       {overall_cost_rollup.estimated_spawns:>15,}")
    print(f"Spawns missing cost:         {overall_cost_rollup.unknown_spawns:>15,}")
    print()

    # Cost by harness
    harness_rows: list[list[str]] = []
    for key, rollup in sorted(harness_cost_rollup.items(), key=lambda kv: (-kv[1].total_cost, kv[0])):
        harness_rows.append(
            [
                key,
                str(rollup.spawns),
                f"${rollup.total_cost:,.2f}",
                f"${rollup.actual_cost:,.2f}",
                f"${rollup.estimated_cost:,.2f}",
                str(rollup.estimated_spawns),
                "yes" if rollup.estimated_spawns > 0 else "no",
            ]
        )
    print("Cost by harness")
    print(render_table(["HARNESS", "SPAWNS", "EST_TOTAL", "REPORTED", "EST_FILL", "EST_SPAWNS", "USED_EST"], harness_rows))
    print()

    harness_model_rows: list[list[str]] = []
    for key, rollup in sorted(harness_model_cost_rollup.items(), key=lambda kv: (-kv[1].total_cost, kv[0])):
        harness_model_rows.append(
            [
                key,
                str(rollup.spawns),
                f"${rollup.total_cost:,.2f}",
                f"${rollup.actual_cost:,.2f}",
                f"${rollup.estimated_cost:,.2f}",
                str(rollup.estimated_spawns),
                "yes" if rollup.estimated_spawns > 0 else "no",
            ]
        )
    print("Cost by harness/model")
    print(render_table(["HARNESS/MODEL", "SPAWNS", "EST_TOTAL", "REPORTED", "EST_FILL", "EST_SPAWNS", "USED_EST"], harness_model_rows))
    print()

    week_rows: list[list[str]] = []
    for key in sorted(week_cost_rollup.keys()):
        rollup = week_cost_rollup[key]
        week_rows.append(
            [
                key,
                str(rollup.spawns),
                f"${rollup.total_cost:,.2f}",
                f"${rollup.actual_cost:,.2f}",
                f"${rollup.estimated_cost:,.2f}",
                str(rollup.estimated_spawns),
                "yes" if rollup.estimated_spawns > 0 else "no",
            ]
        )
    print("Cost by week")
    print(render_table(["WEEK", "SPAWNS", "EST_TOTAL", "REPORTED", "EST_FILL", "EST_SPAWNS", "USED_EST"], week_rows))
    print()

    # Cost by week/harness
    weeks_seen = sorted({k.split("/")[0] for k in week_harness_cost_rollup.keys()})
    harnesses_seen = sorted({k.split("/")[1] for k in week_harness_cost_rollup.keys()})
    wh_rows: list[list[str]] = []
    for week in weeks_seen:
        row = [week]
        for harness in harnesses_seen:
            rollup = week_harness_cost_rollup.get(f"{week}/{harness}")
            if rollup and rollup.total_cost > 0:
                row.append(f"${rollup.total_cost:,.0f}")
            else:
                row.append("-")
        row_total = sum(week_harness_cost_rollup.get(f"{week}/{h}", CostRollup()).total_cost for h in harnesses_seen)
        row.append(f"${row_total:,.0f}")
        wh_rows.append(row)
    print("Cost by week/harness")
    print(render_table(["WEEK"] + [h.upper() for h in harnesses_seen] + ["TOTAL"], wh_rows))
    print()

    date_rows: list[list[str]] = []
    for key in sorted(date_cost_rollup.keys()):
        rollup = date_cost_rollup[key]
        date_rows.append(
            [
                key,
                str(rollup.spawns),
                f"${rollup.total_cost:,.2f}",
                f"${rollup.actual_cost:,.2f}",
                f"${rollup.estimated_cost:,.2f}",
                str(rollup.estimated_spawns),
                "yes" if rollup.estimated_spawns > 0 else "no",
            ]
        )
    print("Cost by date")
    print(render_table(["DATE", "SPAWNS", "EST_TOTAL", "REPORTED", "EST_FILL", "EST_SPAWNS", "USED_EST"], date_rows))
    print()

    # Compaction correlation (reads/redundancy/cost).
    def summarize_compaction_bucket(items: list[SpawnStats]) -> list[str]:
        if not items:
            return ["0", "0.0", "0.0%", "$0.00"]
        reads_total = sum(sum(s.read_counts.values()) for s in items)
        uniques_total = sum(len(s.read_counts) for s in items)
        avg_reads = reads_total / len(items)
        redundancy_pct = (100.0 * max(reads_total - uniques_total, 0) / reads_total) if reads_total else 0.0
        total_cost = 0.0
        for s in items:
            actual = s.meta.cost
            estimated = estimate_spawn_cost(s.meta) if actual is None and args.estimate_costs else None
            effective = actual if actual is not None else estimated
            if effective is not None:
                total_cost += effective
        avg_cost = total_cost / len(items)
        return [str(len(items)), f"{avg_reads:.1f}", f"{redundancy_pct:.1f}%", f"${avg_cost:,.2f}"]

    print("Compaction correlation")
    corr_rows = [
        ["with_compaction", *summarize_compaction_bucket(compact_sessions)],
        ["without_compaction", *summarize_compaction_bucket(no_compact_sessions)],
    ]
    print(render_table(["GROUP", "SESSIONS", "AVG_READS", "REDUNDANCY", "AVG_COST"], corr_rows))
    print()

    print("Top 30 most-read files across all sessions")
    top_rows: list[list[str]] = []
    for file_path, count in top_files.most_common(30):
        top_rows.append([str(count), str(len(file_sessions[file_path])), file_path])
    print(render_table(["COUNT", "SESSIONS", "FILE"], top_rows))
    print()

    print("Read-after-edit per spawn")
    rae_rows = [
        [s.meta.source, s.meta.spawn_id, str(s.read_after_edit)]
        for s in sorted(spawn_stats, key=lambda x: (-x.read_after_edit, x.meta.spawn_id))
    ]
    print(render_table(["SOURCE", "SPAWN", "RAE"], rae_rows))
    print(f"\nTotal read-after-edit count across all spawns: {overall.read_after_edit}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
