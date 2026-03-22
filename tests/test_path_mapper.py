"""Tests for path_mapper module."""

import pytest
from pathlib import Path

from claude_courier.config import Config
from claude_courier.path_mapper import (
    encode_path,
    match_local_dir_to_canonical,
    build_project_map,
    local_encoded_dir_for_project,
)


@pytest.fixture
def config_with_projects(tmp_path):
    """Create config and fake project dirs."""
    config_file = tmp_path / "hub" / "config.yaml"
    config_file.parent.mkdir()
    config_file.write_text("""
machines:
  test-mac:
    claude_home: {claude_home}

projects:
  my-project:
    test-mac: /Users/testuser/repos/my-project
  claude-courier:
    test-mac: /Users/testuser/Desktop/CoWork/REPOS/claude-courier
""".format(claude_home=tmp_path / "claude"))

    # Create fake project directories
    projects_dir = tmp_path / "claude" / "projects"
    projects_dir.mkdir(parents=True)

    # Main project dir
    (projects_dir / "-Users-testuser-repos-my-project").mkdir()
    (projects_dir / "-Users-testuser-repos-my-project" / "abc123.jsonl").write_text("{}")

    # Claude courier dir
    (projects_dir / "-Users-testuser-Desktop-CoWork-REPOS-claude-courier").mkdir()

    # Worktree dir (should map to my-project)
    (projects_dir / "-Users-testuser-repos-my-project--claude-worktrees-cool-name").mkdir()

    # Unmapped dir (should be skipped)
    (projects_dir / "-Users-testuser-some-other-project").mkdir()

    return Config(tmp_path / "hub", machine_override="test-mac")


class TestEncodePath:
    def test_unix_path(self):
        assert encode_path("/Users/tom/repos/my-project") == "-Users-tom-repos-my-project"

    def test_windows_path(self):
        assert encode_path("C:\\Users\\tom\\repos\\my-project") == "C:-Users-tom-repos-my-project"

    def test_trailing_slash(self):
        assert encode_path("/Users/tom/repos/") == "-Users-tom-repos-"

    def test_path_with_hyphens(self):
        # Hyphens in path components are preserved (this is why encoding is lossy)
        assert encode_path("/Users/tom/my-project") == "-Users-tom-my-project"


class TestMatchLocalDir:
    def test_match_known_project(self, config_with_projects):
        result = match_local_dir_to_canonical(
            "-Users-testuser-repos-my-project",
            config_with_projects,
        )
        assert result == "my-project"

    def test_match_worktree(self, config_with_projects):
        result = match_local_dir_to_canonical(
            "-Users-testuser-repos-my-project--claude-worktrees-cool-name",
            config_with_projects,
        )
        assert result == "my-project"

    def test_no_match(self, config_with_projects):
        result = match_local_dir_to_canonical(
            "-Users-testuser-some-other-project",
            config_with_projects,
        )
        assert result is None


class TestBuildProjectMap:
    def test_maps_projects_and_worktrees(self, config_with_projects):
        project_map = build_project_map(config_with_projects)

        assert "my-project" in project_map
        dirs = project_map["my-project"]
        assert "-Users-testuser-repos-my-project" in dirs
        assert "-Users-testuser-repos-my-project--claude-worktrees-cool-name" in dirs

    def test_maps_claude_courier(self, config_with_projects):
        project_map = build_project_map(config_with_projects)
        assert "claude-courier" in project_map

    def test_skips_unmapped(self, config_with_projects):
        project_map = build_project_map(config_with_projects)
        all_dirs = []
        for dirs in project_map.values():
            all_dirs.extend(dirs)
        assert "-Users-testuser-some-other-project" not in all_dirs


class TestLocalEncodedDir:
    def test_returns_encoded_dir(self, config_with_projects):
        result = local_encoded_dir_for_project(config_with_projects, "my-project")
        assert result == "-Users-testuser-repos-my-project"

    def test_returns_none_for_unknown(self, config_with_projects):
        result = local_encoded_dir_for_project(config_with_projects, "nonexistent")
        assert result is None
