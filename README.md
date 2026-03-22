# claude-courier

**Cross-device Claude Code session history sync using a git repository as the central hub.**

`[EXPERIMENTAL]`

## How It Works

Claude Code stores sessions as JSONL files in `~/.claude/projects/`. claude-courier copies these files to a shared git repo (e.g., on a NAS) and pulls sessions from other machines. Session UUIDs are globally unique, so conflicts are rare. When the same file exists on both sides, the longer file wins (sessions are append-only).

## Prerequisites

- Python 3.9+
- Git
- A shared git repo accessible from all machines (e.g., bare repo on a NAS via SSH)
- `click` and `pyyaml` Python packages (installed automatically)

## How to Run

### Install

```bash
pip install -e .
```

### Initialize

Create a bare git repo on your NAS:

```bash
ssh nas "git init --bare /share/claude-courier-hub.git"
```

On each machine:

```bash
claude-courier init user@nas:/share/claude-courier-hub.git --name mac-mini
```

### Configure

Edit `config.yaml` in the hub repo to map your machines and projects:

```yaml
machines:
  mac-mini:
    claude_home: /Users/youruser/.claude
  macbook-air:
    claude_home: /Users/youruser/.claude
  dell-laptop:
    claude_home: C:\Users\youruser\.claude

projects:
  my-project:
    mac-mini: /Users/youruser/repos/my-project
    macbook-air: /Users/youruser/repos/my-project
    dell-laptop: C:\Users\youruser\repos\my-project
```

Commit and push the config, then pull on other machines.

## Usage

```bash
# See what's pending
claude-courier status

# Preview what would change
claude-courier diff

# Push local sessions to hub
claude-courier push

# Pull remote sessions to local
claude-courier pull

# Full sync (push + pull)
claude-courier sync

# Dry run
claude-courier sync --dry-run
```

### Daemon Mode

Auto-sync every 15 minutes:

```bash
claude-courier daemon-install
claude-courier daemon-status
claude-courier daemon-uninstall
```

Uses `launchd` on macOS and Task Scheduler on Windows.

## Folder Structure

```
claude-courier/
├── src/claude_courier/       # Package source
│   ├── cli.py                # Click CLI (entry point)
│   ├── config.py             # Config loading and machine identity
│   ├── sync.py               # Core push/pull/status/diff logic
│   ├── path_mapper.py        # Local path <-> canonical project mapping
│   ├── git_ops.py            # Git operations (commit, push, pull)
│   ├── history.py            # History file merge/dedup
│   └── daemon.py             # launchd/Task Scheduler management
├── tests/                    # pytest test suite (39 tests)
├── docs/superpowers/specs/   # Design spec
├── config.example.yaml       # Example hub configuration
├── pyproject.toml             # Package metadata
└── README.md
```

## What Gets Synced

- Session JSONL files (`{sessionId}.jsonl`)
- Subagent sessions (`subagents/`)
- Tool results (`tool-results/`)
- Session metadata (`.meta.json`)
- Command history (`history.jsonl`) — per-machine, merged on pull

Only configured projects are synced. Unrecognized project directories are silently skipped.

## Known Issues / Limitations

- **Claude Desktop not supported yet** — Desktop uses IndexedDB/LevelDB, not SQLite. Full-snapshot sync planned for v2.
- **System Python 3.9 on macOS** — editable installs may require `setup.py`/`setup.cfg` alongside `pyproject.toml` on older pip versions.
- **No `--project` filter** — currently syncs all configured projects; per-project filtering is a CLI option but not yet wired through to sync logic.
- **Path encoding is lossy** — hyphens in path components are indistinguishable from path separators. Mitigated by forward-lookup matching against config.

## Last Updated

2026-03-22 — v0.1.0
