# proofhouse.cli.main — command-line interface
"""
CLI for proofhouse.

Phase 0 Build Plan §7:
    proofhouse init       — create directories, genesis records, git-init
    proofhouse blocks     — manage block registry (add|list|complete|promote)
    proofhouse explore    — append ledger row (<5s, only --universe required)
    proofhouse register   — validate, hash, chain, append (refuse on failure)
    proofhouse status     — trial counts, block consumption, pending fields

Phase 2c §2:
    proofhouse fill       — fill a pending field from its pinned rule, once
    proofhouse gate       — render the pre-committed gate; exit 1 on a fail
    proofhouse reject     — record a rejected idea
    proofhouse amend      — file an amendment + its successor entry

Phase 2.5 §5:
    proofhouse verify     — full integrity check; exit 1 on error
    proofhouse report     — findings memo

Later addition:
    proofhouse screen     — screen a batch against its recorded stage-1 runs
                            and record the decision (a SCR-* ledger row)
    proofhouse install-skill — copy the agent skill that ships with the
                            package into a project's skills directory

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
        # A segment that claims the block is untouched, next to a list of
        # things that touched it, should not read as unremarkable. Surfacing
        # this took a hand reconciliation once; it should take one command.
        unexplained = block_reg.unexplained_consumption(b["id"])
        if unexplained:
            line += "  ** {} CLAIMS UNTOUCHED, READ BY {} **".format(
                b["segment"], ", ".join(unexplained))
        elif b.get("consumption_notes"):
            line += "  (consumption explained)"
        if b.get("promotion_acknowledgment"):
            line += "  (promoted over prior consumption)"
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
@click.option("--acknowledge-prior-consumption", is_flag=True,
              help="Cross a block something has already read into V.")
@click.option("--rationale", default=None,
              help="Why crossing it is legitimate. Required to acknowledge.")
def blocks_promote(block_id, segment, acknowledge_prior_consumption,
                   rationale):
    """Promote a block to a higher segment (e.g., R→V)."""
    root = _get_root()
    block_reg = _get_block_registry(root)

    try:
        block_reg.promote_block(
            block_id, segment,
            acknowledge_prior_consumption=acknowledge_prior_consumption,
            rationale=rationale)
        click.echo("Promoted block {} to segment {}".format(block_id, segment))
        if acknowledge_prior_consumption:
            click.echo("  acknowledged over prior consumption: {}".format(
                rationale))
    except (ValueError, KeyError) as e:
        click.echo("Error: {}".format(e), err=True)
        sys.exit(1)


@blocks.command("acknowledge")
@click.argument("block_id")
@click.argument("entry_id")
@click.option("--rationale", required=True,
              help="What was read, and what the block is now unfit for.")
def blocks_acknowledge(block_id, entry_id, rationale):
    """Explain consumption the block's segment does not imply.

    Use when a block labelled Reserve turns out to have been read. The
    segment does not move — moving it would need a demotion path, and
    demotion is what makes single-use validation meaningless. The
    explanation attaches instead, and `promote` reads it back to you if
    you later try to spend the block as validation data.
    """
    root = _get_root()
    block_reg = _get_block_registry(root)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        block_reg.acknowledge_consumption(block_id, entry_id, rationale, ts)
        click.echo("Acknowledged consumption of {} by {}".format(
            block_id, entry_id))
        click.echo("  {}".format(rationale))
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
            from proofhouse.core.fill import compose_filled_entry
            fill_log = _fill_log(root)

            for entry in entries:
                # Read the COMPOSED entry, not the registered one. A fill
                # never rewrites the entry file — doing so would change its
                # hash and orphan every later entry chained to it — so the
                # registered YAML still says pending forever. Reporting from
                # it would mean `status` kept announcing a pending field that
                # had already been filled, which is the tool lying about its
                # own state.
                composed = compose_filled_entry(
                    entry, fill_log.for_entry(entry["id"]))

                pending = []
                pc = composed.get("power_check", {})
                val = pc.get("value", {})
                if isinstance(val, dict) and val.get("pending"):
                    pending.append("power_check")
                elif isinstance(val, dict) and val.get("flagged"):
                    pending.append("power_check=FLAGGED")

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


# ---------------------------------------------------------------------------
# Phase 2c — gate, fill, reject.
#
# These are the call sites that terminate the S-2 "unwired function" regress.
# Every layer below has been reachable only from tests: evaluate_gate was wired
# by run_gate, run_gate by nothing. A chain of wiring only ends somewhere a
# human types a command.
# ---------------------------------------------------------------------------

def _ledger(root):
    return Ledger(os.path.join(root, ".proofhouse", "ledger", "ledger.jsonl"))


def _registry(root):
    return Registry(os.path.join(root, ".proofhouse", "registry"),
                    block_registry=_get_block_registry(root))


def _amendment_log(root):
    from proofhouse.core.amend import AmendmentLog
    return AmendmentLog(
        os.path.join(root, ".proofhouse", "amendments", "amendments.jsonl"))


def _fill_log(root):
    from proofhouse.core.fill import FillLog
    return FillLog(
        os.path.join(root, ".proofhouse", "fills", "fills.jsonl"))


def _rejected_log(root):
    from proofhouse.core.amend import RejectedIdeasLog
    return RejectedIdeasLog(
        os.path.join(root, ".proofhouse", "rejected", "rejected.jsonl"))


def _last_run_id(ledger):
    rows = ledger.all_rows()
    return rows[-1]["run_id"] if rows else None


def _manifests_dir(root):
    return os.path.join(root, ".proofhouse", "manifests")


def _latest_eval_run_for(ledger, entry_id):
    """The most recent eval run this entry could be gated on.

    The ledger is the index (D-1): a run with no row never happened as far as
    the record is concerned, so resolving the source here rather than
    scanning manifest files is what forces the row to exist. Whether that
    run's result is actually bound is `run_gate`'s question, not this
    function's.
    """
    candidates = [r for r in ledger.all_rows()
                  if r.get("kind") in ("eval", "subperiod")
                  and r.get("registry_ref") == entry_id]
    return candidates[-1]["run_id"] if candidates else None


@cli.command()
@click.argument("entry_id")
@click.option("--source-run", default=None,
              help="Eval run to gate. Defaults to this entry's latest.")
@click.option("--replay", is_flag=True,
              help="Re-render an already-recorded verdict. Logged as "
                   "kind=maintenance and excluded from the trial count.")
def gate(entry_id, source_run, replay):
    """Render a pre-committed gate. Pass or fail, no interpretation."""
    from proofhouse.chain.hashing import content_hash
    from proofhouse.core.amend import RerunRefused
    from proofhouse.core.fill import compose_filled_entry
    from proofhouse.core.gate import GateError
    from proofhouse.core.gate_run import (ProvenanceRefused,
                                          foreclosure_advisory, run_gate)

    root = _get_root()
    try:
        entry = _registry(root).get_entry(entry_id)
    except KeyError:
        raise click.ClickException("No such entry: {}".format(entry_id))

    ledger = _ledger(root)
    source_run = source_run or _latest_eval_run_for(ledger, entry_id)
    if source_run is None:
        # Not a failing gate. There is no result to compare, so there is
        # nothing to render a verdict about (§2.5).
        raise click.ClickException(
            "No eval run recorded for {}. Run the eval first — a missing "
            "result is a pipeline state, not a gate outcome, so no verdict is "
            "rendered.".format(entry_id)
        )

    fills = _fill_log(root).for_entry(entry_id)

    # The gate runs against the registered entry composed with its fills, and
    # records that composed hash — which is what lets a later ordering check
    # establish which version was in force.
    effective = compose_filled_entry(entry, fills)
    effective["hash"] = content_hash(
        {k: v for k, v in effective.items() if k != "hash"})

    try:
        # The result is NOT passed in. run_gate recovers it from the eval
        # row's manifest and re-checks the digests written when it ran, so
        # this command cannot hand the gate a number.
        verdict, run_id = run_gate(
            effective,
            source_run,
            ledger,
            _manifests_dir(root),
            amendments=_amendment_log(root).for_entry(entry_id),
            block_registry=_get_block_registry(root),
            kind="maintenance" if replay else "gate",
        )
    except RerunRefused as exc:
        raise click.ClickException(str(exc))
    except ProvenanceRefused as exc:
        raise click.ClickException(str(exc))
    except GateError as exc:
        raise click.ClickException(str(exc))
    except KeyError as exc:
        # statistics_from_result naming a statistic the recorded result does
        # not carry. A legible refusal, so it should read as one rather than
        # as a traceback.
        raise click.ClickException(str(exc.args[0] if exc.args else exc))

    click.echo("{}  gate: {}".format(entry_id, verdict.combination))
    for c in verdict.criteria:
        actual = "infeasible" if c.infeasible else c.actual
        click.echo("  {}  {}  {} {} {}   {}".format(
            c.name, c.statistic, actual, c.comparator, c.threshold,
            "PASS" if c.passed else "FAIL"))

    # No softening. §2.1's whole point is that 0.0495 against >= 0.05 reads as
    # FAIL with no adornment, and the output is where that either holds or
    # quietly stops holding.
    click.echo("verdict: {}".format("PASS" if verdict.passed else "FAIL"))
    if verdict.passed:
        click.echo("licenses: {}".format(
            (verdict.pass_licenses or "").strip()))
    else:
        click.echo("licenses: (n/a — did not pass)")
    click.echo("does not establish: {}".format(
        (verdict.pass_does_not_establish or "").strip()))

    advisory = foreclosure_advisory(
        effective, [(c.name, c.passed) for c in verdict.criteria])
    if advisory is not None:
        forced, non_decisive = advisory
        click.echo(
            "\nadvisory: the verdict is {} regardless of {} — flipping any of "
            "those alone would not change it. If any cost a validation block, "
            "that block did not need spending. Acting on this is your "
            "decision, filed as an amendment; the tool does not stop on its "
            "own.".format("PASS" if forced else "FAIL",
                          ", ".join(non_decisive)))

    click.echo("\nrecorded as {} (source eval run {}{})".format(
        run_id, source_run,
        ", kind=maintenance — a replay, excluded from the trial count"
        if replay else ""))
    if not verdict.passed:
        sys.exit(1)


@cli.command()
@click.argument("entry_ids", nargs=-1, required=True)
@click.option("--q", "fdr_q", type=float, required=True,
              help="Target false-discovery rate. Required, no default.")
@click.option("--stage", "screen_stage", default="1a",
              help="'1a' magnitude screen, or '1b' consistency screen.")
@click.option("--source-run", "source_run_pairs", multiple=True,
              metavar="ENTRY=RUN",
              help="Screen this exact run for an entry. Defaults to the "
                   "entry's latest stage-1 eval run.")
def screen(entry_ids, fdr_q, screen_stage, source_run_pairs):
    """Screen a batch of registered hypotheses. Enrichment, not proof.

    Reads each entry's recorded stage-1 eval run — the same way `gate` reads
    the run it renders a verdict against — applies the step-up procedure the
    entries registered, and records the decision as one `screen` row.

    Nothing is executed and no block is consumed here: the trials were
    counted when the runs being read were made. Zero survivors is a real
    outcome, not a refusal, so it exits 0; only a refusal exits non-zero.
    """
    from proofhouse.core.gate_run import ProvenanceRefused
    from proofhouse.core.screen import screen_recorded_runs

    root = _get_root()
    registry = _registry(root)

    entries = []
    for entry_id in entry_ids:
        try:
            entries.append(registry.get_entry(entry_id))
        except KeyError:
            raise click.ClickException("No such entry: {}".format(entry_id))

    source_runs = {}
    for pair in source_run_pairs:
        if "=" not in pair:
            raise click.ClickException(
                "--source-run takes ENTRY=RUN; got {!r}".format(pair))
        entry_id, run_id = pair.split("=", 1)
        source_runs[entry_id.strip()] = run_id.strip()

    ledger = _ledger(root)
    try:
        # Every refusal is the library's own, printed as written: mixed
        # fdr_method, a missing p-value, a V block in the batch, an unknown
        # stage. Restating them here in the CLI's words would be a second
        # copy to drift.
        outcome = screen_recorded_runs(
            entries,
            ledger,
            _manifests_dir(root),
            fdr_q=fdr_q,
            block_registry=_get_block_registry(root),
            screen_stage=screen_stage,
            source_runs=source_runs,
        )
    except ProvenanceRefused as exc:
        raise click.ClickException(str(exc))
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc.args[0] if exc.args else exc))

    fdr = outcome["fdr"]
    survivors = outcome["survivors"]
    surviving = set(survivors)

    click.echo("batch    : {} (m={})".format(
        ", ".join(e["id"] for e in entries), fdr["m"]))
    click.echo("q        : {}".format(fdr_q))
    click.echo("method   : {}".format(fdr["method"]))
    click.echo("stage    : {}".format(screen_stage))
    # The threshold is the whole reason the batch has to be recorded: the
    # same p-value survives at m=10 and fails at m=200, so a survivor list
    # without m, q and the threshold is a claim nobody can check.
    if fdr["threshold"] is None:
        click.echo("threshold: none passed — no rank met (k/m)*q")
    else:
        click.echo("threshold: {}  (largest passing (k/m)*q)".format(
            fdr["threshold"]))

    click.echo("")
    width = max(len(e["id"]) for e in entries)
    for entry in entries:
        entry_id = entry["id"]
        scored = outcome["scored_results"][entry_id]
        click.echo("  {}  p={:<10} {}={:<12} run {}   {}".format(
            entry_id.ljust(width), scored.p_value,
            scored.statistic, scored.value,
            outcome["eval_run_ids"][entry_id],
            "SURVIVES" if entry_id in surviving else "dropped"))

    click.echo("")
    click.echo("survivors: {} of {} — {}".format(
        len(survivors), fdr["m"],
        ", ".join(survivors) if survivors else "none"))
    for entry_id in outcome["no_subperiod_runs"]:
        click.echo(
            "  note: {} has no sub-period runs recorded, so the consistency "
            "screen dropped it for want of a record rather than on the "
            "check.".format(entry_id))

    click.echo("\nrecorded as {} — the batch, m, q, method and threshold are "
               "in that row, so this decision can be rebuilt from the "
               "record.".format(outcome["screen_run_id"]))
    click.echo(
        "Surviving a screen is enrichment, not proof. These stage-1 runs "
        "stay out of the stage-2 trial count; a survivor that later runs on "
        "a validation block counts one trial on its universe.")


@cli.command()
@click.argument("entry_id")
@click.argument("field", default="power_check")
def fill(entry_id, field):
    """Fill a pending field from its pinned rule. Once only."""
    from proofhouse.chain.hashing import content_hash
    from proofhouse.core.fill import (
        FillRefused, compose_filled_entry, fill_pending_field,
    )

    root = _get_root()
    try:
        entry = _registry(root).get_entry(entry_id)
    except KeyError:
        raise click.ClickException("No such entry: {}".format(entry_id))

    log = _fill_log(root)
    existing = log.for_entry(entry_id)
    before = compose_filled_entry(entry, existing)

    try:
        filled, resolved = fill_pending_field(before, field)
    except FillRefused as exc:
        raise click.ClickException(str(exc))

    def _hash(e):
        return content_hash({k: v for k, v in e.items() if k != "hash"})

    log.record(
        entry_id=entry_id,
        field=field,
        rule=entry.get(field, {}).get("value", {}).get("fill_rule"),
        inputs=entry.get(field, {}).get("inputs") or {},
        resolved=resolved,
        entry_hash_before=_hash(before),
        entry_hash_after=_hash(filled),
        ledger_anchor=_last_run_id(_ledger(root)),
    )

    value = filled[field]["value"]
    click.echo("{}  {} filled".format(entry_id, field))
    click.echo("  rule  : {}".format(value["fill_rule"]))
    click.echo("  value : {}".format(value["filled"]))
    for key in sorted(value["derivation"]):
        click.echo("    {}: {}".format(key, value["derivation"][key]))
    if value["flagged"]:
        click.echo(
            "  FLAGGED: power < 0.5 — as registered, this design is more "
            "likely than not to miss its own hypothesized effect. Recorded, "
            "not blocked.")


@cli.command()
@click.argument("entry_id")
def report(entry_id):
    """Render the findings memo for an entry's most recent gate."""
    from proofhouse.chain.hashing import content_hash
    from proofhouse.core.fill import compose_filled_entry
    from proofhouse.core.gate import GateError
    from proofhouse.core.gate_run import ProvenanceRefused
    from proofhouse.core.report import (ReportRefused, bind_verdict,
                                        build_report, render_text)

    root = _get_root()
    registry = _registry(root)
    try:
        entry = registry.get_entry(entry_id)
    except KeyError:
        raise click.ClickException("No such entry: {}".format(entry_id))

    fills = _fill_log(root).for_entry(entry_id)
    effective = compose_filled_entry(entry, fills)
    effective["hash"] = content_hash(effective)

    try:
        # The verdict is READ from the recorded gate row and recomputed from
        # the result that row is bound to. A disagreement refuses rather than
        # rendering, and says which side moved.
        verdict, results, provenance = bind_verdict(
            effective,
            _ledger(root),
            _manifests_dir(root),
            block_registry=_get_block_registry(root),
        )
    except (ReportRefused, ProvenanceRefused) as exc:
        raise click.ClickException(str(exc))
    except GateError as exc:
        raise click.ClickException(str(exc))
    except KeyError as exc:
        raise click.ClickException(str(exc.args[0] if exc.args else exc))

    # The lineage, not just this entry. An entry whose own amendment was
    # ordinary carries an empty `amendments` list while its ancestors carry
    # the invalidation, so the revision disclosure reads zero unless the
    # ancestry is supplied.
    ancestors = [registry.get_entry(a)
                 for a in sorted(registry.ancestor_ids(entry))]

    click.echo(render_text(build_report(
        effective, verdict,
        ledger=_ledger(root),
        amendments=_amendment_log(root).for_entry(entry_id),
        fills=fills,
        scored_results=results,
        ancestors=ancestors,
        provenance=provenance,
    )))


@cli.command()
@click.option("--catalog", default=None,
              help="Path to failure-catalog.md, for resolving #N citations.")
def verify(catalog):
    """Full integrity check across every log, not just the chains."""
    from proofhouse.core.verify import (UNVERIFIABLE,
                                        catalog_citation_resolver, verify_all)

    root = _get_root()
    resolver = None
    catalog_path = catalog or os.path.join(root, "failure-catalog.md")
    if os.path.exists(catalog_path):
        resolver = catalog_citation_resolver(catalog_path)

    ok, findings = verify_all(
        registry=_registry(root),
        ledger=_ledger(root),
        amendment_log=_amendment_log(root),
        fill_log=_fill_log(root),
        rejected_log=_rejected_log(root),
        block_registry=_get_block_registry(root),
        citation_resolver=resolver,
        manifests_dir=_manifests_dir(root),
    )

    if resolver is None:
        click.echo("note: no failure catalog found at {} — external "
                   "citations are UNCHECKED, not verified.".format(
                       catalog_path))

    errors = [f for f in findings if f.severity == "error"]
    warnings = [f for f in findings if f.severity == "warning"]
    # Counted on its own line, not folded into the warnings. "We could not
    # check this" and "we checked, and the evidence is weaker than it looks"
    # are different states, and the second reads as reassurance the first has
    # not earned. See core.verify.UNVERIFIABLE.
    unverifiable = [f for f in findings if f.severity == UNVERIFIABLE]

    for finding in errors + warnings + unverifiable:
        click.echo("  {}".format(finding))

    click.echo("\n{} error(s), {} warning(s), {} unverifiable".format(
        len(errors), len(warnings), len(unverifiable)))
    if ok:
        click.echo("verify: OK")
    else:
        click.echo("verify: FAILED")
        sys.exit(1)


@cli.command()
@click.argument("entry_id")
@click.argument("amended_file")
@click.option("--rationale", required=True, help="Why this change.")
@click.option("--impact", required=True,
              help="What it changes about how the result may be read.")
def amend(entry_id, amended_file, rationale, impact):
    """File an amendment: record and successor entry, together or not at all."""
    from proofhouse.core.amend import AmendmentRefused, file_amendment

    root = _get_root()
    with open(amended_file) as fh:
        amended = yaml.safe_load(fh)

    try:
        registered, record = file_amendment(
            registry=_registry(root),
            amendment_log=_amendment_log(root),
            entry_id=entry_id,
            amended_entry=amended,
            rationale=rationale,
            impact=impact,
            ledger_anchor=_last_run_id(_ledger(root)),
        )
    except (AmendmentRefused, ValidationError, ValueError) as exc:
        raise click.ClickException(str(exc))

    click.echo("{} amends {}".format(registered["id"], entry_id))
    click.echo("  amendment: {} (#{})".format(
        record["amendment_id"], record["number"]))
    for change in record["diff"]:
        click.echo("    {}: {!r} -> {!r}".format(
            change["field"], change["before"], change["after"]))
    if record["invalidating"]:
        click.echo(
            "  INVALIDATING: this change invalidates the pre-registration. "
            "The successor validates on blocks the parent did not use.")
    click.echo("  anchored at: {}".format(record["ledger_anchor"]))


@cli.command()
@click.option("--idea", required=True, help="What was considered.")
@click.option("--reason", required=True, help="Why it was rejected.")
@click.option("--entry-id", default=None, help="Related entry, if any.")
def reject(idea, reason, entry_id):
    """Record a rejected idea, so a later partial adoption leaves a trace."""
    root = _get_root()
    record = _rejected_log(root).reject(
        idea=idea, reason=reason, entry_id=entry_id,
        ledger_anchor=_last_run_id(_ledger(root)),
    )
    click.echo("recorded {}".format(record["rejection_id"]))


def _packaged_skill_path():
    """The copy of the agent skill that shipped inside this install."""
    import proofhouse

    return os.path.join(
        os.path.dirname(os.path.abspath(proofhouse.__file__)),
        "data", "skill", "SKILL.md")


@cli.command("install-skill")
@click.option("--user", "user_wide", is_flag=True,
              help="Install into ~/.claude/skills, for every project.")
@click.option("--dest", default=None, type=click.Path(file_okay=False),
              help="A skills directory to install into instead; the skill "
                   "goes in its proofhouse/ subfolder.")
@click.option("--force", is_flag=True,
              help="Replace an existing skill file that differs.")
def install_skill(user_wide, dest, force):
    """Copy the proofhouse agent skill into a project.

    With no option the skill goes to .claude/skills/proofhouse/SKILL.md
    under the current directory, where Claude Code loads it. An identical
    file is left alone; a different one is refused unless --force is given.
    """
    if user_wide and dest:
        raise click.ClickException("Pass --user or --dest, not both.")

    source = _packaged_skill_path()
    if not os.path.isfile(source):
        raise click.ClickException(
            "The skill file is missing from this install ({}). Reinstall "
            "proofhouse, or copy .claude/skills/proofhouse/ from a clone."
            .format(source))

    if dest:
        skills_dir = dest
    elif user_wide:
        skills_dir = os.path.join(os.path.expanduser("~"), ".claude", "skills")
    else:
        skills_dir = os.path.join(os.getcwd(), ".claude", "skills")
    target = os.path.join(os.path.abspath(skills_dir), "proofhouse", "SKILL.md")

    with open(source, "rb") as fh:
        packaged = fh.read()

    existed = os.path.exists(target)
    if existed:
        with open(target, "rb") as fh:
            current = fh.read()
        if current == packaged:
            click.echo("Already up to date: {}".format(target))
            return
        if not force:
            raise click.ClickException(
                "{} exists and differs from the skill in this install. "
                "Nothing was written. Re-run with --force to replace it."
                .format(target))

    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "wb") as fh:
        fh.write(packaged)
    click.echo("{} the proofhouse skill at {}".format(
        "Replaced" if existed else "Installed", target))


if __name__ == "__main__":
    cli()
