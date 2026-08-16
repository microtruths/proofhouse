# tests/test_stats.py — Hand-computed tests for statistical functions
"""
Phase 1 Build Plan §1a — Stats tests.

Hand-computed values BEFORE implementation, per parent plan §11:
    "DSR on a hand-built 5-trial ledger"
    "gate fail at exactly-threshold"

All expected values computed by hand and documented inline.
"""

import math


class TestDeflatedSharpe:
    """Deflated Sharpe Ratio tests with hand-computed expected values."""

    def test_dsr_single_trial_equals_raw(self):
        """DSR with trial_count=1 equals the raw Sharpe.

        With only one trial, there is no multiple-testing penalty.
        Hand computation:
            returns = [0.01, 0.02, -0.005, 0.015, 0.01]
            mean = (0.01 + 0.02 - 0.005 + 0.015 + 0.01) / 5 = 0.01
            std = sqrt(sum((r - 0.01)^2) / (5-1))
                = sqrt((0 + 0.0001 + 0.000225 + 0.000025 + 0) / 4)
                = sqrt(0.0000875) ≈ 0.009354
            sharpe = 0.01 / 0.009354 ≈ 1.069
            With 1 trial, DSR haircut is zero → DSR ≈ 1.069
        """
        from proofhouse.eval.stats import deflated_sharpe

        returns = [0.01, 0.02, -0.005, 0.015, 0.01]
        result = deflated_sharpe(returns, trial_count=1)

        # Raw Sharpe
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        raw_sharpe = mean / math.sqrt(variance)

        assert abs(result - raw_sharpe) < 1e-10, (
            "DSR with trial_count=1 should equal raw Sharpe. "
            "Got {}, expected {}".format(result, raw_sharpe)
        )

    def test_dsr_multiple_trials_reduces(self):
        """DSR decreases with more trials.

        Hand computation:
            Same returns as above → raw Sharpe ≈ 1.069
            With N trials, expected max of N iid N(0,1) draws ≈ E[max].
            DSR = Sharpe - E[max] * SE(Sharpe)
            At N=5, E[max] ≈ 1.163 (exact from order statistics)
            SE(Sharpe) ≈ sqrt((1 + S²/2) / T) where T = n observations
            SE ≈ sqrt((1 + 1.069²/2) / 5) = sqrt(1.571 / 5) ≈ 0.561
            haircut ≈ 1.163 * 0.561 ≈ 0.652
            DSR ≈ 1.069 - 0.652 ≈ 0.417
        """
        from proofhouse.eval.stats import deflated_sharpe

        returns = [0.01, 0.02, -0.005, 0.015, 0.01]
        dsr_1 = deflated_sharpe(returns, trial_count=1)
        dsr_5 = deflated_sharpe(returns, trial_count=5)

        assert dsr_5 < dsr_1, (
            "DSR should decrease with more trials. "
            "trial_count=1: {}, trial_count=5: {}".format(dsr_1, dsr_5)
        )

    def test_dsr_zero_sharpe_goes_negative(self):
        """DSR of a zero-Sharpe strategy is negative with multiple trials.

        A strategy with no edge has Sharpe ≈ 0. After the multiple-testing
        haircut, DSR should be negative — it's noise.
        """
        from proofhouse.eval.stats import deflated_sharpe

        # Returns that average near zero
        returns = [0.01, -0.01, 0.005, -0.005, 0.002, -0.002]
        dsr = deflated_sharpe(returns, trial_count=10)

        assert dsr < 0, (
            "Zero-Sharpe strategy with 10 trials should have negative DSR. "
            "Got {}".format(dsr)
        )

    def test_dsr_trial_count_from_ledger_not_parameter(self):
        """DSR must accept trial_count as an explicit argument.

        The constraint is that the value comes FROM the ledger at call time.
        This test verifies the interface — the caller is responsible for
        reading the ledger, not the stats function.
        """
        from proofhouse.eval.stats import deflated_sharpe

        returns = [0.01, 0.02, -0.005]
        # Just verify it accepts the argument and produces a number
        result = deflated_sharpe(returns, trial_count=3)
        assert isinstance(result, (int, float))


class TestPanelIC:
    """Information Coefficient tests with hand-computed values."""

    def test_ic_perfect_prediction(self):
        """IC = 1.0 for perfect rank prediction.

        Hand computation:
            predictions = [1, 2, 3, 4, 5]
            outcomes    = [1, 2, 3, 4, 5]
            Spearman rank correlation = 1.0
        """
        from proofhouse.eval.stats import panel_ic

        predictions = [1.0, 2.0, 3.0, 4.0, 5.0]
        outcomes = [1.0, 2.0, 3.0, 4.0, 5.0]

        ic = panel_ic(predictions, outcomes)
        assert abs(ic - 1.0) < 1e-10, "Perfect prediction should give IC=1.0. Got {}".format(ic)

    def test_ic_inverse_prediction(self):
        """IC = -1.0 for perfectly inverted prediction.

        Hand computation:
            predictions = [5, 4, 3, 2, 1]
            outcomes    = [1, 2, 3, 4, 5]
            Spearman rank correlation = -1.0
        """
        from proofhouse.eval.stats import panel_ic

        predictions = [5.0, 4.0, 3.0, 2.0, 1.0]
        outcomes = [1.0, 2.0, 3.0, 4.0, 5.0]

        ic = panel_ic(predictions, outcomes)
        assert abs(ic - (-1.0)) < 1e-10, "Inverse prediction should give IC=-1.0. Got {}".format(ic)

    def test_ic_no_correlation(self):
        """IC ≈ 0 for uncorrelated data.

        Hand computation:
            predictions = [1, 2, 3, 4, 5]
            outcomes    = [3, 1, 4, 5, 2]
            Ranks pred: [1, 2, 3, 4, 5]
            Ranks out:  [3, 1, 4, 5, 2]
            d = [1-3, 2-1, 3-4, 4-5, 5-2] = [-2, 1, -1, -1, 3]
            d² = [4, 1, 1, 1, 9] = 16
            rho = 1 - 6*16/(5*(25-1)) = 1 - 96/120 = 1 - 0.8 = 0.2
        """
        from proofhouse.eval.stats import panel_ic

        predictions = [1.0, 2.0, 3.0, 4.0, 5.0]
        outcomes = [3.0, 1.0, 4.0, 5.0, 2.0]

        ic = panel_ic(predictions, outcomes)
        assert abs(ic - 0.2) < 1e-10, "Expected IC=0.2. Got {}".format(ic)


class TestHitRate:
    """Hit rate tests with hand-computed values."""

    def test_hit_rate_all_correct(self):
        """Hit rate = 1.0 when all predictions match direction.

        predictions = [1, 1, 1, 1]
        outcomes    = [0.5, 0.3, 0.1, 0.2]  (all positive → match)
        hit rate = 4/4 = 1.0
        """
        from proofhouse.eval.stats import hit_rate

        predictions = [1.0, 1.0, 1.0, 1.0]
        outcomes = [0.5, 0.3, 0.1, 0.2]

        hr = hit_rate(predictions, outcomes)
        assert abs(hr - 1.0) < 1e-10, "All correct should give 1.0. Got {}".format(hr)

    def test_hit_rate_none_correct(self):
        """Hit rate = 0.0 when all predictions are wrong.

        predictions = [1, 1, 1]
        outcomes    = [-0.5, -0.3, -0.1]  (all negative → mismatch)
        hit rate = 0/3 = 0.0
        """
        from proofhouse.eval.stats import hit_rate

        predictions = [1.0, 1.0, 1.0]
        outcomes = [-0.5, -0.3, -0.1]

        hr = hit_rate(predictions, outcomes)
        assert abs(hr) < 1e-10, "All wrong should give 0.0. Got {}".format(hr)

    def test_hit_rate_mixed(self):
        """Hit rate = 0.6 for 3 correct out of 5.

        predictions = [1, -1, 1, -1, 1]
        outcomes    = [0.5, -0.3, -0.1, -0.2, 0.1]
        matches: [Y, Y, N, Y, Y] → 4 wrong: 3rd is pred=1,out=-0.1 mismatch
        Wait, let me recompute:
            pred=1, out=0.5 → same sign → hit
            pred=-1, out=-0.3 → same sign → hit
            pred=1, out=-0.1 → diff sign → miss
            pred=-1, out=-0.2 → same sign → hit
            pred=1, out=0.1 → same sign → hit
        hits = 4/5 = 0.8
        """
        from proofhouse.eval.stats import hit_rate

        predictions = [1.0, -1.0, 1.0, -1.0, 1.0]
        outcomes = [0.5, -0.3, -0.1, -0.2, 0.1]

        hr = hit_rate(predictions, outcomes)
        assert abs(hr - 0.8) < 1e-10, "Expected 0.8. Got {}".format(hr)


class TestAlphaVsBenchmark:
    """Alpha vs benchmark tests with hand-computed values."""

    def test_alpha_zero_benchmark(self):
        """Alpha against a zero benchmark equals mean return.

        Hand computation:
            returns   = [0.02, 0.01, 0.03, 0.01, 0.03]
            benchmark = [0.0, 0.0, 0.0, 0.0, 0.0]
            excess = returns - benchmark = returns
            alpha = mean(excess) = 0.02
        """
        from proofhouse.eval.stats import alpha_vs_benchmark

        returns = [0.02, 0.01, 0.03, 0.01, 0.03]
        benchmark = [0.0, 0.0, 0.0, 0.0, 0.0]

        result = alpha_vs_benchmark(returns, benchmark)
        assert abs(result["alpha"] - 0.02) < 1e-10, (
            "Alpha vs zero benchmark should be mean return. Got {}".format(result["alpha"])
        )

    def test_alpha_equal_to_benchmark(self):
        """Alpha = 0 when returns match benchmark.

        returns = benchmark → excess = 0 → alpha = 0
        """
        from proofhouse.eval.stats import alpha_vs_benchmark

        returns = [0.02, -0.01, 0.03]
        benchmark = [0.02, -0.01, 0.03]

        result = alpha_vs_benchmark(returns, benchmark)
        assert abs(result["alpha"]) < 1e-10, (
            "Alpha should be zero when returns match benchmark. Got {}".format(result["alpha"])
        )

    def test_alpha_negative(self):
        """Alpha < 0 when underperforming benchmark.

        returns   = [0.01, 0.01, 0.01]
        benchmark = [0.03, 0.03, 0.03]
        excess = [-0.02, -0.02, -0.02]
        alpha = -0.02
        """
        from proofhouse.eval.stats import alpha_vs_benchmark

        returns = [0.01, 0.01, 0.01]
        benchmark = [0.03, 0.03, 0.03]

        result = alpha_vs_benchmark(returns, benchmark)
        assert abs(result["alpha"] - (-0.02)) < 1e-10


class TestBenchmarkSensitivity:
    """Benchmark sensitivity sweep tests."""

    def test_sign_flip_detected(self):
        """Sign flip across benchmarks is flagged.

        returns   = [0.04, 0.02, 0.03]
        btc       = [0.01, 0.01, 0.01]  → alpha = 0.02 (positive)
        oi_index  = [0.05, 0.05, 0.05]  → alpha = -0.02 (negative)

        This is the AgentCopy §13 case: same data, different benchmarks,
        the sign flips. benchmark_sensitivity must flag this.
        """
        from proofhouse.eval.stats import benchmark_sensitivity

        returns = [0.04, 0.02, 0.03]
        benchmarks = {
            "btc-subtraction": [0.01, 0.01, 0.01],
            "oi-weighted-index": [0.05, 0.05, 0.05],
        }

        result = benchmark_sensitivity(returns, benchmarks)

        # Check that at least two benchmarks are present
        assert len(result["benchmarks"]) >= 2, "Need ≥2 benchmark results"

        # Check sign flip is flagged
        assert result["sign_flip_detected"], (
            "Sign flip across benchmarks should be detected. "
            "btc gives positive alpha, oi-index gives negative."
        )

    def test_no_sign_flip_when_consistent(self):
        """No sign flip when all benchmarks agree.

        returns   = [0.04, 0.05, 0.06]
        btc       = [0.01, 0.01, 0.01]  → alpha = 0.04 (positive)
        eth       = [0.02, 0.02, 0.02]  → alpha = 0.03 (positive)
        Both positive → no sign flip.
        """
        from proofhouse.eval.stats import benchmark_sensitivity

        returns = [0.04, 0.05, 0.06]
        benchmarks = {
            "btc-subtraction": [0.01, 0.01, 0.01],
            "eth-subtraction": [0.02, 0.02, 0.02],
        }

        result = benchmark_sensitivity(returns, benchmarks)
        assert not result["sign_flip_detected"], (
            "Consistent benchmarks should not flag a sign flip."
        )

    def test_requires_at_least_two_benchmarks(self):
        """Sensitivity sweep requires ≥2 benchmarks."""
        from proofhouse.eval.stats import benchmark_sensitivity
        import pytest

        returns = [0.01, 0.02]
        benchmarks = {"btc": [0.01, 0.01]}  # Only one

        with pytest.raises(ValueError, match="at least 2"):
            benchmark_sensitivity(returns, benchmarks)
