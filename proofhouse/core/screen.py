# proofhouse.core.screen — two-stage screening engine
"""
Phase 1 Build Plan §1b — Screening Engine.

Parent plan §2.3:
    Stage 1a — Magnitude screen (FDR q≈0.3)
    Stage 1b — Consistency screen (hit rate, sign stability)

Block isolation (§5.2):
    A stage-1 row touching segment V is a HARD ERROR.

Stage-aware counting:
    Stage-1 rows excluded from stage-2 multiplicity correction.

Transitive contamination:
    Amended descendants of a contaminated entry inherit the contamination.

Winner's curse:
    expected_effect_size registered BEFORE screening. Updating after
    seeing stage-1 results is an amendment requiring new validation window.
"""


def check_contamination(entry_id, ledger, registry=None):
    """Check whether an entry is contaminated (touched a V-block in stage 1).

    Contamination is transitive: if an entry's ancestor (via depends_on
    or amendment chain) is contaminated, the entry inherits it. This
    prevents contamination laundering through amendments.

    Args:
        entry_id: The entry ID to check.
        ledger: Ledger instance.
        registry: Optional Registry instance (for amendment chain traversal).

    Returns:
        Dict with:
            contaminated: bool
            reason: str (empty if not contaminated)
    """
    # Collect all rows for this entry
    for row in ledger.all_rows():
        if row.get("registry_ref") != entry_id:
            continue

        # Check if any stage-1 row touched a V-segment block
        row_stage = row.get("stage")
        row_segment = row.get("segment")

        if row_stage == 1 and row_segment == "V":
            return {
                "contaminated": True,
                "reason": "Entry {} touched V-block {} in stage 1 (row {})".format(
                    entry_id, row.get("blocks_touched", []), row.get("run_id")
                ),
            }

    # Check transitive contamination via depends_on
    if registry is not None:
        try:
            entry = registry.get_entry(entry_id)
        except KeyError:
            entry = None

        if entry and entry.get("depends_on"):
            for dep_id in entry["depends_on"]:
                dep_result = check_contamination(dep_id, ledger, registry)
                if dep_result["contaminated"]:
                    return {
                        "contaminated": True,
                        "reason": (
                            "Entry {} inherits contamination from dependency {}. "
                            "{}".format(entry_id, dep_id, dep_result["reason"])
                        ),
                    }

    return {"contaminated": False, "reason": ""}


def check_effect_size_amendment(entry, original_entry):
    """Check whether expected_effect_size was updated after stage-1.

    Winner's curse: updating expected_effect_size after seeing stage-1
    results is an amendment that invalidates the pre-registration. A
    new validation window is required.

    AgentCopy measured this: train alpha +4,620 bps → test alpha −424 bps.

    Args:
        entry: The current entry.
        original_entry: The original (pre-amendment) entry.

    Returns:
        Dict with:
            amended: bool — True if effect size changed.
            original: The original effect size.
            current: The current effect size.
    """
    orig_effect = (original_entry.get("mechanism", {})
                   .get("expected_effect_size"))
    curr_effect = (entry.get("mechanism", {})
                   .get("expected_effect_size"))

    return {
        "amended": orig_effect != curr_effect,
        "original": orig_effect,
        "current": curr_effect,
    }


def check_polarity_amendment(entry, original_entry):
    """Check whether signal.parameters.polarity was changed after registration.

    One-sided p-values are only legitimate when the direction was fixed
    before the data was seen. The standard objection to one-sided testing
    is "you would have flipped the sign if it came out the other way" —
    this check is what forecloses it.

    The project has been burned on direction twice already:
      - failure catalog #23: a borrowed method's payoff-ratio polarity was
        inverted and had NEGATIVE IC on this data, actively destroying the
        composite.
      - failure catalog #17: a justification written for a universe-pooled
        beta survived the change to per-wallet beta, where sign flips
        change direction-adjusted beta systematically.

    A polarity change is therefore an amendment that invalidates the
    pre-registration; enforcement of the fresh-validation-window
    requirement lives in Registry.register().

    Args:
        entry: The current entry.
        original_entry: The original (pre-amendment) entry.

    Returns:
        Dict with:
            amended: bool — True if polarity changed (including being
                     added or removed).
            original: The original polarity, or None if absent.
            current: The current polarity, or None if absent.
    """
    _MISSING = object()

    orig = (original_entry.get("signal", {})
            .get("parameters", {})
            .get("polarity", _MISSING))
    curr = (entry.get("signal", {})
            .get("parameters", {})
            .get("polarity", _MISSING))

    # Absent in both is not an amendment. Present in one only is.
    if orig is _MISSING and curr is _MISSING:
        return {"amended": False, "original": None, "current": None}

    return {
        "amended": orig != curr,
        "original": None if orig is _MISSING else orig,
        "current": None if curr is _MISSING else curr,
    }


def screen_stage1a(entries, scored_results, fdr_q=0.3):
    """Stage 1a magnitude screen — rank by effect size, FDR filter.

    Runs on E/S1 blocks only. Ranks hypotheses by magnitude.
    FDR correction at q≈0.3 (enrichment, not proof).

    Current implementation: magnitude-rank only. The Benjamini-Hochberg
    procedure requires p-values from the eval engine (via t-stats and SE),
    which are not yet available in the ScoredResult interface. When they
    are, this function will apply BH at the configured q-level.

    For now, all entries with non-zero magnitude pass. This is the
    permissive direction — it lets more through to stage 1b, where
    the consistency screen does the actual filtering.

    Args:
        entries: List of entry dicts.
        scored_results: Dict of {entry_id: ScoredResult}.
        fdr_q: FDR q-level for screening (default 0.3). Currently stored
               but not applied until p-values are available.

    Returns:
        List of entry IDs that survive, ranked by magnitude (descending).
    """
    # Rank by absolute value of the primary statistic
    ranked = sorted(
        scored_results.items(),
        key=lambda kv: abs(kv[1].value),
        reverse=True,
    )

    # TODO: Apply BH procedure when ScoredResult carries p-values.
    # BH threshold for rank i: (i / n) * fdr_q
    # Reject H0 for all i ≤ max{i : p_i ≤ (i/n) * q}
    survivors = [
        entry_id for entry_id, result in ranked
        if abs(result.value) > 0
    ]

    return survivors


def screen_stage1b(entries, scored_results, sub_period_results):
    """Stage 1b consistency screen — sign stability across sub-periods.

    Survivors of 1a only. Checks hit rate and sign stability
    across sub-period splits.

    Args:
        entries: List of entry dicts (1a survivors).
        scored_results: Dict of {entry_id: ScoredResult}.
        sub_period_results: Dict of {entry_id: list of sub-period values}.

    Returns:
        List of entry IDs that survive.
    """
    survivors = []

    for entry_id in entries:
        if entry_id not in sub_period_results:
            continue

        sub_values = sub_period_results[entry_id]
        if not sub_values:
            continue

        # Sign consistency: fraction of sub-periods with same sign as overall
        overall_sign = 1 if scored_results[entry_id].value > 0 else -1
        consistent = sum(1 for v in sub_values if (v > 0) == (overall_sign > 0))
        consistency_rate = consistent / len(sub_values)

        # Require >50% consistency (better than random)
        if consistency_rate > 0.5:
            survivors.append(entry_id)

    return survivors
