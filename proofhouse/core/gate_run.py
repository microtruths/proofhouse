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


def foreclosure_advisory(entry, ordered_outcomes):
    """Find the earliest point at which the verdict was already decided.

    A-12 proved W6+W7 could determine v11's verdict regardless of W8, letting
    W8 stay unspent and clean. A validation block is scarce, so the useful
    question is not "is this decided" — after a full evaluation it always is —
    but "how much of this did I not need to spend?"

    So this walks the criteria in order and reports the first prefix that
    forces the outcome. Reported against a completed evaluation it is
    retrospective ("g2 and g3 could not have changed it"); reported mid-cycle
    it is the live A-12 case.

    THIS MUST NEVER AUTO-STOP. A-12 is careful about why its stop was
    legitimate: *"The stop fires on arithmetic, never on a judgment that the
    result looks bad."* Acting on this stays a human decision filed as an
    amendment, exactly as A-12 was.

    Args:
        entry: The registered entry.
        ordered_outcomes: List of (criterion_name, passed) in evaluation
            order.

    Returns:
        None when no proper prefix decides it — i.e. every criterion was load-
        bearing. Otherwise (verdict, decided_after, not_needed): the forced
        verdict, the criterion name that settled it, and the names that could
        not have changed it.
    """
    combination = (entry.get("gate") or {}).get("combination")
    names = [name for name, _ in ordered_outcomes]

    known = {}
    for index, (name, passed) in enumerate(ordered_outcomes):
        known[name] = passed
        forced = compute_foreclosure(combination, known)
        if forced is None:
            continue
        remaining = names[index + 1:]
        if not remaining:
            # Decided only by the last criterion: everything was needed, so
            # there is nothing to advise about.
            return None
        return forced, name, remaining
    return None
