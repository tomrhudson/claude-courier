"""History file merge and deduplication."""

from __future__ import annotations

import json
from pathlib import Path

from claude_courier.config import Config


def _read_history(path: Path) -> list[dict]:
    """Read a history JSONL file, returning list of entry dicts."""
    if not path.exists():
        return []
    entries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _write_history(path: Path, entries: list[dict]) -> None:
    """Write entries to a history JSONL file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _entry_key(entry: dict) -> tuple:
    """Dedup key: (timestamp, sessionId)."""
    return (entry.get("timestamp"), entry.get("sessionId"))


def _dedup_and_sort(entries: list[dict]) -> list[dict]:
    """Remove duplicates by (timestamp, sessionId) and sort by timestamp."""
    seen = set()
    unique = []
    for entry in entries:
        key = _entry_key(entry)
        if key not in seen:
            seen.add(key)
            unique.append(entry)
    unique.sort(key=lambda e: e.get("timestamp", 0))
    return unique


def push_history(config: Config, hub_path: Path) -> list[str]:
    """Copy new local history entries to hub's machine-specific history file.

    Returns list of relative paths of files modified in the hub.
    """
    local_history = config.claude_home / "history.jsonl"
    machine_history = hub_path / "history" / f"{config.machine}.jsonl"

    local_entries = _read_history(local_history)
    hub_entries = _read_history(machine_history)

    merged = _dedup_and_sort(local_entries + hub_entries)

    if merged == hub_entries:
        return []

    _write_history(machine_history, merged)
    return [str(machine_history.relative_to(hub_path))]


def pull_history(config: Config, hub_path: Path) -> int:
    """Merge other machines' history into local history.jsonl.

    Rewrites project paths to local equivalents where possible.
    Returns count of new entries added.
    """
    local_history_path = config.claude_home / "history.jsonl"
    local_entries = _read_history(local_history_path)
    local_keys = {_entry_key(e) for e in local_entries}

    history_dir = hub_path / "history"
    if not history_dir.exists():
        return 0

    new_entries = []
    for history_file in history_dir.glob("*.jsonl"):
        # Skip our own machine's history (already have it locally)
        if history_file.stem == config.machine:
            continue
        for entry in _read_history(history_file):
            key = _entry_key(entry)
            if key not in local_keys:
                # Rewrite project path to local equivalent
                entry = _rewrite_project_path(entry, config)
                new_entries.append(entry)
                local_keys.add(key)

    if not new_entries:
        return 0

    all_entries = _dedup_and_sort(local_entries + new_entries)
    _write_history(local_history_path, all_entries)
    return len(new_entries)


def _rewrite_project_path(entry: dict, config: Config) -> dict:
    """Rewrite a history entry's project field to this machine's local path."""
    project = entry.get("project")
    if not project:
        return entry

    # Try to find which canonical project this path belongs to
    all_projects = config.get_all_projects()
    for canonical, machine_paths in all_projects.items():
        for machine, path in machine_paths.items():
            if config._paths_match(path, project):
                local_path = config.local_path_for_canonical(canonical)
                if local_path:
                    entry = dict(entry)
                    entry["project"] = local_path
                return entry

    return entry
