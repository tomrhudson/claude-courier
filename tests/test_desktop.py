"""Tests for Claude Desktop snapshot sync."""

import json
import os
import subprocess
import time

import pytest
from pathlib import Path

from claude_courier.config import Config
from claude_courier.desktop import (
    DesktopPullConfirmationRequired,
    DesktopRunningError,
    DesktopSyncFile,
    execute_desktop_pull,
    execute_desktop_push,
    is_desktop_running,
    iter_desktop_files,
    plan_desktop_pull,
    plan_desktop_push,
    read_metadata,
    should_copy_desktop_file,
    write_metadata,
)


# --- Fixtures ---

@pytest.fixture
def desktop_home(tmp_path):
    """Create a fake Claude Desktop data directory."""
    dh = tmp_path / "Claude"
    dh.mkdir()

    # Included files
    (dh / "config.json").write_text('{"theme": "dark"}')
    (dh / "claude_desktop_config.json").write_text('{"prefs": {}}')
    (dh / "bridge-state.json").write_text('{"enabled": true}')

    # Conversions DB (fake)
    (dh / "Conversions").write_bytes(b"SQLite format 3\x00" + b"\x00" * 100)

    # IndexedDB / LevelDB
    idb = dh / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb"
    idb.mkdir(parents=True)
    (idb / "000007.log").write_bytes(b"\x00" * 512)
    (idb / "CURRENT").write_text("MANIFEST-000001\n")
    (idb / "MANIFEST-000001").write_bytes(b"\x00" * 64)

    # Local agent sessions
    session_dir = dh / "local-agent-mode-sessions" / "org" / "user" / "local_abc123"
    claude_dir = session_dir / ".claude"
    claude_dir.mkdir(parents=True)
    (claude_dir / ".claude.json").write_text('{"features": {}}')

    # Session Storage
    ss = dh / "Session Storage"
    ss.mkdir()
    (ss / "000003.log").write_bytes(b"\x00" * 32)

    # Excluded dirs
    cache = dh / "Cache"
    cache.mkdir()
    (cache / "data.bin").write_bytes(b"\x00" * 1024)

    code_cache = dh / "Code Cache"
    code_cache.mkdir()
    (code_cache / "compiled.js").write_bytes(b"\x00" * 512)

    gpu_cache = dh / "GPUCache"
    gpu_cache.mkdir()
    (gpu_cache / "gpu.bin").write_bytes(b"\x00" * 256)

    crashpad = dh / "Crashpad"
    crashpad.mkdir()
    (crashpad / "report.dmp").write_bytes(b"\x00" * 128)

    # Excluded files
    (dh / "Cookies").write_bytes(b"\x00" * 64)
    (dh / "Cookies-journal").write_bytes(b"\x00" * 16)
    (dh / "DIPS").write_bytes(b"\x00" * 64)
    (dh / "Preferences").write_bytes(b"\x00" * 32)
    (dh / ".DS_Store").write_bytes(b"\x00" * 16)
    (dh / "LOCK").write_bytes(b"")

    return dh


@pytest.fixture
def desktop_config(tmp_path, desktop_home):
    """Create a config with desktop_home set."""
    config_file = tmp_path / "hub" / "config.yaml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(f"""
machines:
  test-mac:
    claude_home: {tmp_path / "claude"}
    desktop_home: {desktop_home}
  other-mac:
    claude_home: {tmp_path / "claude-other"}
    desktop_home: {tmp_path / "Claude-other"}

projects:
  test-project:
    test-mac: /Users/test/repos/test-project
""")
    return Config(config_file.parent, machine_override="test-mac")


@pytest.fixture
def desktop_sync_env(tmp_path, desktop_home):
    """Full sync env with git repos for Desktop push/pull integration tests."""
    # Create bare git repo
    bare_repo = tmp_path / "bare-hub.git"
    bare_repo.mkdir()
    subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

    # Clone for machine A
    hub_a = tmp_path / "hub-a"
    subprocess.run(["git", "clone", str(bare_repo), str(hub_a)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=hub_a, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=hub_a, check=True, capture_output=True)

    # Desktop home for machine B (empty initially)
    desktop_b = tmp_path / "Claude-b"
    desktop_b.mkdir()

    config_content = f"""
machines:
  machine-a:
    claude_home: {tmp_path / "claude-a"}
    desktop_home: {desktop_home}
  machine-b:
    claude_home: {tmp_path / "claude-b"}
    desktop_home: {desktop_b}

projects:
  test-project:
    machine-a: /Users/usera/repos/test-project
    machine-b: /Users/userb/repos/test-project
"""
    (hub_a / "config.yaml").write_text(config_content)
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
        "desktop_a": desktop_home,
        "desktop_b": desktop_b,
        "config_a": config_a,
        "config_b": config_b,
    }


# --- Tests: is_desktop_running ---

class TestIsDesktopRunning:
    def test_not_running(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args[0], returncode=1, stdout="", stderr="")
            return result
        monkeypatch.setattr("claude_courier.desktop.subprocess.run", mock_run)
        assert is_desktop_running() is False

    def test_running(self, monkeypatch):
        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(args[0], returncode=0, stdout="12345\n", stderr="")
            return result
        monkeypatch.setattr("claude_courier.desktop.subprocess.run", mock_run)
        assert is_desktop_running() is True

    def test_pgrep_not_found(self, monkeypatch):
        def mock_run(*args, **kwargs):
            raise FileNotFoundError("pgrep not found")
        monkeypatch.setattr("claude_courier.desktop.subprocess.run", mock_run)
        assert is_desktop_running() is False


# --- Tests: iter_desktop_files ---

class TestIterDesktopFiles:
    def test_includes_expected_files(self, desktop_home):
        files = iter_desktop_files(desktop_home)
        rel_paths = {str(f.relative_to(desktop_home)) for f in files}

        # Should include
        assert "config.json" in rel_paths
        assert "claude_desktop_config.json" in rel_paths
        assert "bridge-state.json" in rel_paths
        assert "Conversions" in rel_paths
        assert "IndexedDB/https_claude.ai_0.indexeddb.leveldb/000007.log" in rel_paths
        assert "IndexedDB/https_claude.ai_0.indexeddb.leveldb/CURRENT" in rel_paths
        assert "Session Storage/000003.log" in rel_paths

    def test_excludes_cache_dirs(self, desktop_home):
        files = iter_desktop_files(desktop_home)
        rel_paths = {str(f.relative_to(desktop_home)) for f in files}

        for path in rel_paths:
            assert not path.startswith("Cache/")
            assert not path.startswith("Code Cache/")
            assert not path.startswith("GPUCache/")
            assert not path.startswith("Crashpad/")

    def test_excludes_specific_files(self, desktop_home):
        files = iter_desktop_files(desktop_home)
        names = {f.name for f in files}

        assert "Cookies" not in names
        assert "Cookies-journal" not in names
        assert "DIPS" not in names
        assert "Preferences" not in names
        assert ".DS_Store" not in names
        assert "LOCK" not in names

    def test_nonexistent_dir(self, tmp_path):
        files = iter_desktop_files(tmp_path / "nonexistent")
        assert files == []


# --- Tests: should_copy_desktop_file ---

class TestShouldCopyDesktopFile:
    def test_new_file(self, tmp_path):
        source = tmp_path / "src" / "file.txt"
        source.parent.mkdir()
        source.write_text("hello")
        dest = tmp_path / "dst" / "file.txt"
        assert should_copy_desktop_file(source, dest) == "new"

    def test_newer_file(self, tmp_path):
        source = tmp_path / "src" / "file.txt"
        source.parent.mkdir()
        source.write_text("newer content")

        dest = tmp_path / "dst" / "file.txt"
        dest.parent.mkdir()
        dest.write_text("older content")

        # Make source newer
        old_time = time.time() - 120
        os.utime(dest, (old_time, old_time))

        assert should_copy_desktop_file(source, dest) == "newer"

    def test_changed_size(self, tmp_path):
        source = tmp_path / "src" / "file.txt"
        source.parent.mkdir()
        source.write_text("short")

        dest = tmp_path / "dst" / "file.txt"
        dest.parent.mkdir()
        dest.write_text("much longer content here")

        # Set same mtime
        t = time.time() - 60
        os.utime(source, (t, t))
        os.utime(dest, (t, t))

        assert should_copy_desktop_file(source, dest) == "changed"

    def test_identical(self, tmp_path):
        source = tmp_path / "src" / "file.txt"
        source.parent.mkdir()
        source.write_text("same")

        dest = tmp_path / "dst" / "file.txt"
        dest.parent.mkdir()
        dest.write_text("same")

        # Set same mtime
        t = time.time() - 60
        os.utime(source, (t, t))
        os.utime(dest, (t, t))

        assert should_copy_desktop_file(source, dest) is None


# --- Tests: metadata ---

class TestMetadata:
    def test_write_and_read(self, tmp_path):
        files = [
            DesktopSyncFile("config.json", Path("/a"), Path("/b"), "new", 100),
            DesktopSyncFile("Conversions", Path("/c"), Path("/d"), "newer", 200),
        ]
        write_metadata(tmp_path, "test-mac", files)

        meta = read_metadata(tmp_path)
        assert meta is not None
        assert meta["machine"] == "test-mac"
        assert meta["file_count"] == 2
        assert meta["total_size_bytes"] == 300
        assert "synced_at" in meta

    def test_read_missing(self, tmp_path):
        assert read_metadata(tmp_path) is None


# --- Tests: plan_desktop_push ---

class TestPlanDesktopPush:
    def test_detects_new_files(self, desktop_config, desktop_home, tmp_path):
        hub_path = tmp_path / "hub"
        plan = plan_desktop_push(desktop_config, hub_path)
        assert len(plan.files_to_copy) > 0
        assert all(sf.reason == "new" for sf in plan.files_to_copy)
        assert plan.total_size > 0

    def test_detects_newer_files(self, desktop_config, desktop_home, tmp_path):
        hub_path = tmp_path / "hub"
        hub_desktop = hub_path / "desktop" / "test-mac"

        # Copy files to hub first (simulate prior sync)
        for local_file in iter_desktop_files(desktop_home):
            rel = local_file.relative_to(desktop_home)
            dest = hub_desktop / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(local_file, dest)

        # Modify a local file to be newer
        config_file = desktop_home / "config.json"
        time.sleep(0.05)  # ensure mtime difference
        config_file.write_text('{"theme": "light"}')

        plan = plan_desktop_push(desktop_config, hub_path)
        newer_files = [sf for sf in plan.files_to_copy if sf.reason == "newer"]
        assert len(newer_files) >= 1
        assert any("config.json" in sf.relative_path for sf in newer_files)

    def test_no_desktop_home(self, tmp_path):
        """Config without desktop_home returns empty plan."""
        config_file = tmp_path / "hub" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("""
machines:
  test-mac:
    claude_home: /tmp/claude

projects:
  p: {}
""")
        cfg = Config(config_file.parent, machine_override="test-mac")
        plan = plan_desktop_push(cfg, tmp_path / "hub")
        assert len(plan.files_to_copy) == 0


# --- Tests: plan_desktop_pull ---

class TestPlanDesktopPull:
    def test_pull_from_specific_machine(self, tmp_path):
        """Pull from an explicit machine snapshot."""
        hub_path = tmp_path / "hub"
        desktop_home = tmp_path / "local-desktop"
        desktop_home.mkdir()

        # Create a snapshot in hub for "other-mac"
        other_dir = hub_path / "desktop" / "other-mac"
        other_dir.mkdir(parents=True)
        (other_dir / "config.json").write_text('{"from": "other"}')
        write_metadata(other_dir, "other-mac", [
            DesktopSyncFile("config.json", Path("/a"), Path("/b"), "new", 20),
        ])

        config_file = hub_path / "config.yaml"
        config_file.write_text(f"""
machines:
  test-mac:
    claude_home: {tmp_path / "claude"}
    desktop_home: {desktop_home}
  other-mac:
    claude_home: {tmp_path / "other-claude"}
    desktop_home: {tmp_path / "other-desktop"}

projects:
  p: {{}}
""")
        cfg = Config(hub_path, machine_override="test-mac")
        plan = plan_desktop_pull(cfg, hub_path, from_machine="other-mac")

        assert plan.source_machine == "other-mac"
        assert len(plan.files_to_copy) == 1
        assert plan.files_to_copy[0].relative_path == "config.json"

    def test_pull_auto_selects_most_recent(self, tmp_path):
        """Without from_machine, picks most recently synced snapshot."""
        hub_path = tmp_path / "hub"
        desktop_home = tmp_path / "local-desktop"
        desktop_home.mkdir()

        # Create two snapshots with different timestamps
        old_dir = hub_path / "desktop" / "old-mac"
        old_dir.mkdir(parents=True)
        (old_dir / "data.txt").write_text("old")
        meta_old = old_dir / "metadata.json"
        meta_old.write_text(json.dumps({"machine": "old-mac", "synced_at": "2026-03-20T10:00:00"}))

        new_dir = hub_path / "desktop" / "new-mac"
        new_dir.mkdir(parents=True)
        (new_dir / "data.txt").write_text("new")
        meta_new = new_dir / "metadata.json"
        meta_new.write_text(json.dumps({"machine": "new-mac", "synced_at": "2026-03-23T10:00:00"}))

        config_file = hub_path / "config.yaml"
        config_file.write_text(f"""
machines:
  test-mac:
    claude_home: {tmp_path / "claude"}
    desktop_home: {desktop_home}
  old-mac:
    claude_home: {tmp_path / "old-claude"}
  new-mac:
    claude_home: {tmp_path / "new-claude"}

projects:
  p: {{}}
""")
        cfg = Config(hub_path, machine_override="test-mac")
        plan = plan_desktop_pull(cfg, hub_path)

        assert plan.source_machine == "new-mac"

    def test_pull_skips_own_machine(self, tmp_path):
        """Auto-select should not pull from own machine's snapshot."""
        hub_path = tmp_path / "hub"
        desktop_home = tmp_path / "local-desktop"
        desktop_home.mkdir()

        own_dir = hub_path / "desktop" / "test-mac"
        own_dir.mkdir(parents=True)
        (own_dir / "data.txt").write_text("own data")
        meta = own_dir / "metadata.json"
        meta.write_text(json.dumps({"machine": "test-mac", "synced_at": "2026-03-23T12:00:00"}))

        config_file = hub_path / "config.yaml"
        config_file.write_text(f"""
machines:
  test-mac:
    claude_home: {tmp_path / "claude"}
    desktop_home: {desktop_home}

projects:
  p: {{}}
""")
        cfg = Config(hub_path, machine_override="test-mac")
        plan = plan_desktop_pull(cfg, hub_path)

        assert plan.source_machine is None
        assert len(plan.files_to_copy) == 0


# --- Tests: execute integration ---

class TestExecuteDesktopPushPull:
    def test_push_then_pull(self, desktop_sync_env, monkeypatch):
        """Push from machine A, pull on machine B."""
        # Mock is_desktop_running to return False
        monkeypatch.setattr("claude_courier.desktop.is_desktop_running", lambda: False)

        env = desktop_sync_env

        # Push from A
        push_plan = execute_desktop_push(env["config_a"], env["hub_a"])
        assert len(push_plan.files_to_copy) > 0

        # Verify metadata was written
        meta = read_metadata(env["hub_a"] / "desktop" / "machine-a")
        assert meta is not None
        assert meta["machine"] == "machine-a"

        # Pull on B
        pull_plan = execute_desktop_pull(
            env["config_b"], env["hub_b"],
            from_machine="machine-a",
            force=True,
        )
        assert len(pull_plan.files_to_copy) > 0
        assert pull_plan.source_machine == "machine-a"

        # Verify files landed in B's desktop dir
        desktop_b = env["desktop_b"]
        assert (desktop_b / "config.json").exists()
        assert (desktop_b / "Conversions").exists()
        assert (desktop_b / "IndexedDB" / "https_claude.ai_0.indexeddb.leveldb" / "000007.log").exists()

    def test_push_refuses_when_running(self, desktop_sync_env, monkeypatch):
        """Push should fail if Claude Desktop is running."""
        monkeypatch.setattr("claude_courier.desktop.is_desktop_running", lambda: True)

        with pytest.raises(DesktopRunningError):
            execute_desktop_push(desktop_sync_env["config_a"], desktop_sync_env["hub_a"])

    def test_pull_refuses_when_running(self, desktop_sync_env, monkeypatch):
        """Pull should fail if Claude Desktop is running."""
        monkeypatch.setattr("claude_courier.desktop.is_desktop_running", lambda: True)

        with pytest.raises(DesktopRunningError):
            execute_desktop_pull(desktop_sync_env["config_b"], desktop_sync_env["hub_b"])

    def test_pull_requires_confirmation(self, desktop_sync_env, monkeypatch):
        """Pull should require confirmation when local data exists."""
        monkeypatch.setattr("claude_courier.desktop.is_desktop_running", lambda: False)

        env = desktop_sync_env

        # Push from A first
        execute_desktop_push(env["config_a"], env["hub_a"])

        # Put something in B's desktop dir so it's non-empty
        (env["desktop_b"] / "existing.txt").write_text("existing data")

        # Pull without force should raise
        with pytest.raises(DesktopPullConfirmationRequired):
            execute_desktop_pull(
                env["config_b"], env["hub_b"],
                from_machine="machine-a",
            )

        # Pull with force should succeed
        plan = execute_desktop_pull(
            env["config_b"], env["hub_b"],
            from_machine="machine-a",
            force=True,
        )
        assert len(plan.files_to_copy) > 0

    def test_push_nothing_when_no_desktop_home(self, tmp_path, monkeypatch):
        """Push returns empty plan when desktop_home not configured."""
        monkeypatch.setattr("claude_courier.desktop.is_desktop_running", lambda: False)

        bare_repo = tmp_path / "bare.git"
        bare_repo.mkdir()
        subprocess.run(["git", "init", "--bare", str(bare_repo)], check=True, capture_output=True)

        hub = tmp_path / "hub"
        subprocess.run(["git", "clone", str(bare_repo), str(hub)], check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=hub, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=hub, check=True, capture_output=True)

        config_file = hub / "config.yaml"
        config_file.write_text("""
machines:
  test-mac:
    claude_home: /tmp/claude

projects:
  p: {}
""")
        subprocess.run(["git", "add", "."], cwd=hub, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=hub, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=hub, check=True, capture_output=True)

        cfg = Config(hub, machine_override="test-mac")
        plan = execute_desktop_push(cfg, hub)
        assert len(plan.files_to_copy) == 0
