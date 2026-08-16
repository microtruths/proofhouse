# tests/test_costs.py — Hand-computed tests for cost models
"""
Phase 1 Build Plan §1a — Cost model tests.

Tests for fee/spread/slippage models and quantization drag
from the $10 order floor.
"""

import math


class TestCostModel:
    """Cost model application tests."""

    def test_taker_fee_reduces_returns(self):
        """Taker fees reduce returns on each trade.

        Hand computation:
            returns = [0.05, -0.03, 0.02]
            trades_per_period = [2, 1, 2]  (round-trips per period)
            taker_fee = 0.00035 (3.5 bps per side)
            cost_per_roundtrip = 2 * 0.00035 = 0.0007

            adjusted[0] = 0.05 - 2 * 0.0007 = 0.05 - 0.0014 = 0.0486
            adjusted[1] = -0.03 - 1 * 0.0007 = -0.0307
            adjusted[2] = 0.02 - 2 * 0.0007 = 0.02 - 0.0014 = 0.0186
        """
        from proofhouse.eval.costs import TakerCostModel

        model = TakerCostModel(taker_fee_per_side=0.00035)
        returns = [0.05, -0.03, 0.02]
        trades = [2, 1, 2]

        adjusted = model.apply(returns, trades)

        assert abs(adjusted[0] - 0.0486) < 1e-10, "Period 0: expected 0.0486, got {}".format(adjusted[0])
        assert abs(adjusted[1] - (-0.0307)) < 1e-10, "Period 1: expected -0.0307, got {}".format(adjusted[1])
        assert abs(adjusted[2] - 0.0186) < 1e-10, "Period 2: expected 0.0186, got {}".format(adjusted[2])

    def test_zero_fee_no_change(self):
        """Zero taker fee leaves returns unchanged."""
        from proofhouse.eval.costs import TakerCostModel

        model = TakerCostModel(taker_fee_per_side=0.0)
        returns = [0.05, -0.03]
        trades = [2, 1]

        adjusted = model.apply(returns, trades)
        for i, (r, a) in enumerate(zip(returns, adjusted)):
            assert abs(r - a) < 1e-10, "Period {}: expected {}, got {}".format(i, r, a)


class TestQuantizationDrag:
    """Quantization drag from the $10 order floor."""

    def test_implied_min_rebalance_pct(self):
        """Compute implied_min_rebalance_pct from sleeve and instruments.

        Hand computation:
            sleeve_usd = 2000
            instruments_held = 8
            position_size = 2000 / 8 = 250
            min_trade_notional = 10
            implied_min_rebalance_pct = 10 / 250 = 0.04 (4%)
        """
        from proofhouse.eval.costs import implied_min_rebalance_pct

        result = implied_min_rebalance_pct(
            sleeve_usd=2000,
            instruments_held=8,
            min_trade_notional_usd=10,
        )
        assert abs(result - 0.04) < 1e-10, "Expected 0.04. Got {}".format(result)

    def test_small_sleeve_high_drag(self):
        """Small sleeve → high implied rebalance floor.

        sleeve_usd = 200
        instruments_held = 8
        position_size = 200 / 8 = 25
        implied_min_rebalance_pct = 10 / 25 = 0.40 (40%)
        """
        from proofhouse.eval.costs import implied_min_rebalance_pct

        result = implied_min_rebalance_pct(
            sleeve_usd=200,
            instruments_held=8,
            min_trade_notional_usd=10,
        )
        assert abs(result - 0.40) < 1e-10, "Expected 0.40. Got {}".format(result)

    def test_unexecutable_spec_flagged(self):
        """Spec is unexecutable when implied_min > rebalance_band.

        sleeve_usd = 200, instruments = 8 → implied = 40%
        rebalance_band = 0.15 → 40% > 15% → unexecutable
        """
        from proofhouse.eval.costs import check_feasibility

        result = check_feasibility(
            sleeve_usd=200,
            instruments_held=8,
            min_trade_notional_usd=10,
            rebalance_band=0.15,
        )
        assert not result["executable"], "Spec should be flagged as unexecutable"
        assert result["implied_min_rebalance_pct"] > result["rebalance_band"]

    def test_executable_spec_passes(self):
        """Spec is executable when implied_min <= rebalance_band.

        sleeve_usd = 2000, instruments = 8 → implied = 4%
        rebalance_band = 0.15 → 4% < 15% → executable
        """
        from proofhouse.eval.costs import check_feasibility

        result = check_feasibility(
            sleeve_usd=2000,
            instruments_held=8,
            min_trade_notional_usd=10,
            rebalance_band=0.15,
        )
        assert result["executable"], "Spec should be executable"


class TestMinViableSleeve:
    """Minimum viable sleeve computation."""

    def test_min_viable_sleeve(self):
        """Compute the smallest sleeve where the strategy is executable.

        Hand computation:
            instruments_held = 8
            min_trade_notional = 10
            rebalance_band = 0.15

            At min viable sleeve:
                position_size = sleeve / 8
                min_trade_notional / position_size = rebalance_band
                10 / (sleeve / 8) = 0.15
                80 / sleeve = 0.15
                sleeve = 80 / 0.15 = 533.33...
        """
        from proofhouse.eval.costs import min_viable_sleeve_usd

        result = min_viable_sleeve_usd(
            instruments_held=8,
            min_trade_notional_usd=10,
            rebalance_band=0.15,
        )
        expected = 80.0 / 0.15
        assert abs(result - expected) < 1e-10, (
            "Expected {}. Got {}".format(expected, result)
        )
