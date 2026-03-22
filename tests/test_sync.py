"""Integration tests for sync module."""

import json
import subprocess
import time
import pytest
from pathlib import Path

from claude_courier.config import Config
from claude_courier.sync import plan_push, plan_pull, execute_push, execute_pull


@pytest.fixture
def sync_env(tmp_path):
    """Set up a full sync environment with a git hub repo and two machines."""
    # Create a bare git repo as the "NAS hub"
    bare_repo = tmp_path / "bare-hub.git"
    bare_repo.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

    # Clone it as the working hub for machine A
    hub_a = tmp_path / "hub-a"
    subprocess.run(["git", "clone", str(bare_repo), str(hub_a)], check=True, capture_output=True)

    # Configure git user for commits
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hub_a, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=hub_a, check=True, capture_output=True)

    # Create initial config and commit it
    claude_a = tmp_path / "claude-a"
    claude_a.mkdir()
    (claude_a / "projects").mkdir()

    claude_b = tmp_path / "claude-b"
    claude_b.mkdir()
    (claude_b / "projects").mkdir()

    config_content = f"""
machines:
  machine-a:
    claude_home: {claude_a}
  machine-b:
    claude_home: {claude_b}

projects:
  test-project:
    machine-a: /Users/usera/repos/test-project
    machine-b: /Users/userb/repos/test-project
"""
    (hub_a / "config.yaml").write_text(config_content)
    (hub_a / "history").mkdir()
    subprocess.run(["git", "add", "."], cwd=hub_a, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=hub_a, check=True, capture_output=True)
    subprocess.run(["git", "push"], cwd=hub_a, check=True, capture_output=True)

    # Clone for machine B
    hub_b = tmp_path / "hub-b"
    subprocess.run(["git", "clone", str(bare_repo), str(hub_b)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hub_b, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=hub_b, check=True, capture_output=True)

    config_a = Config(hub_a, machine_override="machine-a")
    config_b = Config(hub_b, machine_override="machine-b")

    return {
        "hub_a": hub_a,
        "hub_b": hub_b,
        "claude_a": claude_a,
        "claude_b": claude_b,
        "config_a": config_a,
        "config_b": config_b,
    }


def _create_session(projects_dir: Path, encoded_name: str, session_id: str, content: str = "{}"):
    """Create a fake session JSONL file."""
    project_dir = projects_dir / encoded_name
    project_dir.mkdir(parents=True, exist_ok=True)
    session_file = project_dir / f"{session_id}.jsonl"
    session_file.write_text(content)
    # Set mtime to past to avoid active session detection
    old_time = time.time() - 120
    import os
    os.utime(session_file, (old_time, old_time))
    return session_file


class TestPlanPush:
    def test_detects_new_sessions(self, sync_env):
        _create_session(
            sync_env["claude_a"] / "projects",
            "-Users-usera-repos-test-project",
            "session-001",
            '{"type":"test"}\n',
        )

        plan = plan_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(plan.files_to_copy) == 1
        assert plan.files_to_copy[0].reason == "new"
        assert "test-project" in plan.files_to_copy[0].relative_path

    def test_skips_unmapped_projects(self, sync_env):
        _create_session(
            sync_env["claude_a"] / "projects",
            "-Users-usera-repos-unknown-project",
            "session-001",
        )

        plan = plan_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(plan.files_to_copy) == 0
        assert "-Users-usera-repos-unknown-project" in plan.skipped_unmapped

    def test_detects_longer_file(self, sync_env):
        # Create local session with more content
        _create_session(
            sync_env["claude_a"] / "projects",
            "-Users-usera-repos-test-project",
            "session-001",
            '{"line":1}\n{"line":2}\n',
        )
        # Create shorter version in hub
        hub_project = sync_env["hub_a"] / "projects" / "test-project"
        hub_project.mkdir(parents=True)
        (hub_project / "session-001.jsonl").write_text('{"line":1}\n')

        plan = plan_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(plan.files_to_copy) == 1
        assert plan.files_to_copy[0].reason == "longer"


class TestExecutePushPull:
    def test_push_then_pull(self, sync_env):
        # Machine A creates a session
        _create_session(
            sync_env["claude_a"] / "projects",
            "-Users-usera-repos-test-project",
            "session-abc",
            '{"from":"machine-a"}\n',
        )

        # Push from A
        push_plan = execute_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(push_plan.files_to_copy) == 1

        # Pull on B
        pull_plan = execute_pull(sync_env["config_b"], sync_env["hub_b"])
        assert len(pull_plan.files_to_copy) == 1

        # Verify file landed in B's claude dir
        expected = (
            sync_env["claude_b"]
            / "projects"
            / "-Users-userb-repos-test-project"
            / "session-abc.jsonl"
        )
        assert expected.exists()
        assert expected.read_text() == '{"from":"machine-a"}\n'

    def test_push_nothing_to_sync(self, sync_env):
        plan = execute_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(plan.files_to_copy) == 0

    def test_bidirectional_sync(self, sync_env):
        # A creates session-1
        _create_session(
            sync_env["claude_a"] / "projects",
            "-Users-usera-repos-test-project",
            "session-1",
            '{"from":"a"}\n',
        )

        # B creates session-2
        _create_session(
            sync_env["claude_b"] / "projects",
            "-Users-userb-repos-test-project",
            "session-2",
            '{"from":"b"}\n',
        )

        # A pushes, B pushes
        execute_push(sync_env["config_a"], sync_env["hub_a"])
        execute_push(sync_env["config_b"], sync_env["hub_b"])

        # A pulls, B pulls
        execute_pull(sync_env["config_a"], sync_env["hub_a"])
        execute_pull(sync_env["config_b"], sync_env["hub_b"])

        # Both should have both sessions
        a_project = sync_env["claude_a"] / "projects" / "-Users-usera-repos-test-project"
        b_project = sync_env["claude_b"] / "projects" / "-Users-userb-repos-test-project"

        assert (a_project / "session-1.jsonl").exists()
        assert (a_project / "session-2.jsonl").exists()
        assert (b_project / "session-1.jsonl").exists()
        assert (b_project / "session-2.jsonl").exists()

    def test_subagent_and_tool_results_synced(self, sync_env):
        project_dir = (
            sync_env["claude_a"] / "projects" / "-Users-usera-repos-test-project"
        )
        project_dir.mkdir(parents=True, exist_ok=True)

        # Create session with subagent and tool results
        session_dir = project_dir / "session-xyz"
        session_dir.mkdir()
        subagents = session_dir / "subagents"
        subagents.mkdir()
        (subagents / "agent-explore.jsonl").write_text('{"agent":"explore"}\n')

        tool_results = project_dir / "tool-results"
        tool_results.mkdir()
        (tool_results / "result1.txt").write_text("some output")

        # Set old mtime
        import os
        old_time = time.time() - 120
        for f in project_dir.rglob("*"):
            if f.is_file():
                os.utime(f, (old_time, old_time))

        # Push from A
        push_plan = execute_push(sync_env["config_a"], sync_env["hub_a"])
        assert len(push_plan.files_to_copy) == 2  # subagent + tool result

        # Pull on B
        pull_plan = execute_pull(sync_env["config_b"], sync_env["hub_b"])
        assert len(pull_plan.files_to_copy) == 2
