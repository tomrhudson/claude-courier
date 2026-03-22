# SOP.md — claude-courier

## How to Make Changes

1. Work on `main` branch (single developer, no PR workflow needed for now)
2. Keep commits atomic — one logical change per commit
3. Commit messages in imperative mood: "add feature X", "fix bug in Y"
4. Update `CHANGELOG.md` with every significant change

## How to Test

Run the full test suite before committing:

```bash
PYTHONPATH=src python3 -m pytest tests/ -v
```

- **Unit tests**: `test_config.py`, `test_path_mapper.py`, `test_history.py` — fast, no git required
- **Integration tests**: `test_sync.py` — creates temp git repos, tests full push/pull flow

All 39 tests must pass before merging or deploying.

## How to Deploy / Release

This is a local tool installed via `pip install -e .` on each machine. No CI/CD pipeline.

### To deploy a new version:

1. Update version in `src/claude_courier/__init__.py` and `pyproject.toml`
2. Add CHANGELOG entry
3. Commit and push to origin
4. On each machine: `git pull && pip install -e .`

### First-time setup on a new machine:

```bash
git clone <repo-url>
cd claude-courier
pip install -e .
claude-courier init user@nas:/share/claude-courier-hub.git --name <machine-name>
```

## How to Roll Back

### If a sync corrupts sessions:

1. The hub is a git repo — `git log` to find the last good commit
2. `git revert <bad-commit>` or `git reset --hard <good-commit>` on the hub
3. On affected machines: `claude-courier pull` to restore

### If the daemon causes issues:

```bash
claude-courier daemon-uninstall
```

### If local sessions are lost:

Sessions are never deleted by claude-courier. The hub repo contains a full history of all synced sessions via git.

## Escalation Path

Single developer (Tom Hudson). No external dependencies or services beyond the NAS.

If the NAS is unreachable, claude-courier reports an error and exits cleanly — no partial state is created.

## Maintenance Cadence

- Review after each significant Claude Code update (storage format may change)
- Update `config.yaml` when adding new machines or projects
- Prune hub repo git history annually if size becomes a concern (`git gc` or reinitialize)

## Input/Output Contracts

**Input**: JSONL session files from `~/.claude/projects/`, `config.yaml` from hub repo
**Output**: Session files copied to/from hub repo, git commits with sync metadata

## Error Handling

- NAS unreachable: error message, clean exit (no partial state)
- No changes to sync: silent success (idempotent)
- Active session detected: skipped with warning (files modified < 60s ago)
- Config missing: error with guidance to run `claude-courier init`
- Git conflicts: `pull --rebase` handles most cases; extremely unlikely with UUID filenames

## Dependencies

| Dependency | Purpose | Version |
|---|---|---|
| Python | Runtime | >= 3.9 |
| click | CLI framework | >= 8.0 |
| pyyaml | Config parsing | >= 6.0 |
| git | Version control / transport | any |
| pytest | Testing | >= 7.0 (dev only) |
