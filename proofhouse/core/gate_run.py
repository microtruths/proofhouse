# proofhouse.core.gate_run — the safe way to invoke the gate engine
"""
Phase 2b — orchestration around the pure verdict function.

WHY THIS IS NOT IN gate.py
---------------------------
`core/gate.py` is pure by construction: no clock, no filesystem, no
randomness, enforced by test S-3's AST scan of its imports. Appending a ledger
row needs all three. Putting the orchestration here keeps that scan meaningful
instead of adding exemptions to it until it stops catching anything.

ORDER OF OPERATIONS IS THE WHOLE DESIGN
----------------------------------------
The re-run lock is checked FIRST, before any block is consumed, any row is
written, or any comparison is made. Phase 1 shipped the mirror-image bug —
contamination was checked *after* block consumption, so a refused run still
burned a validation block. A refusal must cost nothing, or the refusal itself
becomes the expensive path and people learn to avoid triggering it.

THE CALLER NO LONGER SUPPLIES THE NUMBERS
------------------------------------------
`run_gate` used to take `scored_results`, `code_hash` and `data_hash` as
arguments with no source check. Every link in the chain from measurement to
verdict was therefore loose: a caller could present a valid pair of hashes
alongside altered results, the manifest holding the real numbers was
unhashed and unchained, and `report` re-derived the verdict from whichever
manifest happened to be latest. Editing a manifest changed what the tool
printed and every hash chain still verified clean.

So the signature takes a `source_run_id` instead. The eval row must exist,
must belong to this entry, and must carry the digests written when it ran;
the result is read from the manifest that row names and re-checked against
those digests before a single comparison is made. The numbers a verdict is
rendered against are now recovered, never passed in — which is why the
previously-supported direct-call path (`run_postflush_gate.py`) does not
compile against this signature. That path is exactly where the binding was
missing.
"""

import os

from proofhouse.core.amend import check_rerun_lock
from proofhouse.core.gate import compute_foreclosure, evaluate_gate
from proofhouse.core.manifest import manifest_digest, result_digest


# Kinds a gate row may be written under.
#
#   gate        — DECISION-GRADE. At most one per (entry, source eval run).
#   maintenance — an explicit replay of an already-recorded verdict, logged
#                 and excluded from the multiplicity count by
#                 _EXCLUDED_KINDS. Re-rendering a recorded verdict to
#                 inspect it is legitimate; doing it silently, so the ledger
#                 carries two indistinguishable decision rows for one
#                 decision, is not.
_DECISION_KIND = "gate"
_REPLAY_KIND = "maintenance"


class ProvenanceRefused(Exception):
    """The result offered to the gate is not bound to a recorded eval run.

    Distinct from GateRefusal (the SPECIFICATION is malformed) and from
    GateExecutionError (the PIPELINE did not deliver a statistic). This one
    says the numbers cannot be traced to a run that produced them — which is
    not a verdict of any kind, so nothing is recorded.
    """


def required_statistics(entry):
    """Every statistic name this entry's gate will ask for.

    Mirrors evaluate_gate's resolution — a criterion's own `statistic`, or
    `gate.statistic` as the default — so callers can assemble exactly what
    the verdict needs instead of guessing.
    """
    gate = entry.get("gate") or {}
    default = gate.get("statistic")
    names = set()
    for crit in (gate.get("criteria") or {}).values():
        if not isinstance(crit, dict):
            continue
        name = crit.get("statistic") or default
        if name:
            names.add(name)
    return names


def statistics_from_result(entry, scored, allow_metadata=False):
    """Assemble the {statistic: ScoredResult} a multi-criterion gate needs.

    A manifest stores ONE result, and every CLI path built
    `{scored.statistic: scored}` from it. A gate whose criteria name
    different statistics was therefore unrunnable AND unreportable through
    the supported path: the second criterion came back "never produced",
    which is the pipeline-failure branch, so it raised rather than
    rendering. Three call sites, one assumption, and the first real
    two-statistic gate hit it.

    SECONDARY STATISTICS COME FROM `secondary_statistics`, NOT METADATA.
    They used to come from metadata, on the argument that metadata sits in
    the same recorded manifest as the headline value and is therefore
    equally trustworthy. The trust was never the problem — the ENCODING
    was. `ScoredResult.to_dict` deliberately does not canonicalize metadata,
    because canonicalizing an open dict is irreversible on read, and its
    stated licence for that was "nothing in it is compared against a
    threshold, so no verdict can turn on an ulp of it." Reading a gate
    criterion out of it revoked that licence: `mean_net_bps_per_event` was
    compared against a registered threshold of 25 while living as a bare
    float in an uncanonicalized dict. The type's own comment named the
    remedy, and `secondary_statistics` is it.

    A companion still carries no p-value and no benchmark sweep. Those
    belong to the statistic that was actually scored; a secondary statistic
    is a measurement from the same run, not a second primary.

    Args:
        entry: The registered entry, for its gate's statistic names.
        scored: The ScoredResult recovered from the manifest.
        allow_metadata: Fall back to `metadata` for a statistic that is not
            first-class. THE ONLY legitimate caller is the report's legacy
            path, rendering a verdict recorded before `secondary_statistics`
            existed — and it must disclose that it did so. Never true on a
            path that records anything.

    Raises:
        ProvenanceRefused: The statistic exists only in metadata and
            allow_metadata is False. A gating value in an uncanonicalized
            open dict is not a bound number.
        KeyError: The statistic is genuinely absent. Refusing here, where the
            cause is legible, beats letting evaluate_gate report a missing
            statistic as a broken pipeline.
    """
    from proofhouse.eval.types import ScoredResult

    results = {scored.statistic: scored}
    first_class = getattr(scored, "secondary_statistics", None) or {}
    metadata = scored.metadata or {}
    missing = []
    metadata_only = []

    for name in sorted(required_statistics(entry)):
        if name in results:
            continue
        if name in first_class:
            results[name] = ScoredResult(
                statistic=name,
                value=first_class[name],
                trial_count=scored.trial_count,
            )
        elif name in metadata:
            metadata_only.append(name)
            if allow_metadata:
                results[name] = ScoredResult(
                    statistic=name,
                    value=metadata[name],
                    trial_count=scored.trial_count,
                )
        else:
            missing.append(name)

    if metadata_only and not allow_metadata:
        raise ProvenanceRefused(
            "The gate compares statistic(s) {} against registered "
            "thresholds, and the result carries them only in `metadata`, "
            "which is not canonicalized — a float there can move by an ulp "
            "on a YAML round-trip and flip a strict comparison at a "
            "boundary. Score them into `secondary_statistics`, which is "
            "canonicalized like the primary value. (The result holds {!r} "
            "plus first-class secondaries {}.)".format(
                metadata_only, scored.statistic, sorted(first_class))
        )

    if missing:
        raise KeyError(
            "The gate names statistic(s) {} that the recorded result does "
            "not carry. The manifest holds {!r} plus secondary statistics {} "
            "and metadata keys {}. Record the missing statistic in the "
            "result's secondary_statistics at scoring time — the gate cannot "
            "be rendered without it.".format(
                missing, scored.statistic, sorted(first_class),
                sorted(metadata))
        )
    return results


def find_row(ledger, run_id):
    """The ledger row with this run_id, or None.

    The ledger is the index — D-1's whole argument for `run_gate` taking a
    run_id rather than a manifest object is that it forces the row to exist
    before anything can be gated on it.
    """
    for row in ledger.all_rows():
        if row.get("run_id") == run_id:
            return row
    return None


def load_bound_result(entry_id, source_run_id, ledger, manifests_dir):
    """Recover the recorded result a verdict must be rendered against.

    Every refusal below is a link in the chain *this exact registered entry,
    evaluated in this exact run, produced these exact numbers*. A caller
    cannot supply the numbers, so the only way to move them is to move a
    record that is hashed and chained.

    Returns:
        (ScoredResult, eval_row, manifest)

    Raises:
        ProvenanceRefused: The run does not exist, is not an eval run for
            this entry, has no manifest, predates the digests, or its
            manifest no longer matches them.
    """
    import yaml

    from proofhouse.eval.types import ScoredResult

    row = find_row(ledger, source_run_id)
    if row is None:
        raise ProvenanceRefused(
            "No ledger row {}. A verdict is rendered against a recorded eval "
            "run, and the ledger is the index of those runs — a result with "
            "no row never happened as far as the record is "
            "concerned.".format(source_run_id)
        )

    if row.get("kind") not in ("eval", "subperiod"):
        raise ProvenanceRefused(
            "Ledger row {} is kind={!r}, not an eval run. A gate reads the "
            "measurement, not another verdict.".format(
                source_run_id, row.get("kind"))
        )

    if row.get("registry_ref") != entry_id:
        # The v1 formulation matched (entry, code_hash, data_hash), which let
        # a successor gate on its parent's run. It cannot: the successor's
        # blocks are consumed by the run that scored them, so a successor
        # gating on someone else's eval would render a verdict on data it
        # never spent anything to see.
        raise ProvenanceRefused(
            "Ledger row {} is an eval run for {!r}, not for {!r}. A verdict "
            "must be rendered against a run of THIS entry — a successor "
            "gating on its parent's measurement would be claiming a result "
            "it never ran.".format(
                source_run_id, row.get("registry_ref"), entry_id)
        )

    path = os.path.join(manifests_dir or "", "{}.yaml".format(source_run_id))
    if not os.path.exists(path):
        raise ProvenanceRefused(
            "Ledger row {} names no manifest on disk ({} is missing). §5.3: "
            "a result without a manifest is not a result.".format(
                source_run_id, path)
        )
    with open(path) as fh:
        manifest = yaml.safe_load(fh) or {}

    result = manifest.get("result")
    if not result:
        raise ProvenanceRefused(
            "Manifest {} records no result. It predates Phase 2b and cannot "
            "be gated on.".format(path)
        )

    recorded_result_hash = row.get("result_hash")
    recorded_manifest_hash = row.get("manifest_hash")
    if not recorded_result_hash or not recorded_manifest_hash:
        # UNVERIFIABLE, and therefore ungatable. A legacy row's numbers are
        # not in the hash chain, so "these are the numbers that run
        # produced" rests on the file not having been edited — which is the
        # assumption the whole change exists to remove. `verify` reports such
        # rows rather than passing them silently, and `report` renders them
        # with the gap stated; what nothing does is render a NEW verdict
        # against them.
        raise ProvenanceRefused(
            "Ledger row {} carries no result_hash/manifest_hash, so its "
            "manifest is not bound to the chain and a verdict rendered "
            "against it would be unverifiable. Re-run the eval under the "
            "current tool to gate on it.".format(source_run_id)
        )

    actual_result_hash = result_digest(result)
    if actual_result_hash != recorded_result_hash:
        raise ProvenanceRefused(
            "Manifest {} does not match the result_hash recorded on ledger "
            "row {}: the row says {} and the file hashes to {}. THE FILE "
            "MOVED — the row is chained and cannot be edited without "
            "breaking every row after it, so the numbers on disk are not the "
            "numbers that run produced.".format(
                path, source_run_id, recorded_result_hash, actual_result_hash)
        )

    actual_manifest_hash = manifest_digest(manifest)
    if actual_manifest_hash != recorded_manifest_hash:
        raise ProvenanceRefused(
            "Manifest {} does not match the manifest_hash recorded on ledger "
            "row {}: the row says {} and the file hashes to {}. The result "
            "block still matches, so what changed is the provenance around "
            "it (code hash, data hash, seed, execution mode) — which decides "
            "what the numbers are a measurement OF.".format(
                path, source_run_id, recorded_manifest_hash,
                actual_manifest_hash)
        )

    return ScoredResult.from_dict(result), row, manifest


def _prior_decision_row(ledger, entry_id, source_run_id, eval_row):
    """A gate row that already recorded a verdict for this (entry, source).

    Point 7. `run_gate` appends its row only after `evaluate_gate` returns,
    so a pipeline failure never writes one and ANY existing gate row means a
    verdict was recorded. A second decision-grade row for the same
    measurement is therefore ledger duplication: two rows, one decision,
    and nothing in the record saying which is the decision.

    Legacy gate rows carry no source_eval_run_id, so for those the only
    evidence available is the dedup key the ledger already uses —
    (code_hash, data_hash) against the same entry. Matching on it is
    deliberately conservative: it can only ever cause a REFUSAL, and the
    refusal is answered by asking for the replay explicitly.
    """
    for row in ledger.all_rows():
        if row.get("kind") != _DECISION_KIND:
            continue
        if row.get("registry_ref") != entry_id:
            continue
        recorded_source = row.get("source_eval_run_id")
        if recorded_source == source_run_id:
            return row
        if recorded_source is None and (
                row.get("code_hash") == eval_row.get("code_hash")
                and row.get("data_hash") == eval_row.get("data_hash")):
            return row
    return None


def run_gate(entry, source_run_id, ledger, manifests_dir,
             amendments=None, block_registry=None, derived_inputs=None,
             kind=_DECISION_KIND):
    """Check the lock, recover the bound result, render the verdict, record it.

    Args:
        entry: The registered entry dict, including its `hash`.
        source_run_id: The eval run whose recorded result is gated. Its row
            must exist, belong to this entry, and carry the manifest digests;
            the result is read from its manifest and re-checked against them.
        ledger: Ledger instance. Read for prior gate rows, appended to on
            success.
        manifests_dir: Directory the run's manifest lives in.
        amendments: Amendment records for this entry (AmendmentLog.for_entry).
        block_registry: Optional, for the E2 anti-circularity re-check.
        derived_inputs: Optional null-distribution values for derived
            thresholds.
        kind: 'gate' (decision-grade, at most one per source run) or
            'maintenance' (an explicit replay, excluded from the
            multiplicity count).

    Returns:
        (Verdict, run_id).

    Raises:
        RerunRefused: The window was already spent under a different
            specification. Nothing is recorded.
        ProvenanceRefused: The result is not bound to a recorded eval run for
            this entry, or a decision-grade verdict already exists for this
            (entry, source run). Nothing is recorded.
        GateRefusal: The gate specification is malformed. Nothing is recorded.
        GateExecutionError: The pipeline did not deliver a statistic. Nothing
            is recorded — pipeline failure is not a gate outcome (§2.5).
    """
    entry_id = entry.get("id")
    blocks = list(entry.get("validation_blocks") or [])

    if kind not in (_DECISION_KIND, _REPLAY_KIND):
        raise ValueError(
            "run_gate kind must be {!r} or {!r}; got {!r}".format(
                _DECISION_KIND, _REPLAY_KIND, kind)
        )

    # 1. The lock, before anything else. A refusal must cost nothing.
    check_rerun_lock(
        entry_id=entry_id,
        requested_blocks=blocks,
        amendments=amendments or [],
        ledger_rows=ledger.all_rows(),
    )

    # 2. The source. Refused unless the numbers are recoverable from a
    #    recorded run of this entry and still match the digests written when
    #    it ran. Nothing is consumed and nothing is written by any refusal
    #    here — reads only.
    scored, eval_row, _ = load_bound_result(
        entry_id, source_run_id, ledger, manifests_dir)

    # 3. One decision per measurement.
    if kind == _DECISION_KIND:
        prior = _prior_decision_row(ledger, entry_id, source_run_id, eval_row)
        if prior is not None:
            raise ProvenanceRefused(
                "{} already carries a decision-grade verdict for {} "
                "(outcome {!r}). A gate row is written only after a verdict "
                "is reached, so one already exists — a second would leave the "
                "ledger with two rows for one decision and nothing saying "
                "which is the decision. Re-render it as an explicit replay "
                "(kind={!r}), which is logged and excluded from the "
                "multiplicity count.".format(
                    prior.get("run_id"), source_run_id, prior.get("outcome"),
                    _REPLAY_KIND)
            )

    # 4. The verdict. Any raise here leaves the ledger untouched, which is the
    #    point: a verdict is only ever recorded when one was actually reached.
    verdict = evaluate_gate(
        entry,
        statistics_from_result(entry, scored),
        block_registry=block_registry,
        derived_inputs=derived_inputs,
    )

    # 5. Record it. code_hash/data_hash come FROM THE EVAL ROW, not from the
    #    caller. The ledger dedups on (code_hash, data_hash, universe), so
    #    reusing them is what stops the gate row counting as a second trial
    #    on top of the eval that earned it. Inventing new hashes here would
    #    inflate the multiplicity count, and every deflated-Sharpe threshold
    #    reads from that count.
    ledger.append_row(
        code_hash=eval_row.get("code_hash"),
        data_hash=eval_row.get("data_hash"),
        universe=(entry.get("universe") or {}).get("id"),
        kind=kind,
        blocks_touched=blocks,
        stats_scored=sorted({c.statistic for c in verdict.criteria}),
        registry_ref=entry_id,
        outcome="pass" if verdict.passed else "fail",
        # The entry version this verdict was rendered against. Paired with an
        # amendment's entry_hash_after, this is what makes "the amendment was
        # logged before the code ran" checkable after the fact rather than
        # asserted in prose. check_amendment_ordering reads exactly this
        # field, and it must be inside the hashed payload — set here rather
        # than on the returned row, or the stored hash would disagree with
        # the row's contents and break the chain on the next verify.
        entry_hash=entry.get("hash"),
        # WHICH RUN, AND WHICH NUMBERS.
        #
        # The gate row recorded pass/fail and nothing else, so the verdict
        # sat in the chain with no link to the measurement it came from and
        # no record of the values compared. `report` then re-derived
        # everything from whichever manifest was latest, which is how editing
        # a manifest could change what the tool printed with every chain
        # still clean.
        #
        # CriterionOutcome.to_dict's docstring has read "Serialize for the
        # gate ledger row" since 2b and nothing ever called it. This is that
        # call site. Its numeric fields are already canonical decimal
        # strings, so canonical rule 3 is satisfied with no conversion.
        source_eval_run_id=source_run_id,
        result_hash=eval_row.get("result_hash"),
        criteria=[c.to_dict() for c in verdict.criteria],
    )

    return verdict, ledger.all_rows()[-1]["run_id"]


def foreclosure_advisory(entry, outcomes):
    """Report which criteria could not have changed the verdict.

    A-12 proved W6+W7 could determine v11's verdict regardless of W8, letting
    W8 stay unspent and clean. A validation block is scarce, so the useful
    question is not "is this decided" — after a full evaluation it always is —
    but "which of these did I not need to spend?"

    ORDER-FREE, DELIBERATELY. An earlier version walked the criteria in order
    and reported the first prefix that forced the outcome. That was wrong, and
    quietly so: `evaluate_gate` iterates criteria alphabetically, so "the
    verdict was already decided after g1" was a claim about alphabetical
    position, which has no relationship to the order blocks were actually
    spent. An advisory whose entire value is "that block did not need
    spending" cannot rest on an ordering that means nothing.

    So instead: a criterion is NON-DECISIVE when flipping it alone leaves the
    verdict unchanged. That is well-defined without any ordering, and it
    answers the question directly.

    THIS MUST NEVER AUTO-STOP. A-12 is careful about why its stop was
    legitimate: *"The stop fires on arithmetic, never on a judgment that the
    result looks bad."* Acting on this stays a human decision filed as an
    amendment, exactly as A-12 was.

    Args:
        entry: The registered entry.
        outcomes: List of (criterion_name, passed), or a dict.

    Returns:
        None when every criterion was load-bearing (nothing to advise).
        Otherwise (verdict, non_decisive_names).
    """
    combination = (entry.get("gate") or {}).get("combination")
    known = dict(outcomes)
    if not known:
        return None

    verdict = compute_foreclosure(combination, known)
    if verdict is None:
        return None

    non_decisive = []
    for name in sorted(known):
        flipped = dict(known)
        flipped[name] = not flipped[name]
        if compute_foreclosure(combination, flipped) == verdict:
            non_decisive.append(name)

    if not non_decisive:
        return None
    return verdict, non_decisive
