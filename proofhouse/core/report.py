# proofhouse.core.report — the findings memo
"""
Phase 2.5 §5.2.

CONTENT IS COMMITTED, LAYOUT IS PROVISIONAL (2c decision §7.2). The first two
Workstream-A audits are meant to become this format's spec and have not
happened, so rendering is kept separable from content assembly: a
client-driven format change should be a template swap, not a rewrite.
`build_report` returns a structure; `render_text` is one renderer.

WHAT A REPORT MUST NOT DO
--------------------------
Soften the verdict. §2.1's promise is that 0.0495 against >= 0.05 reads as
FAIL with no adornment, and a report is exactly where that discipline
quietly stops holding — "just under threshold" reads as helpful and undoes
the whole point. There is no proximity language here and no percentage-of-
target.

WHY ERRATA HAVE A PLACE IN THE OUTPUT
--------------------------------------
Both v11 source documents — V10_CYCLE_REPORT.md and W6_VALIDATION_RESULTS.md
— carry dated errata correcting a figure no decision depended on. A report
format with nowhere to put a correction encourages silent editing, which is
the failure this whole tool exists to prevent. So corrections are a first-
class section rather than something you work around.

THE NON-CLAIMS ARE NOT DECORATION
----------------------------------
`pass_does_not_establish` is rendered on a FAIL as well as a PASS. A reader
who sees only what a result licenses, and never what it does not, will
reliably over-read it — and the non-claims are hardest to write and easiest
to drop precisely when the result is good.
"""

from proofhouse.core.gate_run import foreclosure_advisory


def build_report(entry, verdict, ledger=None, amendments=None, fills=None,
                 errata=None, scored_results=None):
    """Assemble the report structure. No rendering, no I/O.

    Args:
        entry: The registered entry (composed with its fills).
        verdict: The Verdict from evaluate_gate.
        ledger: Optional Ledger, for trial counts.
        amendments: Amendment records for this entry.
        fills: Fill records for this entry.
        errata: Optional list of {date, note} corrections.
        scored_results: The {statistic: ScoredResult} the verdict was rendered
            against. Required for the benchmark sweep — the sweep lives on
            ScoredResult, not on the verdict, because it is a property of the
            measurement rather than of the comparison.

    Returns:
        Dict with every committed content element.
    """
    gate = entry.get("gate") or {}
    universe = (entry.get("universe") or {}).get("id")

    trials = None
    stage2_trials = None
    if ledger is not None and universe:
        trials = ledger.count_trials(universe)
        stage2_trials = ledger.count_trials_stage2(universe)

    advisory = foreclosure_advisory(
        entry, [(c.name, c.passed) for c in verdict.criteria])

    return {
        "entry_id": entry.get("id"),
        "created_utc": entry.get("created_utc"),
        "mechanism_class": (entry.get("mechanism") or {}).get("class"),
        "counterparty": (entry.get("mechanism") or {}).get("counterparty"),
        "claim": (entry.get("mechanism") or {}).get("claim"),
        "universe": universe,
        "verdict": "PASS" if verdict.passed else "FAIL",
        "combination": verdict.combination,
        "criteria": [c.to_dict() for c in verdict.criteria],
        "benchmark": _benchmark_section(gate, scored_results),
        "multiplicity": {
            "universe": universe,
            "trials": trials,
            "stage2_trials": stage2_trials,
            "fdr_method": entry.get("fdr_method"),
            "fdr_method_justification": entry.get("fdr_method_justification"),
            "p_value_sidedness": entry.get("p_value_sidedness"),
        },
        "power_check": _power_section(entry),
        "history": {
            "amendments": [
                {
                    "id": a.get("amendment_id"), "number": a.get("number"),
                    "rationale": a.get("rationale"), "impact": a.get("impact"),
                    "invalidating": a.get("invalidating"),
                    "fields": [c.get("field") for c in a.get("diff") or []],
                }
                for a in sorted(amendments or [],
                                key=lambda a: a.get("number", 0))
            ],
            "fills": [
                {"id": f.get("fill_id"), "field": f.get("field"),
                 "rule": f.get("fill_rule"), "value": f.get("filled_value"),
                 "flagged": f.get("flagged")}
                for f in fills or []
            ],
        },
        "foreclosure": (
            {"verdict": "PASS" if advisory[0] else "FAIL",
             "non_decisive": advisory[1]} if advisory else None
        ),
        "licensing": {
            # Rendered on a FAIL too. A reader who sees only what a result
            # licenses and never what it does not will over-read it.
            "licenses": (entry.get("pass_licenses") or "").strip() or None,
            "does_not_establish": (
                entry.get("pass_does_not_establish") or "").strip() or None,
            "licenses_apply": verdict.passed,
        },
        "errata": list(errata or []),
        "non_claims": _non_claims(entry, verdict),
    }


def _benchmark_section(gate, scored_results):
    """The sensitivity sweep is mandatory, and its absence is stated.

    §2.2.1 found the benchmark choice can flip the sign — BTC subtraction gave
    +577 bps where an OI-weighted index gave -599 on the same data. A result
    reported against one benchmark, with no sweep, is not a finding about the
    strategy; it is a finding about the benchmark.

    THE SWEEP LIVES ON ScoredResult, NOT ON THE VERDICT. It is a property of
    the measurement, not of the comparison. The first version of this read
    `benchmark_results` off CriterionOutcome, which has no such attribute, so
    the sweep was ALWAYS empty and "SENSITIVITY SWEEP MISSING" fired
    unconditionally — including on results that had run the full sweep. The
    same defect class as the citation resolver that read the catalog as
    ending at #25: a mandatory check that always fires is a check nobody
    reads.
    """
    sweep = {}
    for result in (scored_results or {}).values():
        sweep.update(getattr(result, "benchmark_results", None) or {})

    return {
        "primary": gate.get("benchmark"),
        "justification": gate.get("benchmark_justification"),
        "declared_sensitivity": list(gate.get("benchmark_sensitivity") or []),
        "sweep_results": sweep,
        "sweep_missing": not sweep,
    }


def _power_section(entry):
    block = (entry.get("power_check") or {}).get("value") or {}
    if block.get("pending"):
        return {"status": "pending", "fill_rule": block.get("fill_rule")}
    return {
        "status": "filled",
        "value": block.get("filled"),
        "flagged": block.get("flagged"),
        "derivation": block.get("derivation") or {},
    }


def _non_claims(entry, verdict):
    """What this result does not show, stated whether or not it passed."""
    claims = []
    if not verdict.passed:
        claims.append(
            "A failed gate does not establish that the mechanism is absent — "
            "only that it did not clear the threshold registered before the "
            "data was seen, on this window.")
    infeasible = [c.name for c in verdict.criteria if c.infeasible]
    if infeasible:
        claims.append(
            "Criteria {} were infeasible (no usable statistic) and count as "
            "losses, not as absent evidence.".format(sorted(infeasible)))
    power = (entry.get("power_check") or {}).get("value", {})
    if power.get("flagged"):
        claims.append(
            "The registered design has power below 0.5: it was more likely "
            "than not to miss its own hypothesized effect even if real. A "
            "failure here is weak evidence of absence.")
    elif power.get("pending"):
        # An unfilled power check must not read like a passed one. Silence
        # here would let a reader assume the design was adequately powered,
        # which is the assumption most likely to be wrong and least likely to
        # be questioned.
        claims.append(
            "The power check was never filled, so this design's ability to "
            "detect its own hypothesized effect is unknown. A failure cannot "
            "be weighed as evidence of absence until it is.")
    if not (entry.get("gate") or {}).get("benchmark_sensitivity"):
        claims.append(
            "No benchmark sensitivity was declared, so the result cannot be "
            "distinguished from an artefact of the benchmark choice.")
    return claims


def render_text(report):
    """One renderer over the structure. Layout provisional; content is not."""
    out = []
    add = out.append

    add("=" * 70)
    add("{}  —  {}".format(report["entry_id"], report["verdict"]))
    add("=" * 70)
    add("registered : {}".format(report["created_utc"]))
    add("mechanism  : {}".format(report["mechanism_class"]))
    add("universe   : {}".format(report["universe"]))

    add("\n--- Gate ---")
    add("combination: {}".format(report["combination"]))
    for c in report["criteria"]:
        actual = "infeasible" if c.get("infeasible") else c["actual"]
        line = "  {:<6} {:<24} {} {} {}   {}".format(
            c["name"], c["statistic"], actual, c["comparator"],
            c["threshold"], "PASS" if c["passed"] else "FAIL")
        if c.get("threshold_source") == "derived":
            line += "\n         threshold derived: {}  from {}".format(
                c.get("derived_formula"), c.get("derived_source_blocks"))
        add(line)
    add("verdict: {}".format(report["verdict"]))

    if report["foreclosure"]:
        add("\nforeclosure: verdict is {} regardless of {}. If any of those "
            "cost a validation block, that block did not need spending."
            .format(report["foreclosure"]["verdict"],
                    ", ".join(report["foreclosure"]["non_decisive"])))

    bench = report["benchmark"]
    add("\n--- Benchmark ---")
    add("primary: {}".format(bench["primary"]))
    if bench["sweep_missing"]:
        add("SENSITIVITY SWEEP MISSING. Declared: {}. The benchmark choice "
            "can flip the sign of a result, so a single-benchmark number is "
            "not yet a finding about the strategy.".format(
                bench["declared_sensitivity"] or "none"))
    else:
        for name in sorted(bench["sweep_results"]):
            add("  {}: {}".format(name, bench["sweep_results"][name]))

    mult = report["multiplicity"]
    add("\n--- Multiplicity ---")
    if mult["trials"] is None:
        # "None" would read as a number to a skimming reader, and reading it
        # as zero is the worst possible misreading: the whole point of the
        # count is that it inflates the threshold a result must clear.
        add("trials on {}: NOT AVAILABLE (no ledger supplied) — every "
            "deflated-Sharpe threshold depends on this count, so a report "
            "without it is incomplete rather than clean.".format(
                mult["universe"]))
    else:
        add("trials on {}: {} (stage-2: {})".format(
            mult["universe"], mult["trials"], mult["stage2_trials"]))
    add("fdr: {} — {}".format(mult["fdr_method"],
                              mult["fdr_method_justification"]))
    add("p-value sidedness: {}".format(mult["p_value_sidedness"]))

    power = report["power_check"]
    add("\n--- Power ---")
    if power["status"] == "pending":
        add("PENDING (rule: {})".format(power["fill_rule"]))
    else:
        add("{}{}".format(power["value"],
                          "   FLAGGED (< 0.5)" if power["flagged"] else ""))

    history = report["history"]
    if history["amendments"] or history["fills"]:
        add("\n--- History ---")
        for a in history["amendments"]:
            add("  {} (#{}){}: {}".format(
                a["id"], a["number"],
                " INVALIDATING" if a["invalidating"] else "", a["rationale"]))
            add("      fields: {}".format(", ".join(a["fields"])))
        for f in history["fills"]:
            add("  {} filled {} = {}{}".format(
                f["id"], f["field"], f["value"],
                "  FLAGGED" if f["flagged"] else ""))

    if report["errata"]:
        add("\n--- Errata ---")
        for e in report["errata"]:
            add("  {}: {}".format(e.get("date"), e.get("note")))

    lic = report["licensing"]
    add("\n--- What this licenses ---")
    if lic["licenses_apply"]:
        add("  {}".format(lic["licenses"]))
    else:
        add("  Nothing. The gate did not pass.")
        add("  (Had it passed, it would have licensed: {})".format(
            lic["licenses"]))
    add("\n--- What it does NOT establish ---")
    add("  {}".format(lic["does_not_establish"]))
    for claim in report["non_claims"]:
        add("  - {}".format(claim))

    return "\n".join(out)
