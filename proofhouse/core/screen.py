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


def screen_stage1a(entries, scored_results, fdr_q, fdr_method):
    """Stage 1a magnitude screen — FDR filter, then rank by magnitude.

    Runs on E/S1/S2 blocks only; never V (enforced upstream by
    run_screen passing stage=1 to run_eval).

    FDR at q≈0.3 is enrichment, not proof: survivors have earned a second
    look, not a claim. Part B plan §2.

    Args:
        entries: List of entry dicts (unused here; kept for symmetry with
                 screen_stage1b and for future per-entry rules).
        scored_results: Dict of {entry_id: ScoredResult}. Every result must
                        carry a p_value.
        fdr_q: FDR q-level. Required, no default — see run_screen.
        fdr_method: 'bh' or 'by'. Required, no default (V-14).

    Returns:
        Dict with:
            survivors: entry IDs that pass FDR, ranked by |value| descending
            fdr: the full result dict from the FDR procedure (m, k,
                 threshold, method, q — everything the screen record needs)

    Raises:
        ValueError: If any result lacks a p_value, or fdr_method is unknown.
    """
    from proofhouse.eval.fdr import benjamini_hochberg, benjamini_yekutieli

    procedures = {"bh": benjamini_hochberg, "by": benjamini_yekutieli}
    if fdr_method not in procedures:
        raise ValueError(
            "fdr_method must be one of {}; got {!r}".format(
                sorted(procedures), fdr_method
            )
        )

    pairs = []
    for entry_id, result in scored_results.items():
        if result.p_value is None:
            raise ValueError(
                "entry {!r} has no p_value. The batch cannot be screened: "
                "dropping it would shrink m, which changes every BH "
                "threshold and therefore the survivor set.".format(entry_id)
            )
        pairs.append((entry_id, result.p_value))

    fdr = procedures[fdr_method](pairs, fdr_q)
    surviving = set(fdr["rejected"])

    # Rank survivors by magnitude. FDR decides WHO passes; magnitude only
    # orders them.
    ranked = sorted(
        (eid for eid in scored_results if eid in surviving),
        key=lambda eid: abs(scored_results[eid].value),
        reverse=True,
    )

    return {"survivors": ranked, "fdr": fdr}


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


def run_screen(entries, adapters, block_registry, ledger, score_fn,
               manifests_dir, fdr_q, screen_stage="1a",
               sub_period_adapters=None, seed=None):
    """Screen a batch of hypotheses. The ONLY public way to run a screen.

    Mirrors run_eval: fixed order, every guarantee inside it.

        1. Validate the batch      (three refusals — see below)
        2. Run each entry          via run_eval, inheriting every guarantee
        3. Collect p-values        refuse the batch if any is None
        4. Apply FDR               method from the entries, m = batch size
        5. Rank / filter           screen_stage1a or 1b
        6. Record the screen       ledger row, kind='screen'
        7. Return survivors        with the threshold attached

    Screening ALWAYS runs at eval stage 1, so block isolation refuses any
    V-block outright. Screening never touches validation data.

    Per-hypothesis multiplicity is counted by each run_eval's own row; this
    function adds no trials.

    Three refusals (Part C plan §2.2):

      Mixed fdr_method  -> refuse. FDR correction applies to the FAMILY.
          A batch where one entry declares 'bh' and another 'by' has no
          coherent interpretation: there is no single procedure being run.

      Mixed sidedness   -> ALLOWED. Sidedness is per-hypothesis and affects
          only that hypothesis's p-value; BH consumes p-values without
          caring how each was derived. The asymmetry with fdr_method is
          deliberate — "both are registered fields, treat them alike" is
          the plausible-but-wrong move.

      Any p_value None  -> refuse the BATCH, not the entry. Dropping the
          entry would shrink m, changing every threshold and therefore the
          survivor set (determinism rule 3.3).

    Args:
        entries: List of registry entry dicts.
        adapters: Dict of {entry_id: adapter}. Every entry must be present.
        block_registry: BlockRegistry instance.
        ledger: Ledger instance.
        score_fn: Callable(predictions, outcomes, trial_count) ->
                  (ScoredResult, Diagnostics). Must set p_value.
        manifests_dir: Where run_eval persists manifests.
        fdr_q: FDR q-level. A runtime parameter by decision, not a
               registered field — a screen is enrichment, not proof, and
               shrinking a batch after seeing results is sometimes correct.
               Every screen is recorded, so q-shopping is visible in the
               ledger rather than prevented.
        screen_stage: '1a' (magnitude) or '1b' (consistency).
        sub_period_adapters: For '1b', dict of {entry_id: [adapter, ...]}.
                             Each sub-period run is logged as kind='subperiod'
                             linked to its parent eval row, and excluded from
                             the trial count — k entries over s sub-periods is
                             k trials, not k*s.
        seed: Optional seed passed to run_eval.

    Returns:
        Dict with: survivors, fdr (m/k/threshold/method/q), screen_run_id,
        eval_run_ids, scored_results.

    Raises:
        ValueError: On any of the three refusals, or an unknown screen_stage.
    """
    from proofhouse.eval.runner import run_eval

    if screen_stage not in ("1a", "1b"):
        raise ValueError(
            "screen_stage must be '1a' or '1b'; got {!r}".format(screen_stage)
        )
    if not entries:
        raise ValueError("run_screen requires a non-empty batch")

    # ---- Step 1: Validate the batch ----
    methods = {e.get("fdr_method") for e in entries}
    if len(methods) > 1:
        raise ValueError(
            "Batch declares mixed fdr_method {}. FDR correction applies to "
            "the family, not the individual — a batch running two procedures "
            "has no coherent interpretation. Split it or align the entries."
            .format(sorted(m for m in methods if m))
        )
    fdr_method = methods.pop()
    if not fdr_method:
        raise ValueError(
            "Batch entries declare no fdr_method. Required, no default (V-14)."
        )

    missing = [e["id"] for e in entries if e["id"] not in adapters]
    if missing:
        raise ValueError(
            "No adapter supplied for {}. Every entry in the batch must run — "
            "silently skipping one would shrink m.".format(missing)
        )

    # Mixed universes -> refuse, for the same reason as mixed fdr_method.
    # Trial counts are PER-UNIVERSE (rule 7b), so entries from different
    # universes carry different multiplicity budgets and do not belong to
    # one FDR family. The screen record also names a single universe, so a
    # mixed batch would be recorded under whichever entry happened to be
    # first — a silently wrong record, which is worse than a refusal.
    universes = {e["universe"]["id"] for e in entries}
    if len(universes) > 1:
        raise ValueError(
            "Batch spans multiple universes {}. Trial counts are per-universe, "
            "so these entries carry different multiplicity budgets and are not "
            "one FDR family. Screen them separately.".format(sorted(universes))
        )
    universe = universes.pop()

    # ---- Step 2: Run each entry ----
    scored_results = {}
    eval_run_ids = {}
    for entry in entries:
        entry_id = entry["id"]
        result = run_eval(
            entry, adapters[entry_id], block_registry, ledger,
            stage=1, score_fn=score_fn, manifests_dir=manifests_dir, seed=seed,
        )
        scored_results[entry_id] = result.scored
        eval_run_ids[entry_id] = result.run_id

    # ---- Step 2b: Sub-period runs for the consistency screen ----
    sub_period_results = {}
    if screen_stage == "1b" and sub_period_adapters:
        for entry in entries:
            entry_id = entry["id"]
            slices = []
            for sub_adapter in sub_period_adapters.get(entry_id, []):
                sub = run_eval(
                    entry, sub_adapter, block_registry, ledger,
                    stage=1, score_fn=score_fn, manifests_dir=manifests_dir,
                    seed=seed, ledger_kind="subperiod",
                    parent_run_id=eval_run_ids[entry_id],
                )
                # screen_stage1b compares these against zero for sign
                # stability, so it needs the VALUE, not the ScoredResult.
                slices.append(sub.scored.value)
            sub_period_results[entry_id] = slices

    # ---- Steps 3-5: FDR, then rank/filter ----
    stage1a = screen_stage1a(entries, scored_results, fdr_q, fdr_method)
    fdr = stage1a["fdr"]
    survivors = stage1a["survivors"]

    if screen_stage == "1b":
        # screen_stage1b takes entry IDs, not entry dicts — its first loop
        # uses them as dict keys.
        consistent = screen_stage1b(
            list(survivors),
            {k: v for k, v in scored_results.items() if k in set(survivors)},
            sub_period_results,
        )
        survivors = [eid for eid in survivors if eid in set(consistent)]

    # ---- Step 6: Record the screen ----
    # BH's threshold depends on every p-value in the batch, so a survivor
    # list alone is unfalsifiable. m, q, method and threshold go in the
    # record. Floats are stored as canonical decimal strings (rule 3).
    screen_run_id = ledger.append_screen(
        batch=[e["id"] for e in entries],
        m=fdr["m"],
        q=fdr_q,
        method=fdr_method,
        threshold=fdr["threshold"],
        survivors=survivors,
        screen_stage=screen_stage,
        universe=universe,
    )

    return {
        "survivors": survivors,
        "fdr": fdr,
        "screen_run_id": screen_run_id,
        "eval_run_ids": eval_run_ids,
        "scored_results": scored_results,
    }
