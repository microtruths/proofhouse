# tests/test_w6_retro.py — retro-gate v11's W6 through proofhouse
"""
Phase 2 Completion §1, and checkpoint criterion 1.

The only Phase 2 test that validates the gate engine against a verdict the
harness did not produce. Everything else is self-referential — the engine
checking its own output against thresholds the same test wrote.

TARGET (W6_VALIDATION_RESULTS.md, executed 2026-07-30):

    S2 gross (Construct C) = -10,729 bps, 61-day test period
    Gate 1 (S2 > S1 in >=2/3)   FAIL   — S1 = +15,958
    Gate 2 (median S2 > 0)      FAIL
    Gate 3 (median S2 >= T)     FAIL
    Combination                 g1 AND g2 AND g3   (pinned by A-12 §A12.2)

The criterion is the OUTCOME, not bit-identical statistics: v11's own method
may contain errors, and forcing exact agreement would mean replicating a bug
to pass a checkpoint. Any numeric difference must be explained and written
down — never tolerance-tuned. One is already known and recorded in the fixture
header before this ran: T resolves to 20469.25 against the record's 20,469,
because the median of four values is the mean of the middle pair and the
record rounded to whole bps.

This reproduces the state AFTER W6, which is what W6_VALIDATION_RESULTS
records. It is not the final three-window verdict — A-12 registered a rule
under which W8 executes only if W7 clears both foreclosable gates.
"""

import os

import pytest
import yaml

from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.fill import FillRefused, fill_pending_field
from proofhouse.core.gate import evaluate_gate, evaluate_formula
from proofhouse.core.gate_run import foreclosure_advisory
from proofhouse.eval.types import ScoredResult
from proofhouse.schema.validate import validate_entry

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "v11_w6_behavioral_alpha.yaml",
)

# Registration 5, verbatim.
SIGMA_W = [11223.0, 21273.0, 60604.0, 874630.0]
RECORDED_T = 20469.0          # as written in the locked plan
COMPUTED_T = 20469.25         # what the formula actually gives
S2_GROSS_W6 = -10729.0
S1_GROSS_W6 = 15958.0


@pytest.fixture
def entry():
    with open(FIXTURE) as fh:
        return yaml.safe_load(fh)


@pytest.fixture
def blocks():
    """W2-W5 are dev windows; W6-W8 are validation.

    The segments matter: E2 refuses a threshold derived from validation-
    segment blocks, so this is what makes the anti-circularity check pass for
    the right reason rather than because the registry was empty.
    """
    registry = BlockRegistry()
    for name, segment in [("W2", "S1"), ("W3", "S1"), ("W4", "S2"),
                          ("W5", "S2"), ("W6", "V"), ("W7", "V"),
                          ("W8", "V")]:
        registry.add_block({
            "id": name, "start": "2025-01-01", "end": "2025-03-01",
            "segment": segment, "data_complete": True,
            "completed_at": "2025-03-15", "sources": ["hl-node"], "seams": [],
        })
    return registry


def _w6_results():
    """The scored results as W6 actually landed.

    S2 lost to S1, so the win count is 0 of the 2 required. With one window
    complete the median over completed windows is W6's own value.
    """
    return {
        "s2_beats_s1_windows": ScoredResult(
            "s2_beats_s1_windows", 0, trial_count=4),
        "s2_median_gross_bps": ScoredResult(
            "s2_median_gross_bps", S2_GROSS_W6, trial_count=4),
    }


class TestTheFixtureIsWellFormed:
    def test_it_validates(self, entry, blocks):
        validate_entry(entry, block_registry=blocks)

    def test_the_combination_a12_pinned_is_what_is_registered(self, entry):
        assert entry["gate"]["combination"] == "g1 and g2 and g3"

    def test_gate_three_uses_a_derived_threshold_from_dev_windows(self, entry):
        derived = entry["gate"]["criteria"]["g3"]["threshold"]["derived"]
        assert derived["formula"] == "0.5 * median(sigma_w)"
        assert derived["source_blocks"] == ["W2", "W3", "W4", "W5"]


class TestTheDerivedThreshold:
    """Registration 5, reproduced independently."""

    def test_sigma_dev_is_the_mean_of_the_middle_pair(self):
        # median(11223, 21273, 60604, 874630): the two middle values are
        # 21273 and 60604, so the median is their mean, 40938.5 — not the
        # 40,939 the record rounds to.
        assert evaluate_formula("median(sigma_w)",
                                {"sigma_w": SIGMA_W}) == 40938.5

    def test_t_resolves_to_the_unrounded_value(self):
        assert evaluate_formula("0.5 * median(sigma_w)",
                                {"sigma_w": SIGMA_W}) == COMPUTED_T

    def test_the_divergence_from_the_record_is_the_documented_quarter_bp(self):
        """Recorded in the fixture header BEFORE this ran, per checkpoint
        criterion 1's requirement that differences be explained rather than
        tolerance-tuned."""
        assert COMPUTED_T - RECORDED_T == 0.25

    def test_the_divergence_cannot_move_the_w6_verdict(self):
        """Why the difference is a note rather than a problem: -10,729 fails
        against both values, and nothing in between exists to separate them."""
        assert S2_GROSS_W6 < RECORDED_T
        assert S2_GROSS_W6 < COMPUTED_T


class TestTheRecordedVerdict:
    """The checkpoint criterion itself."""

    def test_all_three_gates_fail_and_the_verdict_is_fail(self, entry, blocks):
        verdict = evaluate_gate(
            entry, _w6_results(), block_registry=blocks,
            derived_inputs={"g3": {"sigma_w": SIGMA_W}},
        )
        outcomes = {c.name: c.passed for c in verdict.criteria}

        assert outcomes == {"g1": False, "g2": False, "g3": False}
        assert verdict.passed is False

    def test_gate_one_fails_because_s2_lost_to_s1(self):
        """S2 = -10,729 against S1 = +15,958, so W6 contributes no win and the
        count is 0 against a required 2."""
        assert S2_GROSS_W6 < S1_GROSS_W6

    def test_gate_three_compares_against_the_derived_value(self, entry, blocks):
        verdict = evaluate_gate(
            entry, _w6_results(), block_registry=blocks,
            derived_inputs={"g3": {"sigma_w": SIGMA_W}},
        )
        g3 = [c for c in verdict.criteria if c.name == "g3"][0]
        assert g3.threshold_source == "derived"
        assert float(g3.threshold) == COMPUTED_T
        assert g3.derived_source_blocks == ["W2", "W3", "W4", "W5"]

    def test_per_criterion_statistics_are_read_separately(self, entry, blocks):
        """g1 reads a win count, g2 and g3 read the median. No earlier fixture
        exercised the per-criterion statistic override."""
        verdict = evaluate_gate(
            entry, _w6_results(), block_registry=blocks,
            derived_inputs={"g3": {"sigma_w": SIGMA_W}},
        )
        by_name = {c.name: c.statistic for c in verdict.criteria}
        assert by_name == {
            "g1": "s2_beats_s1_windows",
            "g2": "s2_median_gross_bps",
            "g3": "s2_median_gross_bps",
        }


class TestAntiCircularity:
    def test_deriving_t_from_a_validation_block_is_refused(self, entry, blocks):
        """v11 Registration 5: 'do not re-derive T from validation data (that
        would be fitting the threshold to the data it polices).' Enforced at
        gate time, not only at registration."""
        entry["gate"]["criteria"]["g3"]["threshold"]["derived"][
            "source_blocks"] = ["W6"]
        from proofhouse.core.gate import GateRefusal
        with pytest.raises(GateRefusal) as exc:
            evaluate_gate(entry, _w6_results(), block_registry=blocks,
                          derived_inputs={"g3": {"sigma_w": SIGMA_W}})
        assert "segment V" in str(exc.value)


class TestForeclosure:
    """A-12's actual argument, checked against A-12's actual gate."""

    def test_no_single_gate_was_load_bearing_after_w6(self, entry, blocks):
        """All three failed, so flipping any one alone still leaves FAIL. That
        is why A-12 could argue the verdict was foreclosable — and it is the
        first time the advisory has run against a registered entry."""
        verdict = evaluate_gate(
            entry, _w6_results(), block_registry=blocks,
            derived_inputs={"g3": {"sigma_w": SIGMA_W}},
        )
        advisory = foreclosure_advisory(
            entry, [(c.name, c.passed) for c in verdict.criteria])
        assert advisory == (False, ["g1", "g2", "g3"])


class TestPowerCheckCannotBeFilled:
    """A real finding, not a gap in the fixture."""

    def test_fill_refuses_because_v11_registered_bps_not_sharpe(self, entry):
        """proofhouse's power rule is Sharpe-denominated; v11 registered its
        effect size as a bps threshold. There is no effect_size to supply, so
        the tool refuses rather than inventing one — which is the behaviour
        that makes a filled field trustworthy."""
        with pytest.raises(FillRefused) as exc:
            fill_pending_field(entry, "power_check")
        assert "effect_size" in str(exc.value)
