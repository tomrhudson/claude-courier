"""Map local Claude Code project directories to canonical project names."""

from __future__ import annotations

import re
from pathlib import Path

from claude_courier.config import Config

# Pattern for worktree session directories
# e.g., -Users-romans3_14-...-claude-courier--claude-worktrees-awesome-mayer
WORKTREE_PATTERN = re.compile(r"(.+?)--claude-worktrees-.+")


def encode_path(path: str) -> str:
    """Encode a filesystem path to Claude Code's directory name format.

    Replaces path separators with hyphens and strips leading separator.
    e.g., /Users/tom/repos/my-project -> -Users-tom-repos-my-project
    """
    # Normalize to forward slashes
    normalized = path.replace("\\", "/")
    # Replace slashes with hyphens
    encoded = normalized.replace("/", "-")
    return encoded


def decode_path_candidates(encoded_dir: str) -> list[str]:
    """Given an encoded dir name, return possible original paths.

    Since encoding is lossy (both / and literal - become -),
    we don't try to reverse it. Instead, callers should use
    match_local_dir_to_canonical() which does forward lookup.
    """
    # Not used in practice — forward lookup is the correct approach
    return []


def match_local_dir_to_canonical(
    local_dir_name: str,
    config: Config,
) -> str | None:
    """Match a local project directory name to its canonical project name.

    Uses forward lookup: encode each configured local path and compare
    to the actual directory name.
    """
    # Check for worktree — extract parent project dir
    worktree_match = WORKTREE_PATTERN.match(local_dir_name)
    if worktree_match:
        parent_dir = worktree_match.group(1)
        # Try to match the parent to a canonical name
        return _match_encoded_dir(parent_dir, config)

    return _match_encoded_dir(local_dir_name, config)


def _match_encoded_dir(encoded_dir: str, config: Config) -> str | None:
    """Match an encoded directory name against configured project paths."""
    projects = config.get_projects()
    for canonical, local_path in projects.items():
        expected_encoded = encode_path(local_path)
        if encoded_dir == expected_encoded:
            return canonical
    return None


def build_project_map(config: Config) -> dict[str, list[str]]:
    """Scan local projects dir and build {canonical_name: [local_dir_names]} mapping.

    Returns a mapping from canonical project names to lists of local directory
    names (including worktree dirs) that belong to that project.
    """
    projects_dir = config.projects_dir
    if not projects_dir.exists():
        return {}

    result: dict[str, list[str]] = {}

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip hidden directories
        if entry.name.startswith("."):
            continue

        canonical = match_local_dir_to_canonical(entry.name, config)
        if canonical is not None:
            result.setdefault(canonical, []).append(entry.name)

    return result


def local_encoded_dir_for_project(config: Config, canonical: str) -> str | None:
    """Get the primary (non-worktree) encoded directory name for a project."""
    local_path = config.local_path_for_canonical(canonical)
    if local_path is None:
        return None
    return encode_path(local_path)
