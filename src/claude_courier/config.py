"""Configuration loading and machine identity management."""

from __future__ import annotations

import socket
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import yaml

COURIER_HOME = Path.home() / ".claude-courier"
MACHINE_ID_FILE = COURIER_HOME / "machine-id"
DEFAULT_CONFIG_NAME = "config.yaml"


class ConfigError(Exception):
    pass


class Config:
    """Loads and provides access to claude-courier hub configuration."""

    def __init__(self, hub_path: Path, machine_override: str | None = None):
        self.hub_path = hub_path
        self._data = self._load(hub_path / DEFAULT_CONFIG_NAME)
        self._machine = machine_override or self._detect_machine()

    @staticmethod
    def _load(config_path: Path) -> dict[str, Any]:
        if not config_path.exists():
            raise ConfigError(f"Config not found: {config_path}")
        with open(config_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ConfigError(f"Invalid config format in {config_path}")
        for key in ("machines", "projects"):
            if key not in data:
                raise ConfigError(f"Missing required key '{key}' in config")
        return data

    def _detect_machine(self) -> str:
        if MACHINE_ID_FILE.exists():
            machine_id = MACHINE_ID_FILE.read_text().strip()
            if machine_id in self._data["machines"]:
                return machine_id
        raise ConfigError(
            f"Machine identity not set. Run 'claude-courier init' first, "
            f"or use --machine flag. "
            f"Available machines: {list(self._data['machines'].keys())}"
        )

    @property
    def machine(self) -> str:
        return self._machine

    @property
    def claude_home(self) -> Path:
        raw = self._data["machines"][self._machine]["claude_home"]
        return Path(raw)

    @property
    def projects_dir(self) -> Path:
        return self.claude_home / "projects"

    def get_projects(self) -> dict[str, str]:
        """Return {canonical_name: local_path} for projects configured on this machine."""
        result = {}
        for canonical, machine_paths in self._data["projects"].items():
            if self._machine in machine_paths:
                result[canonical] = machine_paths[self._machine]
        return result

    def get_all_projects(self) -> dict[str, dict[str, str]]:
        """Return full project config: {canonical: {machine: path}}."""
        return self._data["projects"]

    def get_machines(self) -> dict[str, dict[str, str]]:
        return self._data["machines"]

    def canonical_for_local_path(self, local_path: str) -> str | None:
        """Find canonical project name for a local path."""
        for canonical, machine_paths in self._data["projects"].items():
            if self._machine in machine_paths:
                configured = machine_paths[self._machine]
                if self._paths_match(configured, local_path):
                    return canonical
        return None

    @staticmethod
    def _paths_match(a: str, b: str) -> bool:
        """Compare paths cross-platform (normalize separators)."""
        return PurePosixPath(a.replace("\\", "/")) == PurePosixPath(b.replace("\\", "/"))

    def local_path_for_canonical(self, canonical: str) -> str | None:
        """Find local path for a canonical project name on this machine."""
        projects = self._data["projects"]
        if canonical in projects and self._machine in projects[canonical]:
            return projects[canonical][self._machine]
        return None


def set_machine_id(machine_name: str) -> None:
    """Write machine identity to persistent file."""
    COURIER_HOME.mkdir(parents=True, exist_ok=True)
    MACHINE_ID_FILE.write_text(machine_name + "\n")


def get_hub_path() -> Path:
    """Read the hub repo path from local config."""
    hub_path_file = COURIER_HOME / "hub-path"
    if not hub_path_file.exists():
        raise ConfigError(
            "Hub path not set. Run 'claude-courier init' first."
        )
    return Path(hub_path_file.read_text().strip())


def set_hub_path(hub_path: Path) -> None:
    """Write the hub repo path to local config."""
    COURIER_HOME.mkdir(parents=True, exist_ok=True)
    (COURIER_HOME / "hub-path").write_text(str(hub_path) + "\n")
