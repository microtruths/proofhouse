# proofhouse.eval.runner — eval orchestrator
"""
Phase 1 Build Plan §1a — Eval Runner.

The single orchestrator: run_eval() is the ONLY public entry point for
executing an evaluation. It performs every safety check in fixed order:

    1. Verify artifact pin  (wall, not tripwire — #26)
    2. Enforce block isolation  (V-block in stage-1 = hard error — §5.2)
    3. Check contamination  (transitive — §1b) — BEFORE consumption
    4. Consume blocks  (single-use enforcement — E4/A7)
    5. Load data via adapter
    6. Run artifact with parameters from the entry  (not embedded constants)
    7. Score result  (trial count FROM the ledger, never a parameter — §4)
    8. Build manifest with full provenance  (§5.3)
    9. Persist manifest to disk  (§5.3: "a result without a manifest...")
   10. Append ledger row with stage and segment
   11. Return (ScoredResult, Diagnostics, manifest)

Steps 3 and 4 are ordered so that a contaminated entry does NOT burn
its V-blocks on a run that never executes. consume_block has no inverse;
spending blocks on a refused run is an unrecoverable loss (B1).

Parent plan §5.3:
    "A result without a manifest is not a result."
"""

import importlib.util
import platform
import time
from datetime import datetime, timezone

from proofhouse.core.artifact_pin import verify_pin
from proofhouse.eval.types import ScoredResult, Diagnostics


class EvalResult:
    """Container for the complete output of run_eval.

    Attributes:
        scored: ScoredResult — the gate-evaluable statistic.
        diagnostics: Diagnostics — everything the gate must not see.
        manifest: Dict — full provenance record (also persisted to disk).
        run_id: str — the ledger row ID assigned to this run.
    """

    def __init__(self, scored, diagnostics, manifest, run_id):
        self.scored = scored
        self.diagnostics = diagnostics
        self.manifest = manifest
        self.run_id = run_id

    def __repr__(self):
        return "EvalResult(run_id={!r}, scored={!r})".format(
            self.run_id, self.scored
        )


def run_eval(entry, adapter, block_registry, ledger,
             stage, score_fn, manifests_dir, seed=None):
    """Execute a complete evaluation run with all safety guarantees.

    This is the ONLY public entry point. Every safety property —
    pin verification, block isolation, contamination check, trial-count
    coupling, manifest persistence — is enforced here in fixed order.
    Skipping a step requires modifying this function, which is auditable.

    The score_fn callback computes the statistic from (predictions, outcomes)
    and returns (ScoredResult, Diagnostics). The orchestrator provides
    the trial_count to score_fn so the statistic can incorporate it
    (e.g., DSR). score_fn must NOT read the trial count from anywhere
    else — the orchestrator is the source of truth.

    Stage-aware trial counting:
        Stage-1 runs use count_trials() — full multiplicity.
        Stage-2 runs use count_trials_stage2() — excludes stage-1 rows.
        This is the §2.3.1 separation: screening on S1/S2 doesn't burn
        V's multiplicity budget. Without this, the staged funnel delivers
        zero statistical benefit.

    Args:
        entry: Registry entry dict. Must have signal.implementation
               (with path and sha256), signal.parameters, and
               validation_blocks.
        adapter: Data adapter instance (e.g., CSVAdapter). Must have
                 .data_hash and .load_returns().
        block_registry: BlockRegistry instance.
        ledger: Ledger instance.
        stage: Stage label (1 or 2).
        score_fn: Callable(predictions, outcomes, trial_count) →
                  (ScoredResult, Diagnostics).
                  Receives trial_count from the ledger — must use it,
                  not compute its own.
        manifests_dir: Path to the manifests/ directory. The manifest is
                       persisted to disk here. §5.3: "a result without a
                       manifest is not a result."
        seed: Optional random seed for reproducibility.

    Returns:
        EvalResult with scored, diagnostics, manifest, and run_id.

    Raises:
        ValueError: If any safety check fails (pin mismatch, block
                    isolation violation, contamination detected).
        FileNotFoundError: If the artifact file does not exist.
    """
    entry_id = entry["id"]
    impl = entry["signal"]["implementation"]
    artifact_path = impl["path"]
    expected_hash = impl["sha256"]
    parameters = entry["signal"]["parameters"]
    validation_blocks = entry.get("validation_blocks", [])
    universe = entry["universe"]["id"]
    seam_acknowledged = entry.get("seam_acknowledged", False)

    # Determine segment from blocks (for the ledger row).
    # Uses the public get_block() API, not _blocks.
    segments = set()
    for bid in validation_blocks:
        block = block_registry.get_block(bid)
        segments.add(block.get("segment", ""))
    segment = segments.pop() if len(segments) == 1 else "mixed"

    # ---- Step 1: Verify artifact pin ----
    # This is the wall, not the tripwire. If the hash doesn't match,
    # execution is refused. Nothing below this line runs.
    verify_pin(artifact_path, expected_hash)

    # ---- Step 2: Enforce block isolation ----
    # Stage-1 may not touch V-blocks. This is a hard error (§5.2).
    block_registry.enforce_block_isolation(validation_blocks, stage)

    # ---- Step 3: Check contamination ----
    # Transitive: if a dependency touched V in stage-1, this entry
    # inherits the contamination.
    #
    # This runs BEFORE consumption (step 4) so that a contaminated
    # entry does not burn its V-blocks on a run that never executes.
    # consume_block has no inverse — spending blocks on a refused run
    # is an unrecoverable loss against B1's design goal.
    from proofhouse.core.screen import check_contamination
    contamination = check_contamination(entry_id, ledger)
    if contamination["contaminated"]:
        raise ValueError(
            "Entry {} is contaminated and cannot be evaluated.\n"
            "Reason: {}".format(entry_id, contamination["reason"])
        )

    # ---- Step 4: Consume blocks ----
    # Record that this entry has touched these blocks. V-blocks are
    # single-use (E4/A7). This is where the consumption ledger that
    # replaced the hand-maintained "Preserved Assets" table gets updated.
    for bid in validation_blocks:
        block_registry.consume_block(bid, entry_id, seam_acknowledged)

    # ---- Step 5: Load data ----
    data = adapter.load_returns()

    # ---- Step 6: Run artifact ----
    # Parameters come from the entry, not from embedded constants.
    # This makes polarity drift unrepresentable.
    t_start = time.monotonic()
    raw_result = _load_and_run_artifact(artifact_path, data, parameters)
    t_elapsed = time.monotonic() - t_start

    # The artifact returns (predictions, outcomes)
    predictions, outcomes = raw_result

    # ---- Step 7: Score ----
    # Trial count comes FROM the ledger. The score_fn receives it as
    # an argument — it cannot read it from elsewhere. This is the
    # coupling that parent §4 requires: "read FROM the ledger, never
    # a parameter."
    #
    # Stage-aware counting (§2.3.1):
    #   Stage 1 → count_trials(): full multiplicity
    #   Stage 2 → count_trials_stage2(): excludes stage-1 rows
    #
    # Without this, the staged funnel delivers zero statistical benefit.
    # You'd get identical numbers by testing everything directly on V.
    if stage == 2:
        trial_count = max(1, ledger.count_trials_stage2(universe))
    else:
        trial_count = max(1, ledger.count_trials(universe))

    scored, diagnostics = score_fn(predictions, outcomes, trial_count)

    # Verify score_fn returned the right types
    if not isinstance(scored, ScoredResult):
        raise TypeError(
            "score_fn must return (ScoredResult, Diagnostics), "
            "got ({}, ...)".format(type(scored).__name__)
        )
    if not isinstance(diagnostics, Diagnostics):
        raise TypeError(
            "score_fn must return (ScoredResult, Diagnostics), "
            "got (..., {})".format(type(diagnostics).__name__)
        )

    # ---- Step 8: Build manifest ----
    manifest = _create_eval_manifest(
        run_id=None,  # filled after ledger append
        code_hash=expected_hash,
        data_hash=adapter.data_hash,
        seed=seed,
        registry_ref=entry_id,
    )
    manifest["wall_clock_seconds"] = round(t_elapsed, 3)

    # ---- Step 9: Persist manifest to disk ----
    # §5.3: "a result without a manifest is not a result."
    # The manifest lives on disk, not just in memory. Without this,
    # the Phase 1 exit criterion — "reproduce a past result from a
    # manifest" — is impossible.
    #
    # We persist after building but before the ledger append so that
    # the run_id can be set after the append and the manifest re-saved.
    # (Initial save uses a temp run_id; final save overwrites.)

    # ---- Step 10: Append ledger row ----
    stats_scored = [scored.statistic]
    outcome_str = "{}={:.6f}".format(scored.statistic, scored.value)
    ledger.append_row(
        code_hash=expected_hash,
        data_hash=adapter.data_hash,
        universe=universe,
        kind="eval",
        blocks_touched=validation_blocks,
        stats_scored=stats_scored,
        registry_ref=entry_id,
        outcome=outcome_str,
        stage=stage,
        segment=segment,
    )

    # Get the run_id that was just assigned
    last_row = ledger.all_rows()[-1]
    run_id = last_row["run_id"]
    manifest["run_id"] = run_id

    # Now persist with the real run_id
    from proofhouse.core.manifest import save_manifest
    save_manifest(manifest, manifests_dir)

    # ---- Step 11: Return ----
    return EvalResult(
        scored=scored,
        diagnostics=diagnostics,
        manifest=manifest,
        run_id=run_id,
    )


# ---------------------------------------------------------------------------
# Helpers — used by the orchestrator and available for unit-testing
# individual mechanics. They are NOT the system's API; calling them
# directly bypasses every guarantee that run_eval() provides.
#
# These are prefixed with _ to signal they are internal. Tests that
# need to exercise individual mechanics (e.g., artifact loading, pin
# verification) should import from the canonical modules:
#   - proofhouse.core.artifact_pin.verify_pin
#   - proofhouse.core.artifact_pin.compute_artifact_hash
# ---------------------------------------------------------------------------

def _load_and_run_artifact(artifact_path, data, parameters):
    """Load a strategy artifact and execute it with parameters from the entry.

    The artifact must define a `run(data, parameters)` function. Parameters
    come from `signal.parameters` in the registry entry — the artifact reads
    them as arguments, not as embedded constants. This makes polarity drift
    unrepresentable.

    Args:
        artifact_path: Path to the Python artifact.
        data: Data to pass to the strategy (list of (date, value) tuples).
        parameters: Dict of parameters from signal.parameters.

    Returns:
        The return value of the artifact's run() function.
    """
    spec = importlib.util.spec_from_file_location("strategy", artifact_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise ValueError(
            "Artifact {} does not define a run(data, parameters) function".format(
                artifact_path
            )
        )

    return module.run(data, parameters)


def _create_eval_manifest(run_id, code_hash, data_hash, seed=None,
                          config_hash=None, package_versions=None,
                          registry_ref=None, execution="tool-executed"):
    """Create a manifest for an eval run.

    Parent plan §5.3:
        "code hash, data hash, config hash, seed, package versions,
         registry ref, ledger row id, wall-clock, host,
         execution: self-reported | tool-executed."

    Args:
        run_id: Ledger run_id (may be None; filled after append).
        code_hash: SHA-256 of the artifact.
        data_hash: SHA-256 of the data snapshot.
        seed: Random seed used.
        config_hash: Optional config hash.
        package_versions: Optional dict of package versions.
        registry_ref: Entry ID.
        execution: 'self-reported' or 'tool-executed'.

    Returns:
        Dict representing the manifest.
    """
    return {
        "run_id": run_id,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_hash": code_hash,
        "data_hash": data_hash,
        "config_hash": config_hash,
        "seed": seed,
        "package_versions": package_versions or {},
        "registry_ref": registry_ref,
        "execution": execution,
        "host": platform.node(),
        "wall_clock_seconds": None,  # filled by run_eval after execution
    }
