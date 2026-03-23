"""Tests for config module."""

import pytest
from pathlib import Path

from claude_courier.config import Config, ConfigError, set_machine_id, MACHINE_ID_FILE


@pytest.fixture
def config_yaml(tmp_path):
    """Create a minimal config.yaml for testing."""
    config = tmp_path / "config.yaml"
    config.write_text("""
machines:
  test-mac:
    claude_home: /Users/testuser/.claude
    desktop_home: /Users/testuser/Library/Application Support/Claude
  test-win:
    claude_home: C:\\Users\\testuser\\.claude

projects:
  my-project:
    test-mac: /Users/testuser/repos/my-project
    test-win: C:\\Users\\testuser\\repos\\my-project
  another-project:
    test-mac: /Users/testuser/repos/another
""")
    return tmp_path


def test_load_config(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    assert cfg.machine == "test-mac"


def test_load_missing_config(tmp_path):
    with pytest.raises(ConfigError, match="Config not found"):
        Config(tmp_path, machine_override="test-mac")


def test_load_invalid_config(tmp_path):
    (tmp_path / "config.yaml").write_text("just a string")
    with pytest.raises(ConfigError, match="Invalid config"):
        Config(tmp_path, machine_override="test-mac")


def test_missing_machines_key(tmp_path):
    (tmp_path / "config.yaml").write_text("projects: {}")
    with pytest.raises(ConfigError, match="Missing required key 'machines'"):
        Config(tmp_path, machine_override="test-mac")


def test_claude_home(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    assert cfg.claude_home == Path("/Users/testuser/.claude")


def test_claude_home_windows(config_yaml):
    cfg = Config(config_yaml, machine_override="test-win")
    assert str(cfg.claude_home) == "C:\\Users\\testuser\\.claude"


def test_get_projects(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    projects = cfg.get_projects()
    assert "my-project" in projects
    assert "another-project" in projects
    assert projects["my-project"] == "/Users/testuser/repos/my-project"


def test_get_projects_windows(config_yaml):
    cfg = Config(config_yaml, machine_override="test-win")
    projects = cfg.get_projects()
    assert "my-project" in projects
    assert "another-project" not in projects  # not configured for windows


def test_canonical_for_local_path(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    assert cfg.canonical_for_local_path("/Users/testuser/repos/my-project") == "my-project"
    assert cfg.canonical_for_local_path("/Users/testuser/repos/unknown") is None


def test_local_path_for_canonical(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    assert cfg.local_path_for_canonical("my-project") == "/Users/testuser/repos/my-project"
    assert cfg.local_path_for_canonical("nonexistent") is None


def test_desktop_home_configured(config_yaml):
    cfg = Config(config_yaml, machine_override="test-mac")
    assert cfg.desktop_home == Path("/Users/testuser/Library/Application Support/Claude")


def test_desktop_home_not_configured(config_yaml):
    cfg = Config(config_yaml, machine_override="test-win")
    assert cfg.desktop_home is None


def test_machine_detection_no_file(config_yaml, monkeypatch):
    monkeypatch.setattr("claude_courier.config.MACHINE_ID_FILE", config_yaml / "nonexistent")
    with pytest.raises(ConfigError, match="Machine identity not set"):
        Config(config_yaml)


def test_machine_detection_from_file(config_yaml, monkeypatch):
    machine_file = config_yaml / "machine-id"
    machine_file.write_text("test-mac\n")
    monkeypatch.setattr("claude_courier.config.MACHINE_ID_FILE", machine_file)
    cfg = Config(config_yaml)
    assert cfg.machine == "test-mac"
