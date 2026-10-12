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

WHERE THE VERDICT COMES FROM
-----------------------------
`report` used to ignore the recorded gate row entirely and re-derive the
verdict from whichever manifest was newest. A memo could therefore state a
verdict that no ledger row records, and editing a manifest changed what it
printed with every hash chain still verifying clean. `bind_verdict` reads the
recorded row, recomputes from the bound result, and refuses on disagreement —
see ReportRefused for why refusing beats rendering both.

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

from proofhouse.core.gate import evaluate_gate
from proofhouse.core.gate_run import (ProvenanceRefused, foreclosure_advisory,
                                      load_bound_result,
                                      statistics_from_result)
from proofhouse.core.manifest import latest_manifest_for


class ReportRefused(Exception):
    """The record and a fresh recomputation disagree. No memo is rendered.

    D-3. Rendering both sides would make the report the place where a
    contradiction gets presented as a matter of opinion. Refusing, and saying
    WHICH SIDE MOVED, is the only reading that keeps the record decisive:
    either the manifest changed under a chained digest, or the gate engine
    now renders the same recorded numbers differently.

    A deliberate gate-engine change therefore needs a stated procedure —
    re-render and record a fresh verdict, or leave the historical one as
    recorded and read it through its own version of the tool. Without one,
    this refusal is the check people route around.
    """


def bind_verdict(entry, ledger, manifests_dir, block_registry=None,
                 derived_inputs=None):
    """Recover the RECORDED verdict, recompute it, and refuse on disagreement.

    `report` used to ignore the gate row entirely and re-derive a verdict
    from whichever manifest happened to be latest. Two failures fell out of
    that: the memo could describe a verdict that was never recorded, and
    editing a manifest changed what it printed while every hash chain still
    verified clean.

    RECOMPUTE-AND-COMPARE, NOT ONE OR THE OTHER. Reading the recorded row
    alone would print a verdict nothing re-derives; recomputing alone is what
    the tool already did wrong. Together they catch both a tampered manifest
    (digest mismatch) and a gate-engine change that would silently re-render
    an old verdict differently (criteria mismatch) — and the two failures are
    distinguishable, which is what lets the refusal name the side that moved.

    Args:
        entry: The registered entry, composed with its fills.
        ledger: Ledger instance.
        manifests_dir: Where run manifests live.
        block_registry: Optional, for the E2 anti-circularity re-check.
        derived_inputs: Optional null-distribution values for derived
            thresholds.

    Returns:
        (verdict, scored_results, provenance) where provenance carries
        `status` — 'bound', 'unverifiable' (a recorded verdict predating the
        binding) or 'unrecorded' (no gate row at all) — plus what was read.

    Raises:
        ReportRefused: The record and the recomputation disagree, or there is
            no result to report on at all.
    """
    entry_id = entry.get("id")
    row = _latest_gate_row(ledger, entry_id)

    if row is not None and row.get("source_eval_run_id") and row.get("criteria"):
        return _bound(entry, row, ledger, manifests_dir, block_registry,
                      derived_inputs)

    # LEGACY, AND SAID SO. R-000013 has no source run and no criterion
    # outcomes; there is nothing to compare a recomputation against, so the
    # memo falls back to the unbound lookup the tool used to use everywhere —
    # and states that it did. Refusing here would make the tool unable to
    # report on its own history, which is not integrity, and rendering
    # silently would let an unverifiable memo read as a verified one.
    manifest = latest_manifest_for(manifests_dir, entry_id)
    if manifest is None:
        raise ReportRefused(
            "No manifest with a recorded result for {}. There is nothing to "
            "report on.".format(entry_id))

    from proofhouse.eval.types import ScoredResult
    scored = ScoredResult.from_dict(manifest["result"])
    # allow_metadata: this manifest may predate `secondary_statistics`, and
    # its secondary gating value lives in the uncanonicalized metadata dict.
    # Permitted ONLY here, on a path that records nothing and discloses the
    # gap — never where a verdict is written.
    results = statistics_from_result(entry, scored, allow_metadata=True)
    verdict = evaluate_gate(entry, results, block_registry=block_registry,
                            derived_inputs=derived_inputs)

    if row is None:
        status, note = "unrecorded", (
            "No gate row for this entry. The verdict below was computed now, "
            "from manifest {}, and is NOT a record of a decision that was "
            "taken — nothing in the ledger says this gate was ever "
            "rendered.".format(manifest.get("run_id")))
    else:
        status, note = "unverifiable", (
            "Gate row {} predates the result binding: it records pass/fail "
            "with no source eval run and no criterion values. The verdict "
            "below was recomputed from manifest {}, found by the unbound "
            "'latest manifest for this entry' lookup, so the numbers rest on "
            "that file not having been edited rather than on a digest. "
            "Unverifiable — neither confirmed nor contradicted.".format(
                row.get("run_id"), manifest.get("run_id")))

    return verdict, results, {
        "status": status,
        "gate_run_id": row.get("run_id") if row else None,
        "source_eval_run_id": None,
        "manifest_run_id": manifest.get("run_id"),
        "result_hash": None,
        "note": note,
    }


def _latest_gate_row(ledger, entry_id):
    """The row whose verdict this memo is about.

    DECISION-GRADE FIRST. A kind='maintenance' replay re-renders a verdict
    that a kind='gate' row already recorded, so when both exist the decision
    row is the one the memo is about. A replay is used only when no decision
    row exists, which happens for a verdict recorded before this distinction
    did.
    """
    if ledger is None:
        return None
    decisions = [r for r in ledger.all_rows()
                 if r.get("kind") == "gate" and r.get("registry_ref") == entry_id]
    if decisions:
        return decisions[-1]
    replays = [r for r in ledger.all_rows()
               if r.get("kind") == "maintenance"
               and r.get("registry_ref") == entry_id
               and r.get("criteria")]
    return replays[-1] if replays else None


def _bound(entry, row, ledger, manifests_dir, block_registry, derived_inputs):
    """The bound path: digests checked, verdict recomputed, sides compared."""
    source_id = row.get("source_eval_run_id")
    try:
        scored, _, manifest = load_bound_result(
            entry.get("id"), source_id, ledger, manifests_dir)
    except ProvenanceRefused as exc:
        # THE MANIFEST MOVED. The gate row is chained — it cannot be edited
        # without breaking every row after it — so a disagreement between the
        # row and the file is a disagreement the file lost.
        raise ReportRefused(
            "REFUSED: the recorded result no longer matches the record. Gate "
            "row {} was rendered against eval run {}, and that result cannot "
            "be recovered as recorded.\n\n{}\n\nTHE SIDE THAT MOVED IS THE "
            "MANIFEST. The ledger row is hash-chained; the manifest is a file "
            "on disk. No memo is rendered from a result that disagrees with "
            "the record of it.".format(
                row.get("run_id"), source_id, exc))

    results = statistics_from_result(entry, scored)
    verdict = evaluate_gate(entry, results, block_registry=block_registry,
                            derived_inputs=derived_inputs)

    differences = _compare_outcomes(row, verdict)
    if differences:
        # THE ENGINE MOVED. The manifest matched its digest, so the numbers
        # are the recorded ones; re-rendering them produced a different
        # comparison. That is a change in this tool, not in the data.
        raise ReportRefused(
            "REFUSED: recomputing gate row {}'s verdict from the SAME bound "
            "result gives a different answer.\n  {}\n\nTHE SIDE THAT MOVED IS "
            "THE GATE ENGINE. The manifest matches the digest recorded when "
            "it ran, so the numbers are unchanged and the comparison over "
            "them is not. A verdict is what was committed to before the data "
            "was seen; it is not re-derived silently under a new engine. "
            "Either record a fresh verdict deliberately, or read the "
            "historical one through the tool that rendered it.".format(
                row.get("run_id"), "\n  ".join(differences)))

    return verdict, results, {
        "status": "bound",
        "gate_run_id": row.get("run_id"),
        "source_eval_run_id": source_id,
        "manifest_run_id": manifest.get("run_id"),
        "result_hash": row.get("result_hash"),
        "note": (
            "Verdict read from gate row {}, recomputed from eval run {}'s "
            "recorded result ({}), and the two agree criterion by "
            "criterion.".format(row.get("run_id"), source_id,
                                row.get("result_hash"))),
        "scoring": _scoring_provenance(manifest),
    }


def _scoring_provenance(manifest):
    """What the manifest says about who computed the gated statistic.

    Three states, matching `verify`'s three classes: pinned (the score
    function came from the pinned artifact), caller (supplied by the eval
    script, attested only), and unverifiable (the run predates the pin).
    """
    scoring = (manifest or {}).get("scoring")
    if not scoring:
        return {
            "status": "unverifiable",
            "note": ("this run predates the score-function pin; whether the "
                     "pinned artifact computed the statistic cannot be "
                     "checked"),
        }
    if scoring.get("source") == "artifact":
        return {
            "status": "pinned",
            "note": "score function read from the pinned artifact ({})".format(
                scoring.get("code_hash")),
        }
    return {
        "status": "caller",
        "note": ("score function supplied by the caller, not pinned; the "
                 "statistic's computation is attested, not verified"),
    }


def _compare_outcomes(row, verdict):
    """Every way the recorded verdict and the recomputed one can disagree.

    Compared as strings because both sides are canonical decimal strings by
    construction — CriterionOutcome.to_dict writes them and the stored row
    round-trips through JSON unchanged. Comparing floats here would
    reintroduce exactly the ulp ambiguity that made them strings.
    """
    recorded = {c.get("name"): c for c in row.get("criteria") or []}
    recomputed = {c.name: c.to_dict() for c in verdict.criteria}

    differences = []
    for name in sorted(set(recorded) | set(recomputed)):
        if name not in recorded:
            differences.append(
                "criterion {}: absent from the record, present now".format(name))
            continue
        if name not in recomputed:
            differences.append(
                "criterion {}: recorded, and the engine no longer renders "
                "it".format(name))
            continue
        for field in ("statistic", "comparator", "threshold", "actual",
                      "passed", "infeasible"):
            was = recorded[name].get(field)
            now = recomputed[name].get(field)
            if bool(was) != bool(now) and field in ("passed", "infeasible"):
                differences.append("criterion {} {}: recorded {!r}, now {!r}"
                                   .format(name, field, was, now))
            elif field not in ("passed", "infeasible") and str(was) != str(now):
                differences.append("criterion {} {}: recorded {!r}, now {!r}"
                                   .format(name, field, was, now))

    recorded_outcome = row.get("outcome")
    now_outcome = "pass" if verdict.passed else "fail"
    if recorded_outcome != now_outcome:
        differences.append("verdict: recorded {!r}, now {!r}".format(
            recorded_outcome, now_outcome))
    return differences


def build_report(entry, verdict, ledger=None, amendments=None, fills=None,
                 errata=None, scored_results=None, ancestors=None,
                 provenance=None):
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
        ancestors: The registered entries this one descends from. Required to
            count how often the pre-registration has been invalidated: that
            record lives on whichever ancestor was amended, not on this
            entry. Omitting it on an entry with depends_on is reported as
            UNKNOWN, never as zero.
        provenance: The dict from bind_verdict, saying whether this verdict
            was read from a bound record. Omitted means the caller assembled
            the verdict itself, which is reported as UNVERIFIABLE rather
            than left blank — a memo that does not say where its verdict
            came from reads as though it came from the record.

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

    # Computed before the return so the non-claims can read the same
    # structure the reader sees, rather than re-deriving it from the raw
    # results and risking the two disagreeing.
    benchmark = _benchmark_section(gate, scored_results)

    return {
        "provenance": provenance or {
            "status": "unverifiable",
            "gate_run_id": None,
            "source_eval_run_id": None,
            "manifest_run_id": None,
            "result_hash": None,
            "note": (
                "No provenance was supplied, so this memo cannot say the "
                "verdict below was the one recorded. It was rendered from "
                "results handed to build_report."),
        },
        "entry_id": entry.get("id"),
        "created_utc": entry.get("created_utc"),
        "mechanism_class": (entry.get("mechanism") or {}).get("class"),
        "counterparty": (entry.get("mechanism") or {}).get("counterparty"),
        "claim": (entry.get("mechanism") or {}).get("claim"),
        "universe": universe,
        "verdict": "PASS" if verdict.passed else "FAIL",
        "combination": verdict.combination,
        "criteria": [c.to_dict() for c in verdict.criteria],
        "benchmark": benchmark,
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
        "non_claims": _non_claims(entry, verdict, ancestors,
                                  benchmark),
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

    # DERIVED, NOT PLUMBED. stats.benchmark_sensitivity computes
    # `sign_flip_detected` and nothing ever read it — it does not even reach
    # ScoredResult, which stores only {benchmark: alpha}.
    #
    # Deriving it from the alphas already being printed means the flag cannot
    # disagree with the numbers beside it. A stored field would be a second
    # representation of one fact, which is #26's shape and exactly what makes
    # a stale warning possible.
    #
    # THE PRIMARY IS IN THE SPAN. It was not, and that is a check that
    # structurally could not fire on the case it exists for: a POSITIVE
    # primary against two NEGATIVE alternatives never spanned zero, because
    # the only number the primary benchmark contributed was missing from the
    # set. §2.2.1's own example is that shape — +577 bps under one benchmark
    # against -599 under another — and one of those two numbers is always the
    # primary's.
    #
    # Keyed by `gate.benchmark`, and only when the sweep actually ran: with
    # nothing to compare against, one number cannot span zero anyway, and
    # `sweep_missing` is the finding that catches an unrun sweep. The sweep's
    # values are the SAME statistic recomputed under a substituted benchmark
    # — that is what `benchmark_sensitivity` declares — so the primary's own
    # value is comparable to them by construction. If it is not, the declared
    # sweep is measuring something else and the span is the least of the
    # problems.
    span = dict(sweep)
    primary_name = gate.get("benchmark")
    primary_value = _primary_value(gate, scored_results)
    if sweep and primary_name and primary_value is not None:
        span.setdefault(primary_name, primary_value)

    values = [v for v in span.values() if isinstance(v, (int, float))
              and not isinstance(v, bool)]
    sign_flipped = bool(values) and min(values) < 0 < max(values)

    return {
        "primary": primary_name,
        "justification": gate.get("benchmark_justification"),
        "declared_sensitivity": list(gate.get("benchmark_sensitivity") or []),
        "sweep_results": sweep,
        "sweep_missing": not sweep,
        "sign_flip_span": span,
        "sign_flipped": sign_flipped,
    }


def _primary_value(gate, scored_results):
    """The primary statistic's own value, for the sign-flip span.

    `gate.statistic` names it. Returned as None when the result is absent or
    infeasible — a missing number must not be read as a zero, which would
    make every one-sided sweep span zero and turn the flag into noise.
    """
    name = gate.get("statistic")
    result = (scored_results or {}).get(name)
    value = getattr(result, "value", None)
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    if value != value:       # NaN — infeasible, not a sign
        return None
    return value


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


def _non_claims(entry, verdict, ancestors=None, benchmark=None):
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
    # THE COMPENSATING READER for allowing an amendment to reuse blocks its
    # lineage never spent.
    #
    # Before that change an invalidating amendment cost FRESH BLOCKS, and
    # blocks are the scarce resource — on a 19-block corpus you get three or
    # four before running out. Afterwards it costs nothing on an ungated
    # entry, so a resource-limited search became an unlimited one. The count
    # was already in the record and nothing read it.
    #
    # One number and one sentence, deliberately. Whether repeated amendment
    # should DEFLATE the threshold the way exploration runs do is a real
    # question and a separate one; the amendment log holds what such a rule
    # would need.
    # WALK THE LINEAGE, NOT THE LOCAL LIST.
    #
    # `amendments` is appended only inside the INVALIDATING branch, so an
    # entry whose own amendment was ordinary carries an empty list while its
    # ancestors carry the invalidation. Reading the local list reported zero
    # forks for a chain that had one — and it went silent on exactly the
    # chain where it matters most, since the entry that reused blocks under
    # the fresh-block rule is the one this disclosure exists to pay for.
    #
    # `revision` may be absent on records written before it existed; treat
    # that as 1 rather than as no revision at all.
    records = list(entry.get("amendments") or [])
    for ancestor in ancestors or []:
        records.extend(ancestor.get("amendments") or [])
    revisions = max([(r.get("revision") or 1) for r in records] or [0])

    if ancestors is None and entry.get("depends_on"):
        # Silence here would read as "no revisions" for a chain that has
        # them. Same reasoning as the multiplicity section's NOT AVAILABLE:
        # an unanswered question must not render as a reassuring answer.
        claims.append(
            "This entry descends from {}, and no lineage was supplied to the "
            "report, so the number of times its pre-registration has been "
            "invalidated is UNKNOWN rather than zero.".format(
                ", ".join(entry["depends_on"])))
    elif revisions:
        claims.append(
            "This is revision {} of a pre-registration whose effect size or "
            "direction changed after the original was filed. Each such "
            "revision is a fork in the path, and the trial count does not "
            "currently charge for it.".format(revisions))

    power = (entry.get("power_check") or {}).get("value", {})
    if power.get("flagged"):
        claims.append(
            "The registered design has power below 0.5: it was more likely "
            "than not to miss its own hypothesized effect even if real. A "
            "failure here is weak evidence of absence.")
    if (power.get("derivation") or {}).get("saturated"):
        # Kept separate from the flagged branch above. A saturated design can
        # be perfectly well powered — the two say different things, and a
        # reader who is told only "power 0.86" will reasonably conclude that
        # running longer would make it 0.95.
        claims.append(
            "This design is near the point where more events stop buying "
            "power ({} of the effective-sample ceiling). Past roughly "
            "n_clusters/icc effective observations the binding lever is more "
            "CLUSTERS — a longer window, or a different event class — not "
            "more events.".format(
                power["derivation"].get("pct_of_cap", "?")))
    if power.get("pending"):
        # An unfilled power check must not read like a passed one. Silence
        # here would let a reader assume the design was adequately powered,
        # which is the assumption most likely to be wrong and least likely to
        # be questioned.
        claims.append(
            "The power check was never filled, so this design's ability to "
            "detect its own hypothesized effect is unknown. A failure cannot "
            "be weighed as evidence of absence until it is.")
    if (benchmark or {}).get("sign_flipped"):
        claims.append(
            "The benchmark sweep spans zero, so the SIGN of this result "
            "depends on the benchmark chosen. Until that is resolved it is a "
            "finding about the benchmark rather than about the strategy, and "
            "the primary benchmark's number should not be read on its own.")
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

    # FIRST, not last. Whether the verdict below is the recorded one governs
    # how every number under it may be read, and a provenance note at the
    # bottom of a long memo is a note nobody reaches.
    prov = report["provenance"]
    add("\n--- Provenance ---")
    if prov.get("status") == "bound":
        add("BOUND. {}".format(prov["note"]))
    else:
        add("{}. {}".format(prov.get("status", "unverifiable").upper(),
                            prov.get("note")))
    scoring = prov.get("scoring")
    if scoring:
        add("scoring: {}. {}".format(scoring["status"].upper(),
                                     scoring["note"]))

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
        for name in sorted(bench["sign_flip_span"]):
            # The primary's own value is printed alongside the alternatives,
            # marked as such. A span the reader cannot see is a span the
            # reader cannot check, and the primary being IN it is the whole
            # correction.
            add("  {}{}: {}".format(
                name, "  (primary)" if name == bench["primary"] else "",
                bench["sign_flip_span"][name]))
        if bench["sign_flipped"]:
            add("SIGN FLIP ACROSS BENCHMARKS. The sweep spans zero, so the "
                "direction of this result depends on which benchmark was "
                "chosen. §2.2.1 measured exactly this: +577 bps under BTC "
                "subtraction against -599 under an OI-weighted index, on the "
                "same data.")

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
        # The derivation was assembled into the structure and never printed,
        # so every intermediate a reader needs in order to re-derive the
        # number instead of trusting it was recorded and invisible. That is
        # the S-2 defect in miniature: the component was right and nothing
        # was connected to it.
        derivation = power.get("derivation") or {}
        for key in sorted(derivation):
            if key == "saturated":
                continue
            add("  {:<20} {}".format(key, derivation[key]))

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
