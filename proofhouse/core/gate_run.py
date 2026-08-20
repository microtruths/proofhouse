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
"""

from proofhouse.core.amend import check_rerun_lock
from proofhouse.core.gate import compute_foreclosure, evaluate_gate


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


def statistics_from_result(entry, scored):
    """Assemble the {statistic: ScoredResult} a multi-criterion gate needs.

    A manifest stores ONE result, and every CLI path built
    `{scored.statistic: scored}` from it. A gate whose criteria name
    different statistics was therefore unrunnable AND unreportable through
    the supported path: the second criterion came back "never produced",
    which is the pipeline-failure branch, so it raised rather than
    rendering. Three call sites, one assumption, and the first real
    two-statistic gate hit it.

    SECONDARY STATISTICS COME FROM THE RESULT'S OWN METADATA. That is not a
    loophole: metadata is part of the same recorded, content-hashed manifest
    as the headline value, produced by the same run at the same time, so it
    is exactly as trustworthy. What it is not is the PRIMARY measurement —
    so a companion carries no p-value and no benchmark sweep, both of which
    belong to the statistic that was actually scored.

    Raises:
        KeyError: naming the statistics the gate wants and the result cannot
            supply. Refusing here, where the cause is legible, beats letting
            evaluate_gate report a missing statistic as a broken pipeline.
    """
    from proofhouse.eval.types import ScoredResult

    results = {scored.statistic: scored}
    missing = []
    for name in sorted(required_statistics(entry)):
        if name in results:
            continue
        if name in (scored.metadata or {}):
            results[name] = ScoredResult(
                statistic=name,
                value=scored.metadata[name],
                trial_count=scored.trial_count,
            )
        else:
            missing.append(name)

    if missing:
        raise KeyError(
            "The gate names statistic(s) {} that the recorded result does "
            "not carry. The manifest holds {!r} plus metadata keys {}. "
            "Record the missing statistic in the result's metadata at "
            "scoring time — the gate cannot be rendered without "
            "it.".format(missing, scored.statistic,
                         sorted(scored.metadata or {}))
        )
    return results


def run_gate(entry, scored_results, ledger, code_hash, data_hash,
             amendments=None, block_registry=None, derived_inputs=None):
    """Check the lock, render the verdict, record it.

    Args:
        entry: The registered entry dict, including its `hash`.
        scored_results: Dict of {statistic_name: ScoredResult} from the eval
            run being gated.
        ledger: Ledger instance. Read for prior gate rows, appended to on
            success.
        code_hash: Code hash of the eval run that produced scored_results.
        data_hash: Data hash of that run.
        amendments: Amendment records for this entry (AmendmentLog.for_entry).
        block_registry: Optional, for the E2 anti-circularity re-check.
        derived_inputs: Optional null-distribution values for derived
            thresholds.

    Returns:
        (Verdict, run_id).

    Raises:
        RerunRefused: The window was already spent under a different
            specification. Nothing is recorded.
        GateRefusal: The gate specification is malformed. Nothing is recorded.
        GateExecutionError: The pipeline did not deliver a statistic. Nothing
            is recorded — pipeline failure is not a gate outcome (§2.5).
    """
    entry_id = entry.get("id")
    blocks = list(entry.get("validation_blocks") or [])

    # 1. The lock, before anything else. A refusal must cost nothing.
    check_rerun_lock(
        entry_id=entry_id,
        requested_blocks=blocks,
        amendments=amendments or [],
        ledger_rows=ledger.all_rows(),
    )

    # 2. The verdict. Any raise here leaves the ledger untouched, which is the
    #    point: a verdict is only ever recorded when one was actually reached.
    verdict = evaluate_gate(
        entry,
        scored_results,
        block_registry=block_registry,
        derived_inputs=derived_inputs,
    )

    # 3. Record it. code_hash/data_hash are those of the eval run that
    #    produced these statistics — not a fresh pair. The ledger dedups on
    #    (code_hash, data_hash, universe), so reusing them is what stops the
    #    gate row counting as a second trial on top of the eval that earned
    #    it. Inventing new hashes here would inflate the multiplicity count,
    #    and every deflated-Sharpe threshold reads from that count.
    ledger.append_row(
        code_hash=code_hash,
        data_hash=data_hash,
        universe=(entry.get("universe") or {}).get("id"),
        kind="gate",
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
