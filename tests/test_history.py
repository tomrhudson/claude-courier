"""Tests for history module."""

import json
import pytest
from pathlib import Path

from claude_courier.config import Config
from claude_courier.history import push_history, pull_history


@pytest.fixture
def setup(tmp_path):
    """Create a full test setup with config, local claude, and hub."""
    # Hub repo
    hub = tmp_path / "hub"
    hub.mkdir()
    (hub / "history").mkdir()
    (hub / "config.yaml").write_text(f"""
machines:
  machine-a:
    claude_home: {tmp_path / "claude-a"}
  machine-b:
    claude_home: {tmp_path / "claude-b"}

projects:
  my-project:
    machine-a: /Users/usera/repos/my-project
    machine-b: /Users/userb/repos/my-project
""")

    # Local claude dirs
    claude_a = tmp_path / "claude-a"
    claude_a.mkdir()
    claude_b = tmp_path / "claude-b"
    claude_b.mkdir()

    config_a = Config(hub, machine_override="machine-a")
    config_b = Config(hub, machine_override="machine-b")

    return {
        "hub": hub,
        "claude_a": claude_a,
        "claude_b": claude_b,
        "config_a": config_a,
        "config_b": config_b,
    }


def _write_history(path: Path, entries: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _read_history(path: Path) -> list[dict]:
    entries = []
    for line in path.read_text().splitlines():
        if line.strip():
            entries.append(json.loads(line))
    return entries


class TestPushHistory:
    def test_push_new_entries(self, setup):
        entries = [
            {"display": "hello", "timestamp": 1000, "sessionId": "s1", "project": "/Users/usera/repos/my-project"},
            {"display": "world", "timestamp": 2000, "sessionId": "s2", "project": "/Users/usera/repos/my-project"},
        ]
        _write_history(setup["claude_a"] / "history.jsonl", entries)

        files = push_history(setup["config_a"], setup["hub"])
        assert len(files) == 1
        assert "machine-a.jsonl" in files[0]

        hub_entries = _read_history(setup["hub"] / "history" / "machine-a.jsonl")
        assert len(hub_entries) == 2

    def test_push_dedup(self, setup):
        entries = [
            {"display": "hello", "timestamp": 1000, "sessionId": "s1", "project": "/test"},
        ]
        _write_history(setup["claude_a"] / "history.jsonl", entries)

        # Push twice
        push_history(setup["config_a"], setup["hub"])
        files = push_history(setup["config_a"], setup["hub"])

        # Second push should have no changes
        assert files == []

    def test_push_empty_history(self, setup):
        files = push_history(setup["config_a"], setup["hub"])
        assert files == []


class TestPullHistory:
    def test_pull_from_other_machine(self, setup):
        # Machine B has history on the hub
        b_entries = [
            {"display": "from b", "timestamp": 1000, "sessionId": "s1", "project": "/Users/userb/repos/my-project"},
        ]
        _write_history(setup["hub"] / "history" / "machine-b.jsonl", b_entries)

        # Machine A has its own history
        a_entries = [
            {"display": "from a", "timestamp": 2000, "sessionId": "s2", "project": "/Users/usera/repos/my-project"},
        ]
        _write_history(setup["claude_a"] / "history.jsonl", a_entries)

        new_count = pull_history(setup["config_a"], setup["hub"])
        assert new_count == 1

        merged = _read_history(setup["claude_a"] / "history.jsonl")
        assert len(merged) == 2
        # Should be sorted by timestamp
        assert merged[0]["timestamp"] == 1000
        assert merged[1]["timestamp"] == 2000

    def test_pull_rewrites_project_paths(self, setup):
        b_entries = [
            {"display": "from b", "timestamp": 1000, "sessionId": "s1", "project": "/Users/userb/repos/my-project"},
        ]
        _write_history(setup["hub"] / "history" / "machine-b.jsonl", b_entries)

        pull_history(setup["config_a"], setup["hub"])

        merged = _read_history(setup["claude_a"] / "history.jsonl")
        assert merged[0]["project"] == "/Users/usera/repos/my-project"

    def test_pull_skips_own_machine(self, setup):
        a_entries = [
            {"display": "own", "timestamp": 1000, "sessionId": "s1", "project": "/test"},
        ]
        _write_history(setup["hub"] / "history" / "machine-a.jsonl", a_entries)
        _write_history(setup["claude_a"] / "history.jsonl", a_entries)

        new_count = pull_history(setup["config_a"], setup["hub"])
        assert new_count == 0

    def test_pull_no_history_dir(self, setup):
        new_count = pull_history(setup["config_a"], setup["hub"])
        assert new_count == 0

    def test_pull_dedup(self, setup):
        entries = [
            {"display": "shared", "timestamp": 1000, "sessionId": "s1", "project": "/Users/userb/repos/my-project"},
        ]
        _write_history(setup["hub"] / "history" / "machine-b.jsonl", entries)
        _write_history(setup["claude_a"] / "history.jsonl", [
            {"display": "shared", "timestamp": 1000, "sessionId": "s1", "project": "/Users/usera/repos/my-project"},
        ])

        new_count = pull_history(setup["config_a"], setup["hub"])
        assert new_count == 0
