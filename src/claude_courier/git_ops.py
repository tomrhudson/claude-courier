"""Git operations for hub repository management."""

from __future__ import annotations

import subprocess
from pathlib import Path


class GitError(Exception):
    pass


def _run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitError(
            f"git command failed: {' '.join(args)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def clone_hub(url: str, local_path: Path) -> None:
    """Clone the hub repository."""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", url, str(local_path)], cwd=local_path.parent)


def pull(repo_path: Path) -> str:
    """Pull latest changes with rebase. Returns output message."""
    result = _run(["git", "pull", "--rebase"], cwd=repo_path)
    return result.stdout.strip()


def stage_files(repo_path: Path, files: list[str]) -> None:
    """Stage specific files for commit."""
    if not files:
        return
    # git add in batches to avoid arg length limits
    batch_size = 100
    for i in range(0, len(files), batch_size):
        batch = files[i : i + batch_size]
        _run(["git", "add", "--"] + batch, cwd=repo_path)


def commit(repo_path: Path, message: str) -> bool:
    """Commit staged changes. Returns True if a commit was made, False if nothing to commit."""
    # Check if there are staged changes
    result = _run(["git", "diff", "--cached", "--quiet"], cwd=repo_path, check=False)
    if result.returncode == 0:
        return False  # Nothing staged

    _run(["git", "commit", "-m", message], cwd=repo_path)
    return True


def push(repo_path: Path) -> str:
    """Push to remote. Returns output."""
    result = _run(["git", "push"], cwd=repo_path)
    return result.stdout.strip()


def commit_and_push(repo_path: Path, message: str, files: list[str]) -> bool:
    """Stage files, commit, and push. Returns True if changes were pushed."""
    stage_files(repo_path, files)
    if commit(repo_path, message):
        push(repo_path)
        return True
    return False


def is_clean(repo_path: Path) -> bool:
    """Check if repo has no uncommitted changes."""
    result = _run(["git", "status", "--porcelain"], cwd=repo_path)
    return result.stdout.strip() == ""


def is_git_repo(path: Path) -> bool:
    """Check if path is a git repository."""
    result = _run(
        ["git", "rev-parse", "--git-dir"],
        cwd=path,
        check=False,
    )
    return result.returncode == 0
