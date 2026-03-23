"""Click CLI for claude-courier."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import click

from claude_courier.config import (
    COURIER_HOME,
    Config,
    ConfigError,
    get_hub_path,
    set_hub_path,
    set_machine_id,
)
from claude_courier.desktop import (
    DesktopPullConfirmationRequired,
    DesktopRunningError,
    DesktopSyncPlan,
    execute_desktop_pull,
    execute_desktop_push,
    get_desktop_home,
    plan_desktop_pull,
    plan_desktop_push,
    read_metadata,
    is_desktop_running,
)
from claude_courier.git_ops import GitError, clone_hub, is_git_repo
from claude_courier.sync import SyncPlan, execute_pull, execute_push, execute_sync, plan_pull, plan_push


def _setup_logging(verbose: bool) -> None:
    log_dir = COURIER_HOME
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sync.log"

    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stderr) if verbose else logging.NullHandler(),
        ],
    )


def _load_config(machine: str | None) -> tuple[Config, Path]:
    hub_path = get_hub_path()
    config = Config(hub_path, machine_override=machine)
    return config, hub_path


def _print_plan(plan: SyncPlan, direction: str) -> None:
    if plan.files_to_copy:
        click.echo(f"\n{direction} ({len(plan.files_to_copy)} files):")
        for sf in plan.files_to_copy:
            size_kb = sf.size / 1024
            click.echo(f"  [{sf.reason}] {sf.relative_path} ({size_kb:.1f} KB)")
    else:
        click.echo(f"\n{direction}: nothing to sync")

    if plan.history_changed:
        click.echo(f"  + history updated")

    if plan.skipped_active:
        click.echo(f"\n  Skipped (active sessions): {len(plan.skipped_active)}")
        for s in plan.skipped_active:
            click.echo(f"    {s}")

    if plan.skipped_unmapped:
        click.echo(f"\n  Skipped (unmapped): {len(plan.skipped_unmapped)}")
        for s in plan.skipped_unmapped[:5]:
            click.echo(f"    {s}")
        if len(plan.skipped_unmapped) > 5:
            click.echo(f"    ... and {len(plan.skipped_unmapped) - 5} more")


@click.group()
@click.option("--machine", default=None, help="Override machine identity")
@click.option("--verbose", "-v", is_flag=True, help="Verbose logging")
@click.pass_context
def main(ctx: click.Context, machine: str | None, verbose: bool) -> None:
    """claude-courier: Sync Claude Code sessions across machines."""
    ctx.ensure_object(dict)
    ctx.obj["machine"] = machine
    ctx.obj["verbose"] = verbose
    _setup_logging(verbose)


@main.command()
@click.argument("repo_url")
@click.option("--name", prompt="Machine name", help="Name for this machine (e.g., mac-mini)")
@click.option("--hub-dir", default=None, help="Local directory for hub clone")
def init(repo_url: str, name: str, hub_dir: str | None) -> None:
    """Initialize claude-courier on this machine."""
    if hub_dir:
        local_path = Path(hub_dir)
    else:
        local_path = COURIER_HOME / "hub"

    if local_path.exists() and is_git_repo(local_path):
        click.echo(f"Hub already cloned at {local_path}")
    else:
        click.echo(f"Cloning hub from {repo_url}...")
        try:
            clone_hub(repo_url, local_path)
        except GitError as e:
            click.echo(f"Error cloning: {e}", err=True)
            sys.exit(1)

    set_hub_path(local_path)
    set_machine_id(name)
    click.echo(f"Machine identity set to '{name}'")
    click.echo(f"Hub path: {local_path}")
    click.echo("Ready! Run 'claude-courier status' to see what's pending.")


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be pushed without acting")
@click.option("--project", default=None, help="Only sync a specific project")
@click.pass_context
def push(ctx: click.Context, dry_run: bool, project: str | None) -> None:
    """Push local sessions to hub."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        plan = execute_push(config, hub_path, dry_run=dry_run)
        label = "Would push" if dry_run else "Pushed"
        _print_plan(plan, label)
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would be pulled without acting")
@click.option("--project", default=None, help="Only sync a specific project")
@click.pass_context
def pull(ctx: click.Context, dry_run: bool, project: str | None) -> None:
    """Pull remote sessions to local."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        plan = execute_pull(config, hub_path, dry_run=dry_run)
        label = "Would pull" if dry_run else "Pulled"
        _print_plan(plan, label)
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.option("--dry-run", is_flag=True, help="Show what would change without acting")
@click.pass_context
def sync(ctx: click.Context, dry_run: bool) -> None:
    """Push then pull (full sync)."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        push_plan, pull_plan = execute_sync(config, hub_path, dry_run=dry_run)
        label_push = "Would push" if dry_run else "Pushed"
        label_pull = "Would pull" if dry_run else "Pulled"
        _print_plan(push_plan, label_push)
        _print_plan(pull_plan, label_pull)
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show sessions pending push/pull."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        push_plan = plan_push(config, hub_path)
        pull_plan = plan_pull(config, hub_path)

        _print_plan(push_plan, "Pending push")
        _print_plan(pull_plan, "Pending pull")
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def diff(ctx: click.Context) -> None:
    """Show detailed preview of what sync would add/update."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        push_plan = plan_push(config, hub_path)
        pull_plan = plan_pull(config, hub_path)

        _print_plan(push_plan, "Push diff")
        _print_plan(pull_plan, "Pull diff")
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command()
@click.pass_context
def daemon(ctx: click.Context) -> None:
    """Manage background sync daemon. Use subcommands: install, uninstall, status."""
    click.echo("Use: claude-courier daemon-install, daemon-uninstall, or daemon-status")


@main.command(name="daemon-install")
@click.option("--interval", default=15, help="Sync interval in minutes (default: 15)")
@click.pass_context
def daemon_install(ctx: click.Context, interval: int) -> None:
    """Install background sync daemon."""
    from claude_courier.daemon import install

    try:
        install(interval)
        click.echo(f"Daemon installed (every {interval} minutes)")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="daemon-uninstall")
def daemon_uninstall() -> None:
    """Remove background sync daemon."""
    from claude_courier.daemon import uninstall

    try:
        uninstall()
        click.echo("Daemon uninstalled")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="daemon-status")
def daemon_status() -> None:
    """Show daemon status."""
    from claude_courier.daemon import status as daemon_status_fn

    try:
        info = daemon_status_fn()
        click.echo(info)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


# --- Desktop sync commands ---

def _print_desktop_plan(plan: DesktopSyncPlan, direction: str) -> None:
    if plan.files_to_copy:
        total_kb = plan.total_size / 1024
        click.echo(f"\n{direction} ({len(plan.files_to_copy)} files, {total_kb:.1f} KB):")
        for sf in plan.files_to_copy:
            size_kb = sf.size / 1024
            click.echo(f"  [{sf.reason}] {sf.relative_path} ({size_kb:.1f} KB)")
    else:
        click.echo(f"\n{direction}: nothing to sync")

    if plan.source_machine:
        click.echo(f"  Source: {plan.source_machine}")

    if plan.files_unchanged:
        click.echo(f"  Unchanged: {plan.files_unchanged} files")


@main.command(name="desktop-push")
@click.option("--dry-run", is_flag=True, help="Show what would be pushed without acting")
@click.pass_context
def desktop_push(ctx: click.Context, dry_run: bool) -> None:
    """Push Claude Desktop snapshot to hub."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        plan = execute_desktop_push(config, hub_path, dry_run=dry_run)
        label = "Would push (Desktop)" if dry_run else "Pushed (Desktop)"
        _print_desktop_plan(plan, label)
    except DesktopRunningError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Quit Claude Desktop and try again.", err=True)
        sys.exit(1)
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="desktop-pull")
@click.option("--from-machine", default=None, help="Pull from specific machine snapshot")
@click.option("--dry-run", is_flag=True, help="Show what would be pulled without acting")
@click.option("--force", is_flag=True, help="Overwrite local data without confirmation")
@click.pass_context
def desktop_pull(ctx: click.Context, from_machine: str | None,
                 dry_run: bool, force: bool) -> None:
    """Pull Claude Desktop snapshot from hub."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])
        plan = execute_desktop_pull(
            config, hub_path,
            from_machine=from_machine,
            dry_run=dry_run,
            force=force,
        )
        label = "Would pull (Desktop)" if dry_run else "Pulled (Desktop)"
        _print_desktop_plan(plan, label)
    except DesktopRunningError as e:
        click.echo(f"Error: {e}", err=True)
        click.echo("Quit Claude Desktop and try again.", err=True)
        sys.exit(1)
    except DesktopPullConfirmationRequired:
        if click.confirm("This will overwrite local Desktop data. Continue?"):
            config, hub_path = _load_config(ctx.obj["machine"])
            plan = execute_desktop_pull(
                config, hub_path,
                from_machine=from_machine,
                dry_run=dry_run,
                force=True,
            )
            _print_desktop_plan(plan, "Pulled (Desktop)")
        else:
            click.echo("Aborted.")
    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@main.command(name="desktop-status")
@click.pass_context
def desktop_status(ctx: click.Context) -> None:
    """Show Claude Desktop sync status."""
    try:
        config, hub_path = _load_config(ctx.obj["machine"])

        desktop_home = get_desktop_home(config)
        if desktop_home is None:
            click.echo("Desktop sync not configured (no desktop_home in config)")
            return

        click.echo(f"Desktop home: {desktop_home}")
        click.echo(f"Desktop exists: {desktop_home.exists()}")
        click.echo(f"Desktop running: {is_desktop_running()}")

        # Pending push
        push_plan = plan_desktop_push(config, hub_path)
        _print_desktop_plan(push_plan, "Pending push (Desktop)")

        # Available snapshots in hub
        hub_desktop = hub_path / "desktop"
        if hub_desktop.exists():
            click.echo("\nAvailable snapshots:")
            for machine_dir in sorted(hub_desktop.iterdir()):
                if not machine_dir.is_dir():
                    continue
                meta = read_metadata(machine_dir)
                if meta:
                    click.echo(f"  {machine_dir.name}: synced {meta['synced_at']} "
                               f"({meta['file_count']} files)")
                else:
                    click.echo(f"  {machine_dir.name}: no metadata")
        else:
            click.echo("\nNo Desktop snapshots in hub yet.")

    except (ConfigError, GitError) as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
