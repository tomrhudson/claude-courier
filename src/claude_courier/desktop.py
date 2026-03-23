"""Claude Desktop snapshot sync — macOS only (v2).

Syncs the Claude Desktop application data directory as per-machine
snapshots in the hub git repo. Unlike Claude Code sync (append-only
JSONL with "longer file wins"), Desktop sync uses mtime+size comparison
on opaque binary files.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from claude_courier import git_ops
from claude_courier.config import Config

log = logging.getLogger(__name__)

# --- Constants ---

DESKTOP_BASE_MACOS = Path.home() / "Library" / "Application Support" / "Claude"

EXCLUDED_DIRS = {
    "Cache",
    "Code Cache",
    "GPUCache",
    "Crashpad",
    "VideoDecodeStats",
    "DawnWebGPUCache",
    "DawnGraphiteCache",
    "blob_storage",
}

EXCLUDED_FILES = {
    "Cookies",
    "Cookies-journal",
    "DIPS",
    "DIPS-wal",
    "DIPS-shm",
    "Preferences",
    "TransportSecurity",
    "Trust Tokens",
    "Trust Tokens-journal",
    "Network Persistent State",
    "LOCK",
    ".DS_Store",
    "SharedStorage",
    "SharedStorage-wal",
    "SharedStorage-shm",
}

CLAUDE_DESKTOP_PROCESS_NAME = "Claude"


# --- Exceptions ---

class DesktopRunningError(Exception):
    """Raised when Claude Desktop is running and sync cannot proceed."""
    pass


class DesktopPullConfirmationRequired(Exception):
    """Raised when pull would overwrite local data and needs user confirmation."""
    pass


# --- Dataclasses ---

@dataclass
class DesktopSyncFile:
    """A Desktop file that needs syncing."""
    relative_path: str   # relative to desktop home
    source: Path
    dest: Path
    reason: str          # "new", "newer", or "changed"
    size: int = 0


@dataclass
class DesktopSyncPlan:
    """What a Desktop push or pull operation would do."""
    files_to_copy: list[DesktopSyncFile] = field(default_factory=list)
    files_unchanged: int = 0
    files_excluded: int = 0
    total_size: int = 0
    source_machine: str | None = None  # for pull: which machine snapshot


# --- Utility functions ---

def get_desktop_home(config: Config) -> Path | None:
    """Return the Desktop data directory from config, or None if not configured."""
    return config.desktop_home


def is_desktop_running() -> bool:
    """Check if Claude Desktop is currently running (macOS only)."""
    try:
        result = subprocess.run(
            ["pgrep", "-x", CLAUDE_DESKTOP_PROCESS_NAME],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except FileNotFoundError:
        # pgrep not available (non-macOS)
        return False


def iter_desktop_files(desktop_home: Path) -> list[Path]:
    """Walk Desktop data directory, returning files to sync (excluding caches etc)."""
    files = []
    if not desktop_home.exists():
        return files

    for item in desktop_home.rglob("*"):
        if not item.is_file():
            continue

        # Check if any parent directory is excluded
        rel = item.relative_to(desktop_home)
        if any(part in EXCLUDED_DIRS for part in rel.parts):
            continue

        # Check if file name is excluded
        if item.name in EXCLUDED_FILES:
            continue

        files.append(item)

    return files


def should_copy_desktop_file(source: Path, dest: Path) -> str | None:
    """Determine if source should be copied to dest.

    Returns reason string ("new", "newer", "changed") or None.
    Uses mtime+size comparison (not "longer file wins" — Desktop files are binary).
    """
    if not dest.exists():
        return "new"

    source_stat = source.stat()
    dest_stat = dest.stat()

    if source_stat.st_mtime > dest_stat.st_mtime:
        return "newer"

    if source_stat.st_size != dest_stat.st_size:
        return "changed"

    return None


# --- Metadata ---

def write_metadata(hub_desktop_machine_dir: Path, machine: str,
                   files: list[DesktopSyncFile]) -> None:
    """Write metadata.json tracking sync timestamp and file manifest."""
    manifest = {
        "machine": machine,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "file_count": len(files),
        "total_size_bytes": sum(f.size for f in files),
    }

    hub_desktop_machine_dir.mkdir(parents=True, exist_ok=True)
    meta_path = hub_desktop_machine_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def read_metadata(hub_desktop_machine_dir: Path) -> dict | None:
    """Read metadata.json from a machine's snapshot directory."""
    meta_path = hub_desktop_machine_dir / "metadata.json"
    if not meta_path.exists():
        return None
    with open(meta_path) as f:
        return json.load(f)


# --- Plan functions ---

def plan_desktop_push(config: Config, hub_path: Path) -> DesktopSyncPlan:
    """Plan what a Desktop push would do."""
    plan = DesktopSyncPlan()
    desktop_home = get_desktop_home(config)
    if desktop_home is None:
        return plan

    hub_desktop_dir = hub_path / "desktop" / config.machine

    local_files = iter_desktop_files(desktop_home)
    for local_file in local_files:
        rel = local_file.relative_to(desktop_home)
        dest = hub_desktop_dir / rel

        reason = should_copy_desktop_file(local_file, dest)
        if reason:
            size = local_file.stat().st_size
            plan.files_to_copy.append(
                DesktopSyncFile(
                    relative_path=str(rel),
                    source=local_file,
                    dest=dest,
                    reason=reason,
                    size=size,
                )
            )
            plan.total_size += size
        else:
            plan.files_unchanged += 1

    return plan


def plan_desktop_pull(config: Config, hub_path: Path,
                      from_machine: str | None = None) -> DesktopSyncPlan:
    """Plan what a Desktop pull would do.

    If from_machine is given, pull from that machine's snapshot.
    Otherwise, find the most recently synced snapshot.
    """
    plan = DesktopSyncPlan()
    desktop_home = get_desktop_home(config)
    if desktop_home is None:
        return plan

    hub_desktop = hub_path / "desktop"
    if not hub_desktop.exists():
        return plan

    # Determine source machine
    if from_machine:
        source_dir = hub_desktop / from_machine
        if not source_dir.exists():
            return plan
        plan.source_machine = from_machine
    else:
        # Find most recently synced snapshot (excluding own machine)
        best_machine = None
        best_time = ""
        for machine_dir in hub_desktop.iterdir():
            if not machine_dir.is_dir():
                continue
            if machine_dir.name == config.machine:
                continue
            meta = read_metadata(machine_dir)
            if meta and meta.get("synced_at", "") > best_time:
                best_time = meta["synced_at"]
                best_machine = machine_dir.name

        if best_machine is None:
            return plan
        source_dir = hub_desktop / best_machine
        plan.source_machine = best_machine

    # Compare hub snapshot files against local
    for hub_file in source_dir.rglob("*"):
        if not hub_file.is_file():
            continue
        if hub_file.name == "metadata.json" and hub_file.parent == source_dir:
            continue

        rel = hub_file.relative_to(source_dir)
        dest = desktop_home / rel

        reason = should_copy_desktop_file(hub_file, dest)
        if reason:
            size = hub_file.stat().st_size
            plan.files_to_copy.append(
                DesktopSyncFile(
                    relative_path=str(rel),
                    source=hub_file,
                    dest=dest,
                    reason=reason,
                    size=size,
                )
            )
            plan.total_size += size
        else:
            plan.files_unchanged += 1

    return plan


# --- Execute functions ---

def execute_desktop_push(config: Config, hub_path: Path,
                         dry_run: bool = False) -> DesktopSyncPlan:
    """Push Desktop snapshot to hub repository."""
    if is_desktop_running():
        raise DesktopRunningError(
            "Claude Desktop is running. Quit it before syncing."
        )

    desktop_home = get_desktop_home(config)
    if desktop_home is None:
        log.warning("desktop_home not configured for machine %s", config.machine)
        return DesktopSyncPlan()

    # Pull hub first
    git_ops.pull(hub_path)

    plan = plan_desktop_push(config, hub_path)

    if dry_run or not plan.files_to_copy:
        return plan

    # Copy files
    for sf in plan.files_to_copy:
        sf.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf.source, sf.dest)

    # Write metadata
    hub_desktop_dir = hub_path / "desktop" / config.machine
    write_metadata(hub_desktop_dir, config.machine, plan.files_to_copy)

    # Git commit and push
    staged_files = [
        f"desktop/{config.machine}/{sf.relative_path}"
        for sf in plan.files_to_copy
    ]
    staged_files.append(f"desktop/{config.machine}/metadata.json")

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    message = f"desktop sync from {config.machine} at {timestamp}"
    git_ops.commit_and_push(hub_path, message, staged_files)

    return plan


def execute_desktop_pull(config: Config, hub_path: Path,
                         from_machine: str | None = None,
                         dry_run: bool = False,
                         force: bool = False) -> DesktopSyncPlan:
    """Pull Desktop snapshot from hub to local."""
    if is_desktop_running():
        raise DesktopRunningError(
            "Claude Desktop is running. Quit it before syncing."
        )

    desktop_home = get_desktop_home(config)
    if desktop_home is None:
        log.warning("desktop_home not configured for machine %s", config.machine)
        return DesktopSyncPlan()

    # Pull hub first
    git_ops.pull(hub_path)

    plan = plan_desktop_pull(config, hub_path, from_machine=from_machine)

    if dry_run or not plan.files_to_copy:
        return plan

    # If local Desktop data exists and force is not set, require confirmation
    if desktop_home.exists() and any(desktop_home.iterdir()) and not force:
        raise DesktopPullConfirmationRequired(
            f"This will overwrite local Desktop data at {desktop_home}. "
            f"Source: {plan.source_machine} snapshot."
        )

    # Copy files
    for sf in plan.files_to_copy:
        sf.dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sf.source, sf.dest)

    return plan
