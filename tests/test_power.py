# tests/test_power.py — Hand-computed tests for power check
"""
Phase 1 Build Plan §1a — Power check tests.

Parent plan §11:
    "power check against the §2.1 table (S=1, T=0.1y → SE≈3.87)"

Parent plan §5.6:
    Power < 0.5 → flagged. Derived budget: affordable N.
"""

import math

import pytest


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


class TestPowerEventsRule:
    """The event-count rule.

    Exists because compute_power_check's z_effect converges to sqrt(2T) — a
    ceiling that depends only on the span, so at 3 blocks power asymptotes at
    0.259 for ANY effect size. Every event-study design was unscoreable, which
    on this data budget is the only class that can resolve anything.

    Expected values are pinned to what _phi_inv RETURNS (the Abramowitz &
    Stegun approximation, 1.6452114401 at p=0.95), not to the mathematics
    (1.6448536270). Deriving an expectation from theory and asserting it
    against an approximation is failure catalog #22's shape, and it is how the
    hand-off spec's own base case came out wrong in the fourth decimal.
    """

    def test_the_hand_computed_base_case(self):
        """r=0.10, n=400, G=100, icc=0.10, 1 prior trial.

            m_bar = 400/100                = 4
            deff  = 1 + (4-1)*0.10         = 1.3
            n_eff = 400/1.3                = 307.6923076923
            z     = 0.10*sqrt(307.69...)   = 1.7541160386
            z_thr = _phi_inv(0.95) + 0     = 1.6452114401
            power = Phi(0.1089045985)      = 0.5433609204
        """
        from proofhouse.eval.power import compute_power_events

        result = compute_power_events(
            effect_ratio=0.10, n_events=400, n_clusters=100, icc=0.10,
            trial_count=1)

        assert result["m_bar"] == pytest.approx(4.0)
        assert result["deff"] == pytest.approx(1.3)
        assert result["n_eff"] == pytest.approx(307.6923076923, abs=1e-9)
        assert result["z_effect"] == pytest.approx(1.7541160386, abs=1e-9)
        assert result["z_threshold"] == pytest.approx(1.6452114401, abs=1e-9)
        assert result["power"] == pytest.approx(0.5433609204, abs=1e-9)
        assert result["flagged"] is False

    def test_icc_zero_reduces_to_the_unclustered_case(self):
        from proofhouse.eval.power import compute_power_events

        result = compute_power_events(
            effect_ratio=0.10, n_events=400, n_clusters=100, icc=0.0,
            trial_count=1)

        assert result["deff"] == pytest.approx(1.0)
        assert result["n_eff"] == pytest.approx(400.0)
        # No clustering means no cap at all: more events always buy power.
        # Reported as None rather than infinity — "inf" in a record parses
        # back as a number, which is the trap V-15 closes for thresholds.
        assert result["n_eff_cap"] is None
        assert result["pct_of_cap"] is None
        assert result["saturated"] is False

    def test_icc_one_collapses_to_the_cluster_count(self):
        """Perfect within-cluster correlation: each cluster is one
        observation, however many events it holds."""
        from proofhouse.eval.power import compute_power_events

        result = compute_power_events(
            effect_ratio=0.10, n_events=400, n_clusters=100, icc=1.0,
            trial_count=1)

        assert result["n_eff"] == pytest.approx(100.0)

    def test_n_eff_is_monotone_and_bounded_by_the_cap(self):
        """THE SATURATION. n_eff -> G/icc as n grows with G fixed.

        Asserts the property both ways: always increasing, never past the
        cap. A test that only checked the limit would pass on a formula that
        approached it from above.
        """
        from proofhouse.eval.power import compute_power_events

        cap = 279 / 0.067
        previous = 0.0
        for n_events in (300, 1_000, 10_000, 100_000, 10_000_000):
            result = compute_power_events(
                effect_ratio=0.10, n_events=n_events, n_clusters=279,
                icc=0.067, trial_count=1)
            assert result["n_eff"] > previous
            assert result["n_eff"] < cap
            assert result["n_eff_cap"] == pytest.approx(cap)
            previous = result["n_eff"]

        assert previous == pytest.approx(cap, rel=1e-3)

    def test_saturation_flags_above_eighty_percent_of_cap(self):
        from proofhouse.eval.power import compute_power_events

        low = compute_power_events(
            effect_ratio=0.10, n_events=950, n_clusters=279, icc=0.067,
            trial_count=1)
        assert low["pct_of_cap"] < 0.8
        assert low["saturated"] is False

        high = compute_power_events(
            effect_ratio=0.10, n_events=100_000, n_clusters=279, icc=0.067,
            trial_count=1)
        assert high["pct_of_cap"] > 0.8
        assert high["saturated"] is True

    def test_the_ceiling_is_escapable_by_a_bigger_effect(self):
        """The whole reason this rule exists, asserted rather than argued.

        The Sharpe ceiling caps z_effect itself and is INDEPENDENT of the
        effect size, so no strategy however good clears it. Here the cap binds
        the sample, and z = r*sqrt(n_eff) still rises without bound in r.
        """
        from proofhouse.eval.power import compute_power_events

        powers = [
            compute_power_events(effect_ratio=r, n_events=10_000_000,
                                 n_clusters=279, icc=0.067, trial_count=1)
            ["power"]
            for r in (0.01, 0.02, 0.05, 0.10)
        ]
        assert powers == sorted(powers)
        assert powers[0] < 0.5 < powers[-1]

    def test_but_the_span_constraint_is_weakened_not_escaped(self):
        """G is an event count, and event counts scale with the window.

        The ceiling moved from sqrt(2T) to sqrt(G) with G >> T — a far better
        constant, not a different kind of constraint. A low-event design has
        the same pathology, and a reader of the comparison table could easily
        conclude otherwise.
        """
        from proofhouse.eval.power import compute_power_events

        few = compute_power_events(effect_ratio=0.05, n_events=200,
                                   n_clusters=10, icc=0.067, trial_count=1)
        many = compute_power_events(effect_ratio=0.05, n_events=5_000,
                                    n_clusters=279, icc=0.067, trial_count=1)
        assert few["power"] < 0.5 < many["power"]

    def test_threshold_parity_with_the_sharpe_rule(self):
        """THE REGRESSION TEST THAT STOPS THE TWO RULES DRIFTING.

        Both must derive z_threshold from the same multiple-testing
        correction. Two corrections that can diverge is a worse failure than
        having only one rule.
        """
        from proofhouse.eval.power import (compute_power_check,
                                           compute_power_events)

        for trials in (1, 2, 4, 6, 14, 47):
            sharpe = compute_power_check(
                expected_sharpe=1.0, span_years=2.0, trial_count=trials)
            events = compute_power_events(
                effect_ratio=0.10, n_events=400, n_clusters=100, icc=0.10,
                trial_count=trials)
            assert events["z_threshold"] == sharpe["z_threshold"]

    def test_degenerate_inputs_raise_rather_than_returning_a_number(self):
        from proofhouse.eval.power import compute_power_events

        with pytest.raises(ValueError, match="n_events"):
            compute_power_events(0.1, 0, 10, 0.1, 1)
        with pytest.raises(ValueError, match="n_clusters"):
            compute_power_events(0.1, 400, 0, 0.1, 1)
        with pytest.raises(ValueError, match="exceeds n_events"):
            compute_power_events(0.1, 100, 400, 0.1, 1)
        with pytest.raises(ValueError, match="icc"):
            compute_power_events(0.1, 400, 100, 1.5, 1)

    def test_h_2026_pfr1_reproduces_its_own_recorded_power(self):
        """A real registration, not a synthesized one.

        PFR1's power (0.863) was computed outside proofhouse and pasted into
        prose because the Sharpe rule could not consume its bps-denominated
        effect. This rule recovers it from the registered inputs alone, which
        is the whole point of the fill mechanism.
        """
        from proofhouse.eval.power import compute_power_events

        result = compute_power_events(
            effect_ratio=0.1361, n_events=950, n_clusters=279, icc=0.067,
            trial_count=4)

        assert result["n_eff"] == pytest.approx(818.1641390678, abs=1e-6)
        assert result["z_effect"] == pytest.approx(3.8929456922, abs=1e-9)
        assert result["power"] == pytest.approx(0.8630381045, abs=1e-9)
        assert result["pct_of_cap"] == pytest.approx(0.196477, abs=1e-6)
        assert result["saturated"] is False
