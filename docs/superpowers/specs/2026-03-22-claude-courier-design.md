# claude-courier: Cross-Device Claude Code Session Sync

## Problem

Claude Code stores session history as JSONL files under `~/.claude/projects/`. Working across multiple machines (Mac Mini, MacBook Air, Dell Latitude/Windows 11) means session history is siloed per device. There is no built-in mechanism to access sessions from one machine on another.

## Goal

A Python CLI tool that syncs Claude Code session history across machines using a git repository on a UNAS Pro NAS as the central hub.

## Scope

### MVP (v1)
- Claude Code session sync (JSONL files in `~/.claude/projects/`)
- CLI commands: `push`, `pull`, `sync`, `status`, `diff`, `init`
- Daemon mode via `launchd` (macOS) and Task Scheduler (Windows)
- Git-based transport via NAS (SSH access)
- Manual path mapping config for cross-machine project identity

### Future (v2)
- Claude Desktop full-snapshot sync (IndexedDB directory copy while app is closed)

## Architecture

### Storage Format (Source)

Claude Code stores sessions at:
- **macOS**: `~/.claude/projects/{path-encoded-project-dir}/{sessionId}.jsonl`
- **Windows**: `C:\Users\{user}\.claude\projects\{path-encoded-project-dir}\{sessionId}.jsonl`

Key properties:
- Directory names encode the full filesystem path with hyphens (e.g., `-Users-romans3_14-Desktop-CoWork-REPOS-claude-courier`)
- Session files have UUID filenames (e.g., `412c2f22-3ac2-4c4c-a1d6-5cccccd05042.jsonl`)
- Sessions are append-only JSONL
- Subagent sessions live in `{sessionId}/subagents/agent-{name}.jsonl`
- Global history at `~/.claude/history.jsonl` (one entry per user input across all sessions)

### Hub Repository Structure (on NAS)

A git repo hosted on the NAS, cloned on each machine:

```
claude-courier-hub/
├── config.yaml              # machine definitions + path mappings
├── projects/
│   ├── claude-courier/      # canonical project name
│   │   ├── 412c2f22-....jsonl
│   │   └── subagents/
│   │       └── agent-explore.jsonl
│   ├── unifi/
│   │   └── ...
│   └── mission-control/
│       └── ...
└── history/
    ├── mac-mini.jsonl
    ├── macbook-air.jsonl
    └── dell-latitude.jsonl
```

Sessions are stored under **canonical project names** (not machine-specific encoded paths), solving cross-machine identity.

### Configuration

`config.yaml` in the hub repo root:

```yaml
machines:
  mac-mini:
    claude_home: /Users/romans3_14/.claude
    path_prefix: /Users/romans3_14/Desktop/CoWork/REPOS
  macbook-air:
    claude_home: /Users/romans3_14/.claude
    path_prefix: /Users/romans3_14/repos
  dell-latitude:
    claude_home: C:\Users\tom\.claude
    path_prefix: C:\Users\tom\repos

projects:
  claude-courier:
    mac-mini: /Users/romans3_14/Desktop/CoWork/REPOS/claude-courier
    macbook-air: /Users/romans3_14/repos/claude-courier
    dell-latitude: C:\Users\tom\repos\claude-courier
  unifi:
    mac-mini: /Users/romans3_14/Desktop/CoWork/REPOS/UniFi
    macbook-air: /Users/romans3_14/repos/UniFi
    dell-latitude: C:\Users\tom\repos\UniFi
```

Machine detection: match `socket.gethostname()` against config keys, or allow override via `--machine` flag.

## CLI Commands

### `claude-courier init`
1. Clone the hub repo from NAS (or initialize if first machine)
2. Detect current machine identity
3. Prompt for local paths and write initial config entries
4. Validate git connectivity

### `claude-courier push`
1. Detect machine identity
2. Scan `~/.claude/projects/` for all session JSONL files
3. For each project directory, resolve canonical name via config
4. Copy session files into hub repo `projects/{canonical}/` (skip files that already exist and haven't changed)
5. Copy subagent sessions into `projects/{canonical}/subagents/`
6. Append new entries from `~/.claude/history.jsonl` to `history/{machine}.jsonl` (dedup by timestamp + sessionId)
7. `git add . && git commit && git push`

### `claude-courier pull`
1. `git pull --rebase` from NAS
2. For each canonical project in `projects/`, resolve to local path via config
3. Copy session files that don't exist locally into `~/.claude/projects/{local-encoded-path}/`
4. Copy subagent sessions similarly
5. Merge other machines' `history/{machine}.jsonl` entries into local `~/.claude/history.jsonl` (dedup, sort by timestamp)

### `claude-courier sync`
- Runs `push` then `pull`

### `claude-courier status`
- Shows: sessions pending push (local-only), sessions pending pull (remote-only), last sync time

### `claude-courier diff`
- Detailed preview: lists exact files that would be added/updated in each direction, with sizes and timestamps

### `claude-courier daemon install`
- **macOS**: Write and load a `launchd` plist at `~/Library/LaunchAgents/com.claude-courier.sync.plist` that runs `claude-courier sync` every 15 minutes
- **Windows**: Create a Task Scheduler task via `schtasks` with the same interval

### `claude-courier daemon uninstall`
- Remove the scheduled task for the current OS

### `claude-courier daemon status`
- Show whether daemon is installed, last run time, next scheduled run

## Key Behaviors

- **Never overwrites**: Only copies sessions that don't exist on the target. If a session file exists locally and in the hub, the local version is kept.
- **Append-only safety**: Session JSONL files are append-only. If both machines have the same session (unlikely due to UUIDs), the longer file wins.
- **Skips active sessions**: Detects sessions modified in the last 60 seconds and skips them (likely in-use).
- **Idempotent**: Running sync multiple times produces the same result.
- **Graceful offline**: If NAS is unreachable, reports the error and exits cleanly (no partial state).

## Conflict Resolution

Conflicts are rare because:
1. Session UUIDs are globally unique — two machines won't create the same session file
2. Sessions are append-only — no in-place edits
3. History files are per-machine in the hub

The only potential conflict: if the same session UUID appears on two machines with different content. Strategy: keep the longer file (more data). This is an edge case that shouldn't occur in practice.

For git-level conflicts (e.g., concurrent pushes from two machines): `git pull --rebase` before pushing. If rebase fails (extremely unlikely with unique filenames), abort and retry.

## Project Structure

```
claude-courier/
├── pyproject.toml
├── src/
│   └── claude_courier/
│       ├── __init__.py
│       ├── cli.py            # Click CLI: push/pull/sync/status/diff/init/daemon
│       ├── config.py         # Load/validate config.yaml, machine detection
│       ├── sync.py           # Core sync logic (push, pull, status, diff)
│       ├── git_ops.py        # Git operations (commit, push, pull, clone)
│       ├── path_mapper.py    # Map local encoded paths <-> canonical project names
│       ├── history.py        # History file merge/dedup logic
│       └── daemon.py         # launchd plist / Task Scheduler install/uninstall
├── tests/
│   ├── test_sync.py
│   ├── test_path_mapper.py
│   ├── test_history.py
│   └── test_config.py
├── config.example.yaml
└── README.md
```

## Dependencies

- `click` — CLI framework
- `pyyaml` — config parsing
- `gitpython` — git operations (or shell out to `git` for simplicity)

No other external dependencies. Standard library for everything else (`pathlib`, `json`, `socket`, `shutil`, `subprocess`, `platform`).

## Testing Strategy

- Unit tests for path mapping (local path <-> canonical name conversion)
- Unit tests for history merge/dedup logic
- Integration test with a temp git repo simulating push/pull between two "machines"
- Manual end-to-end test: run on Mac Mini, push, then pull on MacBook Air and verify sessions appear

## Open Questions (Resolved)

- **SQLite vs LevelDB**: Claude Desktop uses IndexedDB/LevelDB, not SQLite. Deferred to v2 as full-snapshot sync.
- **Path mapping**: Manual config chosen over auto-detection or path normalization.
- **Sync approach**: Git-based chosen over raw rsync or SQLite hub.
- **Language**: Python chosen for cross-platform portability.
