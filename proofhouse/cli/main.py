# proofhouse.cli.main — command-line interface
"""
CLI for proofhouse.

Phase 0 Build Plan §7:
    proofhouse init       — create directories, genesis records, git-init
    proofhouse blocks     — manage block registry (add|list|complete|promote)
    proofhouse explore    — append ledger row (<5s, only --universe required)
    proofhouse register   — validate, hash, chain, append (refuse on failure)
    proofhouse status     — trial counts, block consumption, pending fields

"explore being fast is the single most load-bearing UX property in the
 project — the parent plan identifies 'you become the leak' as the
 likeliest killer, and cheapness is the entire mitigation."
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

import click
import yaml

from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.schema.validate import ValidationError


def _get_root():
    """Find the proofhouse project root (where .proofhouse/ lives)."""
    cwd = os.getcwd()
    path = cwd
    while True:
        if os.path.isdir(os.path.join(path, ".proofhouse")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return cwd  # Not found, use cwd
        path = parent


def _ensure_dirs(root):
    """Create the standard directory structure."""
    dirs = [
        os.path.join(root, ".proofhouse"),
        os.path.join(root, ".proofhouse", "registry"),
        os.path.join(root, ".proofhouse", "ledger"),
        os.path.join(root, ".proofhouse", "manifests"),
        os.path.join(root, ".proofhouse", "blocks"),
    ]
    for d in dirs:
        os.makedirs(d, exist_ok=True)
    return dirs


def _get_block_registry(root):
    """Get a path-backed BlockRegistry for the project."""
    blocks_file = os.path.join(root, ".proofhouse", "blocks", "blocks.yaml")
    return BlockRegistry(path=blocks_file)


@click.group()
def cli():
    """proofhouse — personal research harness."""
    pass


@cli.command()
def init():
    """Initialize a proofhouse project in the current directory.

    Creates registry/, ledger/, manifests/, blocks/ directories.
    Initializes git if not already a git repo.
    """
    root = os.getcwd()
    _ensure_dirs(root)

    # Write genesis ledger
    ledger_path = os.path.join(root, ".proofhouse", "ledger", "ledger.jsonl")
    if not os.path.exists(ledger_path):
        open(ledger_path, "w").close()

    # Write empty blocks registry
    blocks_file = os.path.join(root, ".proofhouse", "blocks", "blocks.yaml")
    if not os.path.exists(blocks_file):
        with open(blocks_file, "w") as f:
            yaml.dump({"schema_version": 1, "blocks": []}, f,
                      default_flow_style=False)

    # Git init if absent
    if not os.path.exists(os.path.join(root, ".git")):
        try:
            subprocess.run(["git", "init"], cwd=root, capture_output=True)
            click.echo("Initialized git repository.")
        except FileNotFoundError:
            click.echo("git not found — skipping git init.", err=True)

    click.echo("Initialized proofhouse in {}".format(root))
    click.echo("  .proofhouse/registry/")
    click.echo("  .proofhouse/ledger/")
    click.echo("  .proofhouse/manifests/")
    click.echo("  .proofhouse/blocks/")


@cli.group()
def blocks():
    """Manage the block registry."""
    pass


@blocks.command("add")
@click.argument("block_file")
def blocks_add(block_file):
    """Add a block from a YAML file."""
    root = _get_root()
    block_reg = _get_block_registry(root)

    with open(block_file) as f:
        block = yaml.safe_load(f)

    try:
        block_reg.add_block(block)
        click.echo("Added block: {}".format(block["id"]))
    except (ValueError, KeyError) as e:
        click.echo("Error: {}".format(e), err=True)
        sys.exit(1)


@blocks.command("list")
def blocks_list():
    """List all blocks."""
    root = _get_root()
    block_reg = _get_block_registry(root)
    all_blocks = block_reg.list_blocks()

    if not all_blocks:
        click.echo("No blocks registered.")
        return

    for b in all_blocks:
        consumed = b.get("consumed_by", [])
        seams = b.get("seams", [])
        line = "{id}  {segment}  {start} → {end}  complete={complete}".format(
            id=b["id"],
            segment=b["segment"],
            start=b["start"][:10],
            end=b["end"][:10],
            complete=b.get("data_complete", False),
        )
        if consumed:
            line += "  consumed_by={}".format(
                [c["entry_id"] for c in consumed]
            )
        if seams:
            line += "  seams={}".format(seams)
        click.echo(line)


@blocks.command("complete")
@click.argument("block_id")
def blocks_complete(block_id):
    """Record the data-completion event for a block (A6)."""
    root = _get_root()
    block_reg = _get_block_registry(root)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        block_reg.complete_block(block_id, ts)
        click.echo("Completed block {} at {}".format(block_id, ts))
    except (ValueError, KeyError) as e:
        click.echo("Error: {}".format(e), err=True)
        sys.exit(1)


@blocks.command("promote")
@click.argument("block_id")
@click.argument("segment")
def blocks_promote(block_id, segment):
    """Promote a block to a higher segment (e.g., R→V)."""
    root = _get_root()
    block_reg = _get_block_registry(root)

    try:
        block_reg.promote_block(block_id, segment)
        click.echo("Promoted block {} to segment {}".format(block_id, segment))
    except (ValueError, KeyError) as e:
        click.echo("Error: {}".format(e), err=True)
        sys.exit(1)


@cli.command()
@click.option("--universe", "-u", required=True, help="Universe identifier.")
@click.option("--blocks-touched", "-b", multiple=True, help="Block IDs touched.")
@click.option("--stats-scored", "-s", multiple=True, help="Statistics scored.")
@click.option("--kind", "-k", default="explore",
              type=click.Choice(["explore", "eval", "gate", "maintenance"]),
              help="Run kind.")
@click.option("--outcome", "-o", default=None, help="Outcome description.")
def explore(universe, blocks_touched, stats_scored, kind, outcome):
    """Log an exploration run to the ledger.

    Under 5 seconds. Only --universe is required. Auto-captures code
    context from the current directory.
    """
    root = _get_root()
    _ensure_dirs(root)

    # Auto-capture code hash from working directory
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root, capture_output=True, text=True, timeout=5
        )
        code_hash = "git:" + result.stdout.strip() if result.returncode == 0 else "unknown"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        code_hash = "unknown"

    # Data hash: placeholder for now — real implementation hashes the data dir
    data_hash = "manual"

    ledger_path = os.path.join(root, ".proofhouse", "ledger", "ledger.jsonl")
    ledger = Ledger(ledger_path)

    ledger.append_row(
        code_hash=code_hash,
        data_hash=data_hash,
        universe=universe,
        kind=kind,
        blocks_touched=list(blocks_touched),
        stats_scored=list(stats_scored),
        outcome=outcome,
    )

    count = ledger.count_trials(universe)
    click.echo("Logged {} run on universe '{}'. Trial count: {}".format(
        kind, universe, count
    ))


@cli.command()
@click.argument("entry_file")
def register(entry_file):
    """Register a hypothesis from a YAML file.

    Runs V1–V14 + E1–E6 validation. Refuses loudly on any failure.
    On success: hashes, chains, persists to the registry.
    """
    root = _get_root()
    _ensure_dirs(root)

    with open(entry_file) as f:
        entry = yaml.safe_load(f)

    block_reg = _get_block_registry(root)
    reg_dir = os.path.join(root, ".proofhouse", "registry")
    registry = Registry(reg_dir, block_registry=block_reg)

    try:
        result = registry.register(entry)
        click.echo("Registered: {}".format(result["id"]))
        click.echo("  hash: {}".format(result["hash"]))
        if result.get("prev"):
            click.echo("  prev: {}".format(result["prev"]))
        click.echo("  status: {}".format(result["status"]))
    except ValidationError as e:
        click.echo("Registration REFUSED:", err=True)
        for error in e.errors:
            click.echo("  ✗ {}".format(error), err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo("Error: {}".format(e), err=True)
        sys.exit(1)


@cli.command()
def status():
    """Show trial counts, block consumption, and pending fields."""
    root = _get_root()

    # Ledger status
    ledger_path = os.path.join(root, ".proofhouse", "ledger", "ledger.jsonl")
    if os.path.exists(ledger_path):
        ledger = Ledger(ledger_path)
        rows = ledger.all_rows()

        # Collect universes
        universes = set()
        for row in rows:
            u = row.get("universe")
            if u:
                universes.add(u)

        click.echo("=== Ledger ===")
        click.echo("Total rows: {}".format(len(rows)))
        for u in sorted(universes):
            count = ledger.count_trials(u)
            click.echo("  {}: {} trials".format(u, count))
    else:
        click.echo("No ledger found. Run 'proofhouse init' first.")

    # Block status
    block_reg = _get_block_registry(root)
    all_blocks = block_reg.list_blocks()
    if all_blocks:
        click.echo("\n=== Blocks ===")
        for b in all_blocks:
            consumed = b.get("consumed_by", [])
            status_str = "available" if b.get("data_complete") else "INCOMPLETE"
            click.echo("  {}: segment={} status={} consumed_by={}".format(
                b["id"], b["segment"], status_str,
                [c["entry_id"] for c in consumed] if consumed else "none"
            ))

    # Registry status
    reg_dir = os.path.join(root, ".proofhouse", "registry")
    if os.path.exists(reg_dir):
        registry = Registry(reg_dir)
        entries = registry.list_entries()
        if entries:
            click.echo("\n=== Registry ===")
            click.echo("Entries: {}".format(len(entries)))
            for entry in entries:
                pending = []
                pc = entry.get("power_check", {})
                val = pc.get("value", {})
                if isinstance(val, dict) and val.get("pending"):
                    pending.append("power_check")

                status_str = entry.get("status", "unknown")
                line = "  {}: status={} mechanism={}".format(
                    entry["id"], status_str,
                    entry.get("mechanism", {}).get("class", "?")
                )
                if pending:
                    line += " PENDING={}".format(pending)
                click.echo(line)

            # Chain verification
            ok, errors = registry.verify()
            if ok:
                click.echo("  Chain: ✓ verified")
            else:
                click.echo("  Chain: ✗ {} error(s)".format(len(errors)))
                for e in errors:
                    click.echo("    {}".format(e["message"]))


if __name__ == "__main__":
    cli()
