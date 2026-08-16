# tests/test_power.py — Hand-computed tests for power check
"""
Phase 1 Build Plan §1a — Power check tests.

Parent plan §11:
    "power check against the §2.1 table (S=1, T=0.1y → SE≈3.87)"

Parent plan §5.6:
    Power < 0.5 → flagged. Derived budget: affordable N.
"""

import math


class TestPowerCheck:
    """Power check tests with hand-computed expected values."""

    def test_se_sharpe_five_weeks(self):
        """SE of Sharpe estimate at S=1.0, T=0.1y (5 weeks).

        Parent plan §2.1:
            SE ≈ √((1 + S²/2) / T), T in calendar years
            SE = √((1 + 1.0/2) / 0.1) = √(1.5 / 0.1) = √15 ≈ 3.873
        """
        from proofhouse.eval.power import se_sharpe

        se = se_sharpe(sharpe=1.0, span_years=0.1)
        expected = math.sqrt(15.0)  # sqrt((1 + 0.5) / 0.1)
        assert abs(se - expected) < 0.01, (
            "SE at S=1.0, T=0.1y should be ≈{:.3f}. Got {:.3f}".format(expected, se)
        )

    def test_se_sharpe_two_years(self):
        """SE at S=1.0, T=2.0y.

        SE = √((1 + 0.5) / 2.0) = √0.75 ≈ 0.866
        """
        from proofhouse.eval.power import se_sharpe

        se = se_sharpe(sharpe=1.0, span_years=2.0)
        expected = math.sqrt(0.75)
        assert abs(se - expected) < 0.01

    def test_power_below_half_flagged(self):
        """Power < 0.5 should be flagged.

        At S=1.0, T=0.1y, SE≈3.87. The true effect is utterly
        undetectable — power should be very low.
        """
        from proofhouse.eval.power import compute_power_check

        result = compute_power_check(
            expected_sharpe=1.0,
            span_years=0.1,
            trial_count=1,
        )
        assert result["power"] < 0.5, (
            "Power at S=1, T=0.1y should be < 0.5. Got {}".format(result["power"])
        )
        assert result["flagged"], "Low power should be flagged"

    def test_power_adequate_not_flagged(self):
        """Power > 0.5 should not be flagged.

        At S=2.0, T=3.0y, trial_count=1:
            SE = √((1 + 2.0) / 3.0) = √1.0 = 1.0
            z = 2.0 / 1.0 = 2.0
            Power = Φ(2.0 - 1.645) = Φ(0.355) ≈ 0.639
        """
        from proofhouse.eval.power import compute_power_check

        result = compute_power_check(
            expected_sharpe=2.0,
            span_years=3.0,
            trial_count=1,
        )
        assert result["power"] > 0.5, (
            "Power at S=2, T=3y should be > 0.5. Got {}".format(result["power"])
        )
        assert not result["flagged"], "Adequate power should not be flagged"

    def test_affordable_n_weak_effect(self):
        """Affordable N at S=1.0, T=2y is 0 — effect is too weak.

        At S=1.0, T=2y: SE=0.866, z_effect=1.155. Even with trial_count=1
        (z_threshold=z_alpha=1.645), power=0.31 < 0.5. The effect is too
        weak relative to the noise to afford any trials — this is the
        parent plan §2.1 point about short-horizon experiments.
        """
        from proofhouse.eval.power import affordable_n

        n = affordable_n(expected_sharpe=1.0, span_years=2.0)
        assert n == 0, (
            "S=1.0, T=2y is underpowered even at N=1. Got N={}".format(n)
        )

    def test_affordable_n_stronger_effect(self):
        """Affordable N at S=2.0, T=3y is small but nonzero.

        At S=2.0, T=3y: SE=1.0, z_effect=2.0. With trial_count=1,
        power=0.64 > 0.5, so at least 1 trial is affordable. The additive
        threshold (z_alpha + E[max(N)]) rises fast with N.
        """
        from proofhouse.eval.power import affordable_n

        n = affordable_n(expected_sharpe=2.0, span_years=3.0)
        assert n >= 1, (
            "S=2.0, T=3y should afford at least 1 trial. Got N={}".format(n)
        )

    def test_power_check_stores_all_inputs(self):
        """Power check result stores all inputs for later re-derivation.

        Parent plan §5.6: "stored power_check block records all inputs,
        so a later reader can see what the experiment was capable of detecting."
        """
        from proofhouse.eval.power import compute_power_check

        result = compute_power_check(
            expected_sharpe=1.5,
            span_years=2.0,
            trial_count=3,
        )
        assert "inputs" in result
        assert result["inputs"]["expected_sharpe"] == 1.5
        assert result["inputs"]["span_years"] == 2.0
        assert result["inputs"]["trial_count"] == 3

    def test_more_trials_reduce_power(self):
        """More trials reduce power (higher bar to clear)."""
        from proofhouse.eval.power import compute_power_check

        result_1 = compute_power_check(
            expected_sharpe=1.5,
            span_years=2.0,
            trial_count=1,
        )
        result_10 = compute_power_check(
            expected_sharpe=1.5,
            span_years=2.0,
            trial_count=10,
        )
        assert result_10["power"] < result_1["power"], (
            "More trials should reduce power. "
            "trial_count=1: {}, trial_count=10: {}".format(
                result_1["power"], result_10["power"])
        )
