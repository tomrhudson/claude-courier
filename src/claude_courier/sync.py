"""Core sync logic: push, pull, status, diff."""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from claude_courier import git_ops, history
from claude_courier.config import Config
from claude_courier.path_mapper import (
    build_project_map,
    local_encoded_dir_for_project,
)

ACTIVE_SESSION_THRESHOLD_SECS = 60


@dataclass
class SyncFile:
    """A file that needs syncing."""

    relative_path: str  # relative to project dir (e.g., "abc123.jsonl")
    source: Path  # absolute source path
    dest: Path  # absolute destination path
    reason: str  # "new" or "longer"
    size: int = 0


@dataclass
class SyncPlan:
    """What a push or pull operation would do."""

    files_to_copy: list[SyncFile] = field(default_factory=list)
    history_changed: bool = False
    skipped_active: list[str] = field(default_factory=list)
    skipped_unmapped: list[str] = field(default_factory=list)


def _is_active(path: Path) -> bool:
    """Check if a file was modified recently (likely in-use)."""
    try:
        mtime = path.stat().st_mtime
        return (time.time() - mtime) < ACTIVE_SESSION_THRESHOLD_SECS
    except OSError:
        return False


def _should_copy(source: Path, dest: Path) -> str | None:
    """Determine if source should be copied to dest.

    Returns reason string ("new" or "longer") or None if no copy needed.
    """
    if not dest.exists():
        return "new"
    # "Longer file wins" for append-only JSONL
    source_size = source.stat().st_size
    dest_size = dest.stat().st_size
    if source_size > dest_size:
        return "longer"
    return None


def _iter_session_files(project_dir: Path) -> list[Path]:
    """List all syncable files in a project directory.

    Includes: .jsonl files, subagents/, tool-results/, .meta.json
    """
    files = []
    if not project_dir.exists():
        return files

    for item in project_dir.rglob("*"):
        if item.is_file():
            # Include JSONL, meta files, and tool results
            if (
                item.suffix == ".jsonl"
                or item.name == ".meta.json"
                or "tool-results" in item.parts
                or "subagents" in item.parts
            ):
                files.append(item)
    return files


def plan_push(config: Config, hub_path: Path) -> SyncPlan:
    """Plan what a push operation would do (without executing)."""
    plan = SyncPlan()
    project_map = build_project_map(config)

    # Track unmapped directories
    projects_dir = config.projects_dir
    if projects_dir.exists():
        all_dirs = {d.name for d in projects_dir.iterdir() if d.is_dir() and not d.name.startswith(".")}
        mapped_dirs = set()
        for dirs in project_map.values():
            mapped_dirs.update(dirs)
        plan.skipped_unmapped = sorted(all_dirs - mapped_dirs)

    for canonical, local_dirs in project_map.items():
        hub_project_dir = hub_path / "projects" / canonical

        for local_dir_name in local_dirs:
            local_project_dir = projects_dir / local_dir_name

            for local_file in _iter_session_files(local_project_dir):
                if _is_active(local_file):
                    plan.skipped_active.append(str(local_file.relative_to(projects_dir)))
                    continue

                rel = local_file.relative_to(local_project_dir)
                dest = hub_project_dir / rel
                reason = _should_copy(local_file, dest)
                if reason:
                    plan.files_to_copy.append(
                        SyncFile(
                            relative_path=f"projects/{canonical}/{rel}",
                            source=local_file,
                            dest=dest,
                            reason=reason,
                            size=local_file.stat().st_size,
                        )
                    )

    return plan


def plan_pull(config: Config, hub_path: Path) -> SyncPlan:
    """Plan what a pull operation would do (without executing)."""
    plan = SyncPlan()
    projects_dir = config.projects_dir
    hub_projects = hub_path / "projects"

    if not hub_projects.exists():
        return plan

    for canonical_dir in sorted(hub_projects.iterdir()):
        if not canonical_dir.is_dir():
            continue
        canonical = canonical_dir.name

        local_encoded = local_encoded_dir_for_project(config, canonical)
        if local_encoded is None:
            plan.skipped_unmapped.append(canonical)
            continue

        local_project_dir = projects_dir / local_encoded

        for hub_file in _iter_session_files(canonical_dir):
            rel = hub_file.relative_to(canonical_dir)
            dest = local_project_dir / rel
            reason = _should_copy(hub_file, dest)
            if reason:
                plan.files_to_copy.append(
                    SyncFile(
                        relative_path=f"{canonical}/{rel}",
                        source=hub_file,
                        dest=dest,
                        reason=reason,
                        size=hub_file.stat().st_size,
                    )
                )

    return plan


def execute_push(config: Config, hub_path: Path, dry_run: bool = False) -> SyncPlan:
    """Push local sessions to hub repository."""
    # Pull first to avoid conflicts
    git_ops.pull(hub_path)

    plan = plan_push(config, hub_path)

    if dry_run or not plan.files_to_copy:
        # Still check history even if no session files to copy
        if not dry_run:
            history_files = history.push_history(config, hub_path)
            plan.history_changed = bool(history_files)
        return plan

    # Copy files
    staged_files = []
    for sf in plan.files_to_copy:
        sf.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf.source, sf.dest)
        staged_files.append(sf.relative_path)

    # Handle history
    history_files = history.push_history(config, hub_path)
    staged_files.extend(history_files)
    plan.history_changed = bool(history_files)

    # Commit and push
    if staged_files:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        message = f"sync from {config.machine} at {timestamp}"
        git_ops.commit_and_push(hub_path, message, staged_files)

    return plan


def execute_pull(config: Config, hub_path: Path, dry_run: bool = False) -> SyncPlan:
    """Pull remote sessions from hub to local."""
    git_ops.pull(hub_path)

    plan = plan_pull(config, hub_path)

    if dry_run:
        return plan

    # Copy files
    for sf in plan.files_to_copy:
        sf.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf.source, sf.dest)

    # Merge history
    new_count = history.pull_history(config, hub_path)
    plan.history_changed = new_count > 0

    return plan


def execute_sync(config: Config, hub_path: Path, dry_run: bool = False) -> tuple[SyncPlan, SyncPlan]:
    """Push then pull. Returns (push_plan, pull_plan)."""
    push_plan = execute_push(config, hub_path, dry_run=dry_run)
    pull_plan = execute_pull(config, hub_path, dry_run=dry_run)
    return push_plan, pull_plan
