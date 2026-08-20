# tests/test_report.py — the findings memo
"""
Phase 2.5 §5.2.

Built against the W6 fixture deliberately. A report designed against a
single-criterion gate would bake in assumptions that break on the first real
one — and every real gate this tool will see looks like W6: several criteria,
per-criterion statistics, and a derived threshold.

The tests that matter here are the ones about what the report REFUSES to say.
A report is exactly where "renders the comparison and nothing else" quietly
stops holding, because softening language reads as helpful.
"""

import os

import pytest
import yaml

from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.gate import evaluate_gate
from proofhouse.core.ledger import Ledger
from proofhouse.core.report import build_report, render_text
from proofhouse.eval.types import ScoredResult

FIXTURES = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")
SIGMA_W = [11223.0, 21273.0, 60604.0, 874630.0]


def _blocks():
    registry = BlockRegistry()
    for name, segment in [("W2", "S1"), ("W3", "S1"), ("W4", "S2"),
                          ("W5", "S2"), ("W6", "V"), ("W7", "V"),
                          ("W8", "V")]:
        registry.add_block({
            "id": name, "start": "2025-01-01", "end": "2025-03-01",
            "segment": segment, "data_complete": True,
            "completed_at": "2025-03-15", "sources": ["hl"], "seams": [],
        })
    return registry


@pytest.fixture
def w6():
    with open(os.path.join(FIXTURES, "v11_w6_behavioral_alpha.yaml")) as fh:
        entry = yaml.safe_load(fh)
    results = {
        "s2_beats_s1_windows": ScoredResult("s2_beats_s1_windows", 0, 4),
        "s2_median_gross_bps": ScoredResult("s2_median_gross_bps", -10729.0, 4),
    }
    verdict = evaluate_gate(entry, results, block_registry=_blocks(),
                            derived_inputs={"g3": {"sigma_w": SIGMA_W}})
    return entry, verdict


class TestTheVerdictIsNotSoftened:
    """The discipline most likely to erode in a report."""

    def test_a_fail_says_fail(self, w6):
        entry, verdict = w6
        text = render_text(build_report(entry, verdict))
        assert "verdict: FAIL" in text

    def test_no_proximity_language_anywhere(self, w6):
        """No 'close to', 'just under', 'approaching', 'nearly'. §2.1 reports
        0.0495 against >= 0.05 as a fail, full stop."""
        entry, verdict = w6
        text = render_text(build_report(entry, verdict)).lower()
        for phrase in ["close to", "just under", "just below", "nearly",
                       "approaching", "almost", "marginally", "essentially",
                       "narrowly", "% of target"]:
            assert phrase not in text, phrase

    def test_a_failing_gate_licenses_nothing(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)
        assert report["licensing"]["licenses_apply"] is False
        assert "Nothing. The gate did not pass." in render_text(report)


class TestNonClaimsSurviveBothOutcomes:
    """pass_does_not_establish is rendered on a FAIL too. A reader who sees
    only what a result licenses will over-read it."""

    def test_does_not_establish_is_rendered_on_a_fail(self, w6):
        entry, verdict = w6
        text = render_text(build_report(entry, verdict))
        assert "What it does NOT establish" in text
        assert "Net-of-cost profitability" in text

    def test_a_fail_does_not_claim_the_mechanism_is_absent(self, w6):
        entry, verdict = w6
        claims = build_report(entry, verdict)["non_claims"]
        assert any("does not establish that the mechanism is absent" in c
                   for c in claims)

    def test_a_flagged_power_check_weakens_the_failure(self, w6):
        """A design that was more likely than not to miss its own effect
        cannot support a strong absence claim, and the report has to say so
        rather than leaving the reader to notice."""
        entry, verdict = w6
        entry["power_check"]["value"] = {
            "pending": False, "filled": "0.001", "flagged": True,
            "derivation": {},
        }
        claims = build_report(entry, verdict)["non_claims"]
        assert any("weak evidence of absence" in c for c in claims)


class TestBenchmarkSweep:
    """§2.2.1: the benchmark choice can flip the sign."""

    def test_a_missing_sweep_is_stated_loudly(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)
        assert report["benchmark"]["sweep_missing"] is True
        assert "SENSITIVITY SWEEP MISSING" in render_text(report)

    def test_declared_sensitivity_is_reported_even_when_unrun(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)
        assert report["benchmark"]["declared_sensitivity"] == [
            "s0-broad-pool", "s6-sign-randomized"]


class TestMultiCriterionRendering:
    """Why this was built after the W6 fixture rather than before."""

    def test_all_three_criteria_appear_with_their_own_statistics(self, w6):
        entry, verdict = w6
        text = render_text(build_report(entry, verdict))
        assert "s2_beats_s1_windows" in text
        assert "s2_median_gross_bps" in text
        assert text.count("FAIL") >= 4        # three criteria plus the verdict

    def test_a_derived_threshold_shows_its_formula_and_sources(self, w6):
        entry, verdict = w6
        text = render_text(build_report(entry, verdict))
        assert "0.5 * median(sigma_w)" in text
        assert "W2" in text and "W5" in text

    def test_foreclosure_is_reported(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)
        assert report["foreclosure"]["non_decisive"] == ["g1", "g2", "g3"]
        assert "did not need spending" in render_text(report)


class TestErrata:
    """Both v11 source documents carry dated errata. A format with nowhere to
    put a correction encourages silent editing."""

    def test_errata_are_rendered(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict, errata=[{
            "date": "2026-08-06",
            "note": "The '~51,000 to overcome' figure was incorrect and "
                    "unsourced. No decision depended on it.",
        }])
        text = render_text(report)
        assert "Errata" in text
        assert "2026-08-06" in text
        assert "No decision depended on it" in text

    def test_no_errata_section_when_there_are_none(self, w6):
        entry, verdict = w6
        assert "Errata" not in render_text(build_report(entry, verdict))


class TestMultiplicity:
    def test_trial_counts_come_from_the_ledger(self, w6, tmpdir):
        entry, verdict = w6
        ledger = Ledger(os.path.join(str(tmpdir), "ledger.jsonl"))
        for n in range(3):
            ledger.append_row(
                code_hash="c{}".format(n), data_hash="d", kind="eval",
                universe="hl-perps-wallets", blocks_touched=[],
                stats_scored=["ic"])

        report = build_report(entry, verdict, ledger=ledger)
        assert report["multiplicity"]["trials"] == 3
        assert report["multiplicity"]["fdr_method"] == "by"

    def test_sidedness_is_reported(self, w6):
        entry, verdict = w6
        assert build_report(
            entry, verdict)["multiplicity"]["p_value_sidedness"] == "one-sided"


class TestSeparability:
    """Layout is provisional per the 2c decision; content is not. A format
    change should be a template swap, not a rewrite."""

    def test_build_report_returns_data_not_text(self, w6):
        entry, verdict = w6
        assert isinstance(build_report(entry, verdict), dict)

    def test_every_committed_content_element_is_present(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)
        for key in ["verdict", "criteria", "benchmark", "multiplicity",
                    "power_check", "history", "licensing", "errata",
                    "non_claims"]:
            assert key in report, key


class TestAbsentInformationIsNotSilence:
    """Things the report must not let a reader assume."""

    def test_a_missing_trial_count_is_stated_not_printed_as_None(self, w6):
        """'None' reads as a number to a skimming reader, and reading it as
        zero is the worst misreading: the count is what inflates the threshold
        a result has to clear."""
        entry, verdict = w6
        text = render_text(build_report(entry, verdict))
        assert "trials on hl-perps-wallets: NOT AVAILABLE" in text
        assert "trials on hl-perps-wallets: None" not in text

    def test_a_pending_power_check_is_a_stated_non_claim(self, w6):
        """An unfilled power check must not read like a passed one. Silence
        lets a reader assume the design was adequately powered — the
        assumption most likely to be wrong and least likely to be
        questioned."""
        entry, verdict = w6
        assert entry["power_check"]["value"]["pending"] is True
        claims = build_report(entry, verdict)["non_claims"]
        assert any("never filled" in c for c in claims)

    def test_a_filled_unflagged_power_check_adds_no_such_claim(self, w6):
        entry, verdict = w6
        entry["power_check"]["value"] = {
            "pending": False, "filled": "0.85", "flagged": False,
            "derivation": {},
        }
        claims = build_report(entry, verdict)["non_claims"]
        assert not any("never filled" in c for c in claims)
        assert not any("weak evidence of absence" in c for c in claims)


class TestBenchmarkSweepCanActuallyBeReported:
    """Audit finding. The first version read `benchmark_results` off
    CriterionOutcome, which has no such attribute, so the sweep was always
    empty and 'SENSITIVITY SWEEP MISSING' fired unconditionally — including
    on results that had run the full sweep.

    A mandatory check that always fires is a check nobody reads. Same defect
    class as the citation resolver that read the real catalog as ending at
    #25.
    """

    def _with_sweep(self):
        with open(os.path.join(FIXTURES,
                               "v11_w6_behavioral_alpha.yaml")) as fh:
            entry = yaml.safe_load(fh)
        results = {
            "s2_beats_s1_windows": ScoredResult("s2_beats_s1_windows", 0, 4),
            "s2_median_gross_bps": ScoredResult(
                "s2_median_gross_bps", -10729.0, 4,
                benchmark_results={"s0-broad-pool": -236822.0,
                                   "s6-sign-randomized": 1657.0}),
        }
        verdict = evaluate_gate(entry, results, block_registry=_blocks(),
                                derived_inputs={"g3": {"sigma_w": SIGMA_W}})
        return entry, verdict, results

    def test_a_run_sweep_is_reported_not_called_missing(self):
        entry, verdict, results = self._with_sweep()
        report = build_report(entry, verdict, scored_results=results)

        assert report["benchmark"]["sweep_missing"] is False
        assert report["benchmark"]["sweep_results"] == {
            "s0-broad-pool": -236822.0, "s6-sign-randomized": 1657.0}
        text = render_text(report)
        assert "SENSITIVITY SWEEP MISSING" not in text
        assert "s0-broad-pool" in text

    def test_an_absent_sweep_is_still_reported_missing(self):
        entry, verdict, _ = self._with_sweep()
        plain = {"s2_beats_s1_windows": ScoredResult("s2_beats_s1_windows", 0, 4),
                 "s2_median_gross_bps": ScoredResult(
                     "s2_median_gross_bps", -10729.0, 4)}
        report = build_report(entry, verdict, scored_results=plain)
        assert report["benchmark"]["sweep_missing"] is True


class TestThePowerDerivationIsActuallyRendered:
    """The derivation was assembled into the structure and never printed.

    Every intermediate a reader needs in order to re-derive the number
    instead of trusting it was recorded and invisible — the S-2 defect in
    miniature: the component was right and nothing was connected to it. Same
    shape as the benchmark sweep that could never report.
    """

    def _filled(self, entry, derivation, flagged=False):
        entry = dict(entry)
        entry["power_check"] = {
            "inputs": {"effect_ratio": "0.1361", "n_events": 950,
                       "n_clusters": 279, "icc": "0.067", "prior_trials": 4},
            "value": {"filled": "0.8630381045", "flagged": flagged,
                      "derivation": derivation},
        }
        return entry

    DERIVATION = {
        "m_bar": "3.4050179211", "deff": "1.1611362007",
        "n_eff": "818.1641390678", "n_eff_cap": "4164.1791044776",
        "pct_of_cap": "0.1964774", "saturated": False,
        "z_effect": "3.8929456922", "z_threshold": "2.79887458",
    }

    def test_deff_and_the_cap_reach_the_rendered_memo(self, w6):
        entry, verdict = w6
        report = build_report(self._filled(entry, self.DERIVATION), verdict)
        text = render_text(report)

        assert "deff" in text
        assert "1.1611362007" in text
        assert "n_eff_cap" in text
        assert "4164.1791044776" in text

    def test_a_saturated_design_says_more_events_will_not_help(self, w6):
        """A saturated design can be perfectly well powered. A reader told
        only 'power 0.86' will reasonably conclude that running longer makes
        it 0.95, and for this design it does not."""
        entry, verdict = w6
        derivation = dict(self.DERIVATION, saturated=True,
                          pct_of_cap="0.91")
        report = build_report(self._filled(entry, derivation), verdict)

        claims = " ".join(report["non_claims"])
        assert "more events stop buying power" in claims
        assert "CLUSTERS" in claims
        assert "0.91" in claims

    def test_an_unsaturated_design_says_nothing_about_it(self, w6):
        entry, verdict = w6
        report = build_report(self._filled(entry, self.DERIVATION), verdict)

        claims = " ".join(report["non_claims"])
        assert "more events stop buying power" not in claims

    def test_saturation_and_low_power_are_separate_statements(self, w6):
        """Deliberately not folded into `flagged`. That flag means one thing
        — power below 0.5 — and overloading it would make an existing report
        line ambiguous about which problem it is reporting."""
        entry, verdict = w6
        derivation = dict(self.DERIVATION, saturated=True, pct_of_cap="0.91")
        report = build_report(self._filled(entry, derivation, flagged=True),
                              verdict)

        claims = " ".join(report["non_claims"])
        assert "power below 0.5" in claims
        assert "more events stop buying power" in claims


class TestTheLineageRevisionCount:
    """The compensating reader for permitting block reuse.

    Before that change an invalidating amendment cost fresh blocks, and
    blocks are the scarce resource. Afterwards it costs nothing on an
    ungated entry, so a resource-limited search became an unlimited one.
    The count sat in the record and nothing read it.
    """

    def test_a_revised_registration_says_so(self, w6):
        entry, verdict = w6
        entry = dict(entry)
        entry["amendments"] = [
            {"field": "expected_effect_size", "parent_id": "H-B",
             "revision": 2},
        ]

        report = build_report(entry, verdict)
        claims = " ".join(report["non_claims"])

        assert "revision 2" in claims
        assert "does not currently charge" in claims

    def test_the_number_comes_from_the_record_not_the_list_length(self, w6):
        """A successor built fresh carries one amendment record for a
        lineage that has forked twice. Counting the list would report 1."""
        entry, verdict = w6
        entry = dict(entry)
        entry["amendments"] = [
            {"field": "expected_effect_size", "parent_id": "H-C",
             "revision": 4},
        ]

        report = build_report(entry, verdict)

        assert "revision 4" in " ".join(report["non_claims"])

    def test_an_original_registration_says_nothing(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict)

        assert "revision" not in " ".join(report["non_claims"])

    def test_an_ancestors_invalidation_is_disclosed(self, w6):
        """THE LIVE REGRESSION. `amendments` is appended only in the
        invalidating branch, so an entry whose own amendment was ordinary
        carries an empty list while its ancestor carries the invalidation.

        PFR1 -> PFR2 -> PFR3 in the real registry: PFR2 holds the
        invalidating record, PFR3 holds none. Reading the local list
        disclosed zero forks for a chain that had one — and went silent on
        exactly the chain where it matters most, since PFR2 is the entry
        that reused blocks under the fresh-block rule and this disclosure is
        what pays for allowing that.
        """
        entry, verdict = w6
        entry = dict(entry, depends_on=["H-PARENT"], amendments=[])
        parent = {"id": "H-PARENT",
                  "amendments": [{"field": "expected_effect_size",
                                  "revision": 1}]}

        report = build_report(entry, verdict, ancestors=[parent])

        assert "revision 1" in " ".join(report["non_claims"])

    def test_a_missing_lineage_is_UNKNOWN_not_zero(self, w6):
        """An unanswered question must not render as a reassuring answer —
        the same reasoning as the multiplicity section's NOT AVAILABLE."""
        entry, verdict = w6
        entry = dict(entry, depends_on=["H-PARENT"], amendments=[])

        report = build_report(entry, verdict)          # no ancestors
        claims = " ".join(report["non_claims"])

        assert "UNKNOWN rather than zero" in claims
        assert "H-PARENT" in claims

    def test_an_entry_with_no_parents_says_nothing(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict, ancestors=[])
        claims = " ".join(report["non_claims"])

        assert "revision" not in claims
        assert "UNKNOWN rather than zero" not in claims

    def test_a_record_without_a_revision_field_counts_as_one(self, w6):
        """Records written before `revision` existed carry no such key —
        PFR2's does not. Treating that as no revision at all would silence
        the disclosure on precisely the historical entries it is for."""
        entry, verdict = w6
        entry = dict(entry, depends_on=["H-PARENT"], amendments=[])
        parent = {"id": "H-PARENT",
                  "amendments": [{"field": "expected_effect_size"}]}

        report = build_report(entry, verdict, ancestors=[parent])

        assert "revision 1" in " ".join(report["non_claims"])


class TestASignFlipAcrossBenchmarksIsReported:
    """§2.2.1 measured the benchmark choice reversing a result's sign:
    +577 bps under BTC subtraction against -599 under an OI-weighted index,
    on the same data. That is why the sweep is mandatory.

    stats.benchmark_sensitivity computed `sign_flip_detected` and nothing
    read it — it does not even reach ScoredResult, which stores only
    {benchmark: alpha}. So a memo could print a sweep that spanned zero and
    say nothing. Derived here from the alphas already being printed, so the
    flag cannot disagree with the numbers beside it.

    §2.3: THE PRIMARY IS IN THE SPAN, and it was not. The check therefore
    could not fire on the case it exists for — a positive primary against
    two negative alternatives — because the only number the primary
    benchmark contributed was missing from the set. The fixture's primary
    statistic is `s2_median_gross_bps` and its benchmark is
    `s1-alpha-ranked-cohort`, so the span is keyed by that name.
    """

    def _with_sweep(self, sweep, primary=-10729.0):
        return {"s2_median_gross_bps": ScoredResult(
            "s2_median_gross_bps", primary, 4, benchmark_results=sweep)}

    def test_a_sweep_spanning_zero_is_flagged_and_rendered(self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict, scored_results=self._with_sweep(
            {"btc-subtraction": 577.0, "oi-weighted-index": -599.0}))

        assert report["benchmark"]["sign_flipped"] is True
        assert "SIGN FLIP ACROSS BENCHMARKS" in render_text(report)
        assert any("depends on the benchmark chosen" in c
                   for c in report["non_claims"])

    def test_same_signed_alphas_do_not_flag(self, w6):
        """THE CRY-WOLF DIRECTION. A mandatory warning that always fires is
        a warning nobody reads — how the first cut of the block check and
        the citation resolver both failed.

        "Same signed" now has to include the primary, which is the point of
        the fix: agreement among the alternatives while the primary
        disagrees is not agreement."""
        entry, verdict = w6
        report = build_report(entry, verdict, scored_results=self._with_sweep(
            {"btc-subtraction": -577.0, "oi-weighted-index": -412.0}))

        assert report["benchmark"]["sign_flipped"] is False
        assert "SIGN FLIP" not in render_text(report)
        assert not any("depends on the benchmark chosen" in c
                       for c in report["non_claims"])

    def test_a_positive_primary_against_negative_alternatives_flips(self, w6):
        """§2.3's case, and the reason the check was worth fixing rather than
        deleting: this shape trips nothing at all when the primary is left
        out of the span, and it is precisely §2.2.1's finding — one benchmark
        positive, another negative, on the same data."""
        entry, verdict = w6
        report = build_report(entry, verdict, scored_results=self._with_sweep(
            {"s0-broad-pool": -577.0, "s6-sign-randomized": -599.0},
            primary=577.0))

        assert report["benchmark"]["sign_flipped"] is True
        assert report["benchmark"]["sign_flip_span"][
            "s1-alpha-ranked-cohort"] == 577.0
        text = render_text(report)
        assert "SIGN FLIP ACROSS BENCHMARKS" in text
        # The span is printed, primary marked, so a reader can check it.
        assert "s1-alpha-ranked-cohort  (primary): 577.0" in text

    def test_both_negative_including_the_primary_does_not_flag(self, w6):
        """Consistently negative is a consistent answer, not a flip."""
        entry, verdict = w6
        report = build_report(entry, verdict, scored_results=self._with_sweep(
            {"a": -577.0, "b": -599.0}))
        assert report["benchmark"]["sign_flipped"] is False

    def test_a_single_alternative_agreeing_with_the_primary_does_not_flip(
            self, w6):
        entry, verdict = w6
        report = build_report(entry, verdict,
                              scored_results=self._with_sweep({"a": -577.0}))
        assert report["benchmark"]["sign_flipped"] is False

    def test_a_single_alternative_disagreeing_with_the_primary_flips(self, w6):
        """One alternative IS enough, now that the primary is in the span.
        Two numbers of opposite sign is a sign flip whether or not both are
        alternatives."""
        entry, verdict = w6
        report = build_report(entry, verdict,
                              scored_results=self._with_sweep({"a": 577.0}))
        assert report["benchmark"]["sign_flipped"] is True

    def test_an_unrun_sweep_does_not_put_the_primary_in_a_span_of_one(
            self, w6):
        """One number cannot span zero, and folding the primary in when
        nothing was swept would make the span look like a sweep.
        `sweep_missing` stays the finding that catches an unrun sweep."""
        entry, verdict = w6
        report = build_report(entry, verdict,
                              scored_results=self._with_sweep({}))
        assert report["benchmark"]["sign_flip_span"] == {}
        assert report["benchmark"]["sweep_missing"] is True

    def test_an_infeasible_primary_is_not_read_as_a_zero(self, w6):
        """A missing number must not be read as a sign. NaN is
        infeasibility, which the gate counts as a loss — not as a negative
        alpha that makes every one-sided sweep span zero."""
        entry, verdict = w6
        report = build_report(entry, verdict, scored_results=self._with_sweep(
            {"a": 577.0, "b": 412.0}, primary=float("nan")))
        assert report["benchmark"]["sign_flipped"] is False

    def test_an_unrun_sweep_is_not_a_clean_sweep(self, w6):
        """No flip detected is not the same as no flip possible. The
        MISSING line must still be what catches an unrun sweep."""
        entry, verdict = w6
        report = build_report(entry, verdict)

        assert report["benchmark"]["sign_flipped"] is False
        assert report["benchmark"]["sweep_missing"] is True
        assert "SENSITIVITY SWEEP MISSING" in render_text(report)


class TestTheMemoReadsTheRecordedVerdict:
    """§2.2 point 6. `report` ignored the recorded gate row entirely and
    re-derived the verdict from whichever manifest was newest, so the memo
    could state a verdict no ledger row records — and editing a manifest
    changed what it printed with every chain verifying clean.

    Recompute-and-compare is strictly stronger than either half: reading the
    row alone prints a verdict nothing re-derives, recomputing alone is what
    the tool already did wrong.
    """

    ENTRY = {
        "id": "H-2026-AC10",
        "hash": "h0",
        "created_utc": "2026-08-19T00:00:00Z",
        "universe": {"id": "hl-perps-wallets"},
        "validation_blocks": [],
        "pass_licenses": "A 6-week shadow trial.",
        "pass_does_not_establish": "Deployable alpha.",
        "gate": {
            "statistic": "ic",
            "combination": "g1",
            "criteria": {"g1": {"comparator": ">=",
                                "threshold": {"literal": "0.05"}}},
        },
    }

    def _bound(self, value=0.038898, **kw):
        from tests.bound_eval import project, record_eval
        ledger, manifests = project()
        scored = ScoredResult("ic", value, 14)
        run_id = record_eval(ledger, manifests, dict(self.ENTRY), scored, **kw)
        return ledger, manifests, run_id

    def test_a_bound_verdict_is_read_and_recomputed(self):
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import bind_verdict

        ledger, manifests, run_id = self._bound()
        recorded, gate_id = run_gate(dict(self.ENTRY), run_id, ledger,
                                     manifests)

        verdict, results, provenance = bind_verdict(
            dict(self.ENTRY), ledger, manifests)

        assert provenance["status"] == "bound"
        assert provenance["gate_run_id"] == gate_id
        assert provenance["source_eval_run_id"] == run_id
        assert verdict.to_dict() == recorded.to_dict()
        assert results["ic"].value == 0.038898

    def test_the_memo_states_the_binding(self):
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import bind_verdict

        ledger, manifests, run_id = self._bound()
        run_gate(dict(self.ENTRY), run_id, ledger, manifests)
        verdict, results, provenance = bind_verdict(
            dict(self.ENTRY), ledger, manifests)

        text = render_text(build_report(
            dict(self.ENTRY), verdict, scored_results=results,
            provenance=provenance))

        assert "--- Provenance ---" in text
        assert "BOUND." in text
        assert run_id in text

    def test_a_tampered_manifest_refuses_and_names_the_manifest(self):
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import ReportRefused, bind_verdict
        from tests.bound_eval import tamper

        ledger, manifests, run_id = self._bound()
        run_gate(dict(self.ENTRY), run_id, ledger, manifests)
        tamper(manifests, run_id, value="0.9")

        with pytest.raises(ReportRefused) as exc:
            bind_verdict(dict(self.ENTRY), ledger, manifests)

        assert "THE SIDE THAT MOVED IS THE MANIFEST" in str(exc.value)

    def test_a_legacy_gate_row_still_renders_and_says_it_is_unverifiable(self):
        """R-000013 predates every new field, and `proofhouse report
        H-2026-PFR4` has to keep working. Refusing here would make the tool
        unable to report on its own history, which is not integrity."""
        from proofhouse.core.report import bind_verdict

        ledger, manifests, run_id = self._bound(digests=False)
        ledger.append_row(
            code_hash="c1", data_hash="d1", universe="hl-perps-wallets",
            kind="gate", blocks_touched=[], stats_scored=["ic"],
            registry_ref="H-2026-AC10", outcome="fail", entry_hash="h0")

        verdict, results, provenance = bind_verdict(
            dict(self.ENTRY), ledger, manifests)

        assert provenance["status"] == "unverifiable"
        assert verdict.passed is False
        text = render_text(build_report(
            dict(self.ENTRY), verdict, scored_results=results,
            provenance=provenance))
        assert "UNVERIFIABLE." in text
        assert "verdict: FAIL" in text

    def test_a_legacy_row_may_still_gate_on_a_metadata_statistic(self):
        """PFR4's g2 reads mean_net_bps_per_event, which lives in the
        immutable manifest's metadata and always will. The memo renders it
        and states the gap; what nothing does is record a NEW verdict from
        it."""
        from proofhouse.core.report import bind_verdict
        from tests.bound_eval import project, record_eval

        entry = {
            "id": "H-2026-PFR4-LIKE",
            "hash": "h0",
            "universe": {"id": "hl-perps"},
            "validation_blocks": [],
            "gate": {
                "statistic": "clustered_t_stat",
                "combination": "g1 and g2",
                "criteria": {
                    "g1": {"comparator": ">=",
                           "threshold": {"literal": "2.923"}},
                    "g2": {"statistic": "mean_net_bps_per_event",
                           "comparator": ">=", "threshold": {"literal": "25"}},
                },
            },
        }
        ledger, manifests = project()
        scored = ScoredResult(
            "clustered_t_stat", 1.8617105276654804, 1,
            metadata={"mean_net_bps_per_event": 46.42003773745695})
        record_eval(ledger, manifests, entry, scored, digests=False)
        ledger.append_row(
            code_hash="c1", data_hash="d1", universe="hl-perps", kind="gate",
            blocks_touched=[], stats_scored=["clustered_t_stat",
                                             "mean_net_bps_per_event"],
            registry_ref=entry["id"], outcome="fail", entry_hash="h0")

        verdict, results, provenance = bind_verdict(entry, ledger, manifests)

        assert provenance["status"] == "unverifiable"
        assert verdict.passed is False
        outcomes = {c.name: c for c in verdict.criteria}
        assert outcomes["g1"].actual == "1.8617105276654804"
        assert outcomes["g2"].actual == "46.420037737456951"
        assert outcomes["g2"].passed is True

    def test_no_gate_row_at_all_is_not_a_recorded_decision(self):
        """A memo computed on the spot must not read as a record of a
        decision that was taken."""
        from proofhouse.core.report import bind_verdict

        ledger, manifests, run_id = self._bound()
        verdict, results, provenance = bind_verdict(
            dict(self.ENTRY), ledger, manifests)

        assert provenance["status"] == "unrecorded"
        assert "NOT a record of a decision" in provenance["note"]

    def test_nothing_to_report_on_refuses(self):
        from proofhouse.core.report import ReportRefused, bind_verdict
        from tests.bound_eval import project

        ledger, manifests = project()
        with pytest.raises(ReportRefused):
            bind_verdict(dict(self.ENTRY), ledger, manifests)

    def test_a_decision_row_outranks_a_replay(self):
        """A kind='maintenance' replay re-renders a verdict a gate row
        already recorded, so the decision row is the one the memo is
        about."""
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import bind_verdict

        ledger, manifests, run_id = self._bound()
        _, gate_id = run_gate(dict(self.ENTRY), run_id, ledger, manifests)
        run_gate(dict(self.ENTRY), run_id, ledger, manifests,
                 kind="maintenance")

        _, _, provenance = bind_verdict(dict(self.ENTRY), ledger, manifests)
        assert provenance["gate_run_id"] == gate_id

    def test_a_report_without_provenance_says_so(self):
        """build_report may be called with results assembled by hand — in
        tests, and by anything that predates bind_verdict. Silence there
        would let such a memo read as though its verdict came from the
        record."""
        report = build_report(dict(self.ENTRY), _AlwaysFail())
        assert report["provenance"]["status"] == "unverifiable"
        assert "No provenance was supplied" in report["provenance"]["note"]


class _AlwaysFail:
    """The smallest thing shaped like a Verdict, for the provenance default."""
    entry_id = "H-2026-AC10"
    combination = "g1"
    criteria = []
    passed = False
    pass_licenses = None
    pass_does_not_establish = None
