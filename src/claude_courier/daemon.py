"""Background sync daemon management (launchd on macOS, Task Scheduler on Windows)."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

LAUNCHD_LABEL = "com.claude-courier.sync"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
TASK_NAME = "ClaudeCourierSync"


def _get_courier_path() -> str:
    """Find the claude-courier executable."""
    path = shutil.which("claude-courier")
    if path:
        return path
    return sys.executable + " -m claude_courier.cli"


def _generate_plist(interval_minutes: int) -> str:
    courier = _get_courier_path()
    interval_seconds = interval_minutes * 60

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{courier}</string>
        <string>sync</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval_seconds}</integer>
    <key>RunAtLoad</key>
    <false/>
    <key>StandardOutPath</key>
    <string>{Path.home() / ".claude-courier" / "daemon-stdout.log"}</string>
    <key>StandardErrorPath</key>
    <string>{Path.home() / ".claude-courier" / "daemon-stderr.log"}</string>
</dict>
</plist>"""


def install(interval_minutes: int = 15) -> None:
    """Install the background sync daemon."""
    system = platform.system()

    if system == "Darwin":
        _install_launchd(interval_minutes)
    elif system == "Windows":
        _install_windows_task(interval_minutes)
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def uninstall() -> None:
    """Remove the background sync daemon."""
    system = platform.system()

    if system == "Darwin":
        _uninstall_launchd()
    elif system == "Windows":
        _uninstall_windows_task()
    else:
        raise RuntimeError(f"Unsupported platform: {system}")


def status() -> str:
    """Check daemon status. Returns a human-readable string."""
    system = platform.system()

    if system == "Darwin":
        return _status_launchd()
    elif system == "Windows":
        return _status_windows_task()
    else:
        return f"Unsupported platform: {system}"


# --- macOS (launchd) ---


def _install_launchd(interval_minutes: int) -> None:
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_text(_generate_plist(interval_minutes))
    subprocess.run(["launchctl", "load", str(PLIST_PATH)], check=True)


def _uninstall_launchd() -> None:
    if PLIST_PATH.exists():
        subprocess.run(["launchctl", "unload", str(PLIST_PATH)], check=False)
        PLIST_PATH.unlink()


def _status_launchd() -> str:
    if not PLIST_PATH.exists():
        return "Daemon not installed"

    result = subprocess.run(
        ["launchctl", "list", LAUNCHD_LABEL],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return f"Daemon installed and loaded\n{result.stdout.strip()}"
    return "Daemon installed but not loaded"


# --- Windows (Task Scheduler) ---


def _install_windows_task(interval_minutes: int) -> None:
    courier = _get_courier_path()
    subprocess.run(
        [
            "schtasks",
            "/create",
            "/tn",
            TASK_NAME,
            "/tr",
            f'"{courier}" sync',
            "/sc",
            "minute",
            "/mo",
            str(interval_minutes),
            "/f",
        ],
        check=True,
    )


def _uninstall_windows_task() -> None:
    subprocess.run(
        ["schtasks", "/delete", "/tn", TASK_NAME, "/f"],
        check=False,
    )


def _status_windows_task() -> str:
    result = subprocess.run(
        ["schtasks", "/query", "/tn", TASK_NAME],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return f"Daemon installed\n{result.stdout.strip()}"
    return "Daemon not installed"
