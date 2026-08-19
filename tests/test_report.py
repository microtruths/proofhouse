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
