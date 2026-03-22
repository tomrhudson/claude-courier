# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com).

## [0.1.0] — 2026-03-22

### Added

- Initial implementation of claude-courier CLI
- `config.py` — YAML config loading, machine identity via `~/.claude-courier/machine-id`
- `path_mapper.py` — forward-lookup path encoding, worktree session grouping, unmapped dir skipping
- `git_ops.py` — clone, pull, explicit-file staging, commit, push via subprocess
- `history.py` — per-machine history push to hub, cross-machine merge with dedup and project path rewriting
- `sync.py` — push/pull/sync/status/diff with "longer file wins" conflict resolution and active session skipping
- `cli.py` — Click CLI with commands: init, push, pull, sync, status, diff, daemon-install/uninstall/status
- `daemon.py` — launchd plist generation (macOS) and Task Scheduler support (Windows)
- 39 pytest tests covering config, path mapping, history merge, and full push/pull integration
- Design spec at `docs/superpowers/specs/2026-03-22-claude-courier-design.md`
- Repo hygiene files: README.md, CLAUDE.md, CHANGELOG.md, SOP.md, CONTEXT.md
