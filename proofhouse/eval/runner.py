# proofhouse.eval.runner — eval orchestrator
"""
Phase 1 Build Plan §1a — Eval Runner.

The single orchestrator: run_eval() is the ONLY public entry point for
executing an evaluation. It performs every safety check in fixed order:

    1. Verify artifact pin  (wall, not tripwire — #26)
    2. Enforce block isolation  (V-block in stage-1 = hard error — §5.2)
    3. Check contamination  (transitive — §1b)
    4. Refuse an amended entry reusing its parent's blocks
    5. Consume blocks  (single-use enforcement — E4/A7)
    6. Load data via adapter
    7. Run artifact with parameters from the entry  (not embedded constants)
    8. Score result  (trial count FROM the ledger, never a parameter — §4)
    9. Build manifest with full provenance  (§5.3), including the result
   9a. Digest the manifest, so the RESULT enters the hash chain
   10. Persist manifest to disk  (§5.3: "a result without a manifest...")
   11. Append ledger row with stage, segment, and both digests
   12. Return (ScoredResult, Diagnostics, manifest)

Every refusal (3 and 4) precedes consumption (5) so that a rejected run
does NOT burn its V-blocks. consume_block has no inverse; spending blocks
on a run that never executes is an unrecoverable loss (B1).

Step 4 exists because the registration-time version of the same rule is
bypassable — validation_blocks is empty at registration in the normal
workflow, so the overlap check passes trivially and the block is spent
here instead. Recording at registration catches declared conflicts;
gating at execution is what makes it structural.

Parent plan §5.3:
    "A result without a manifest is not a result."
"""

import importlib.util
import inspect
import os
import platform
import time
from datetime import datetime, timezone

from proofhouse.chain.canonical import decimal_str
from proofhouse.core.artifact_pin import verify_pin
from proofhouse.core.blocks import _SINGLE_USE_SEGMENTS
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
             stage, score_fn=None, manifests_dir=None, seed=None,
             ledger_kind="eval", parent_run_id=None):
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

    if manifests_dir is None:
        raise ValueError(
            "manifests_dir is required: a result without a manifest is not a "
            "result (§5.3).")

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

    # ---- Step 4: Amended entries may not reuse a parent's blocks ----
    # Registry.register() refuses an invalidating amendment (polarity or
    # expected_effect_size) whose validation_blocks overlap the parent's.
    # That check alone is bypassable: validation_blocks is empty at
    # registration in the normal workflow, so the intersection is empty
    # and the check passes trivially — then the block gets consumed here,
    # unobserved.
    #
    # Same shape as the artifact pin (#26): recording at registration
    # catches DECLARED conflicts; gating at execution makes it structural.
    # One-sided p-values depend on this holding.
    for amendment in entry.get("amendments", []):
        parent_id = amendment.get("parent_id")
        if not parent_id:
            continue
        for bid in validation_blocks:
            block = block_registry.get_block(bid)
            prior = {c.get("entry_id") for c in block.get("consumed_by", [])}
            if parent_id in prior:
                raise ValueError(
                    "Entry {} amends {} ({} changed from {!r} to {!r}), which "
                    "invalidates the pre-registration — but block {} was "
                    "already consumed by that parent. An amended entry must "
                    "validate on blocks the parent did not use.".format(
                        entry_id, parent_id, amendment.get("field"),
                        amendment.get("original"), amendment.get("current"),
                        bid,
                    )
                )

    # ---- Step 4b: One eval per single-use block per entry ----
    # consume_block is idempotent for the same entry on purpose: a block can
    # be read as reserve, promoted, then validated by the entry that read it.
    # So the block registry cannot tell a second measurement from the first,
    # and a second eval on a spent V block was recorded, then gated again
    # (BACKLOG D-12). The ledger can tell: an eval row for this entry that
    # touched the block means the measurement was taken. Checked before
    # consumption so that nothing is spent on a refused run. Subperiod runs
    # decompose a parent eval and must carry parent_run_id; they are not a
    # second measurement.
    if ledger_kind == "eval":
        _refuse_second_eval(entry_id, validation_blocks, block_registry,
                            ledger)

    # ---- Step 4c: Load the artifact and resolve the score function ----
    # The artifact's output is pinned by step 1. The code that turns that
    # output into the gated statistic used to be a callable the caller
    # handed in, covered by nothing (BACKLOG D-12). It now lives in the
    # pinned artifact by default, and when a callable is supplied the
    # manifest records where it came from, so `verify` and `report` can say
    # whether the statistic's computation was verified or merely attested.
    # Resolved here, still before anything is consumed, so a missing score
    # function refuses without spending a block.
    module = _load_artifact(artifact_path)
    score_fn, scoring = _resolve_score_fn(
        score_fn, module, artifact_path, expected_hash)

    # ---- Step 5: Consume blocks ----
    # Record that this entry has touched these blocks. V-blocks are
    # single-use (E4/A7). This is where the consumption ledger that
    # replaced the hand-maintained "Preserved Assets" table gets updated.
    for bid in validation_blocks:
        block_registry.consume_block(bid, entry_id, seam_acknowledged)

    # ---- Step 6: Load data ----
    data = adapter.load_returns()

    # ---- Step 7: Run artifact ----
    # Parameters come from the entry, not from embedded constants.
    # This makes polarity drift unrepresentable.
    t_start = time.monotonic()
    raw_result = _run_artifact(module, data, parameters)
    t_elapsed = time.monotonic() - t_start

    # The artifact returns (predictions, outcomes)
    predictions, outcomes = raw_result

    # ---- Step 8: Score ----
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

    # ---- Step 9: Build manifest ----
    manifest = _create_eval_manifest(
        run_id=None,  # filled after ledger append
        code_hash=expected_hash,
        data_hash=adapter.data_hash,
        seed=seed,
        registry_ref=entry_id,
    )
    manifest["wall_clock_seconds"] = round(t_elapsed, 3)
    # Where the score function came from. Inside the manifest digest, so
    # inside the chain: a later reader can check whether the pinned artifact
    # computed the statistic (BACKLOG D-12).
    manifest["scoring"] = scoring

    # THE RESULT ITSELF, not only its provenance.
    #
    # §5.3 says "a result without a manifest is not a result", and until now
    # the manifest carried code/data hashes but never the statistic they
    # produced. That made the Phase 1 exit criterion — reproduce a past result
    # from a manifest — impossible to check, and left the gate with nowhere to
    # read scored results from, which is what blocked the `gate` CLI.
    #
    # Diagnostics are deliberately NOT written here. A4 keeps them
    # structurally outside the verdict; putting them in the same record the
    # gate reads would reopen that boundary through the back door.
    manifest["result"] = scored.to_dict()

    # ---- Step 9a: Digest the manifest, so the RESULT enters the chain ----
    #
    # The manifest was plain YAML: unhashed, unchained, and referenced by
    # nothing. Editing R-000012.yaml changed what `proofhouse report`
    # printed and every hash chain still verified clean, which is the exact
    # hole this closes. The digests go on the ledger row, which IS hashed and
    # chained, so a later edit to the file contradicts an append-only record.
    #
    # Computed BEFORE the append because the row carries them, and the row's
    # own hash must cover them (the entry_hash comment below is the same
    # rule). run_id is excluded from manifest_hash precisely because it is
    # assigned by that append — see _DIGEST_EXCLUDED.
    from proofhouse.core.manifest import manifest_digest, result_digest
    result_hash = result_digest(manifest["result"])
    manifest_hash = manifest_digest(manifest)

    # ---- Step 10: Persist manifest to disk ----
    # §5.3: "a result without a manifest is not a result."
    # The manifest lives on disk, not just in memory. Without this,
    # the Phase 1 exit criterion — "reproduce a past result from a
    # manifest" — is impossible.
    #
    # We persist after building but before the ledger append so that
    # the run_id can be set after the append and the manifest re-saved.
    # (Initial save uses a temp run_id; final save overwrites.)

    # ---- Step 11: Append ledger row ----
    #
    # stats_scored names every gate-evaluable statistic this run produced,
    # not only the headline one. A gate criterion may compare a secondary
    # statistic against a registered threshold, so a row listing only the
    # primary understates what was measured — and `verify` cross-checks the
    # gate row's criteria against this list.
    stats_scored = sorted({scored.statistic} | set(scored.secondary_statistics))
    # EXACT, not rounded. This read `{:.6f}`, so the row said
    # clustered_t_stat=1.861711 for a value of 1.8617105276654804 — six
    # digits of a number whose comparison against 2.923 was the verdict. A
    # rounded restatement of a hashed number is a second representation that
    # can only ever disagree with the first; decimal_str is the same rule the
    # manifest and the criterion outcomes use.
    outcome_str = "{}={}".format(scored.statistic, decimal_str(scored.value))
    ledger.append_row(
        code_hash=expected_hash,
        data_hash=adapter.data_hash,
        universe=universe,
        kind=ledger_kind,
        blocks_touched=validation_blocks,
        stats_scored=stats_scored,
        registry_ref=entry_id,
        outcome=outcome_str,
        stage=stage,
        segment=segment,
        # Part A: recorded so a screening batch is reconstructible. A
        # survivor list without the p-values that produced it is an
        # unfalsifiable claim — BH's threshold depends on the whole batch.
        p_value=scored.p_value,
        n_effective=scored.n_effective,
        # kind='subperiod' rows decompose a parent eval and are excluded
        # from the trial count: k entries over s sub-periods is k trials,
        # not k*s. The parent link is what stops the kind being a
        # laundering hole — the ledger refuses a subperiod row without it.
        parent_run_id=parent_run_id,
        # The result, inside the hash chain. A gate row later cites
        # result_hash to say which numbers its verdict was rendered against,
        # and `verify` recomputes both digests from the file on disk.
        result_hash=result_hash,
        manifest_hash=manifest_hash,
    )

    # Get the run_id that was just assigned
    last_row = ledger.all_rows()[-1]
    run_id = last_row["run_id"]
    manifest["run_id"] = run_id

    # Now persist with the real run_id
    from proofhouse.core.manifest import save_manifest
    save_manifest(manifest, manifests_dir)

    # ---- Step 12: Return ----
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

def _load_artifact(artifact_path):
    """Load a strategy artifact as a module.

    The artifact must define `run(data, parameters)`. It may also define
    `score_fn(predictions, outcomes, trial_count)`, which is where the score
    function belongs: the pin then covers the statistic's computation as
    well as the strategy's output.
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
    return module


def _run_artifact(module, data, parameters):
    """Execute a loaded artifact with parameters from the entry.

    Parameters come from `signal.parameters` in the registry entry — the
    artifact reads them as arguments, not as embedded constants. This makes
    polarity drift unrepresentable.
    """
    return module.run(data, parameters)


def _load_and_run_artifact(artifact_path, data, parameters):
    """Load a strategy artifact and execute it with parameters from the entry.

    Args:
        artifact_path: Path to the Python artifact.
        data: Data to pass to the strategy (list of (date, value) tuples).
        parameters: Dict of parameters from signal.parameters.

    Returns:
        The return value of the artifact's run() function.
    """
    return _run_artifact(_load_artifact(artifact_path), data, parameters)


def _resolve_score_fn(score_fn, module, artifact_path, code_hash):
    """Decide which score function runs, and record where it came from.

    Returns (score_fn, scoring). `scoring` goes into the manifest:
        {"source": "artifact", "code_hash": <the pin>}   — covered by the pin
        {"source": "caller",   "code_hash": None}        — attested only

    A supplied callable whose source file IS the pinned artifact counts as
    pinned: loading the artifact module yourself and passing its score_fn is
    the same code under the same hash. Anything else is `caller`, which is
    not refused — a lock that blocks working scripts gets routed around —
    but is recorded, warned on by `verify`, and printed by `report`.
    """
    if score_fn is None:
        score_fn = getattr(module, "score_fn", None)
        if score_fn is None:
            raise ValueError(
                "Artifact {} defines no score_fn(predictions, outcomes, "
                "trial_count) and none was supplied. The score function turns "
                "the artifact's output into the gated statistic; define it in "
                "the pinned artifact so the pin covers it.".format(
                    artifact_path))
        return score_fn, {"source": "artifact", "code_hash": code_hash}

    pinned = os.path.realpath(os.path.abspath(artifact_path))
    try:
        source_file = inspect.getsourcefile(score_fn) or inspect.getfile(score_fn)
    except TypeError:
        source_file = None
    if source_file and os.path.realpath(os.path.abspath(source_file)) == pinned:
        return score_fn, {"source": "artifact", "code_hash": code_hash}
    return score_fn, {"source": "caller", "code_hash": None}


def _refuse_second_eval(entry_id, validation_blocks, block_registry, ledger):
    """Refuse an `eval` run on a single-use block this entry already measured.

    Keyed on recorded eval rows, not on consumption: consumption is
    idempotent for the same entry by design, and a run whose pipeline broke
    after consuming but before its row was written must remain re-runnable
    (README: "halt, repair, re-run").
    """
    single_use = [
        bid for bid in validation_blocks
        if block_registry.get_block(bid).get("segment") in _SINGLE_USE_SEGMENTS
    ]
    if not single_use:
        return
    for row in ledger.all_rows():
        if row.get("kind") != "eval" or row.get("registry_ref") != entry_id:
            continue
        touched = row.get("blocks_touched") or []
        if isinstance(touched, str):
            # BACKLOG D-5: some rows carry a comma-joined string.
            touched = [t for t in touched.split(",") if t]
        overlap = sorted(set(single_use) & set(touched))
        if overlap:
            raise ValueError(
                "Entry {} already recorded eval run {} on validation block(s) "
                "{}. A validation block is spent once per hypothesis (E4): a "
                "second measurement on it is a re-test, and a gate on that "
                "measurement would be a second verdict on data already "
                "judged. Nothing was consumed or recorded. To re-render the "
                "recorded verdict: proofhouse gate {} --replay. To change the "
                "specification: file an amendment and validate on a block "
                "that postdates it.".format(
                    entry_id, row.get("run_id"), overlap, entry_id))


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
