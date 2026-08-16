# proofhouse.eval.costs — fee, spread, slippage, and quantization models
"""
Phase 1 Build Plan §1a — Cost models.

Parent plan §4 eval/costs/:
    Fee/spread/slippage, applied at scoring time.

Parent plan §5.1 feasibility:
    min_trade_notional_usd: Hyperliquid FLOOR on any single order.
    Quantization drag from skipped sub-floor rebalances.

Phase 1 plan: unexecutable specs are flagged retroactively (mirrors
power_check pattern). Registration records inputs; Phase 1 computes
and flags. This avoids giving Phase 0's register a Phase 1 dependency.

B4: sleeve_range in, min_viable_sleeve_usd OUT.
"""


class TakerCostModel:
    """Taker fee cost model.

    Applies a fixed per-side taker fee to each round-trip trade.
    Cost per round-trip = 2 × taker_fee_per_side.

    Attributes:
        taker_fee_per_side: Fee per side as a decimal (e.g., 0.00035 = 3.5 bps).
    """

    def __init__(self, taker_fee_per_side):
        """Initialize with fee per side.

        Args:
            taker_fee_per_side: Fee per side as decimal.
        """
        self.taker_fee_per_side = taker_fee_per_side

    def apply(self, returns, trades_per_period):
        """Adjust returns for taker fees.

        Args:
            returns: List of period returns.
            trades_per_period: List of round-trip trade counts per period.

        Returns:
            List of adjusted returns.
        """
        if len(returns) != len(trades_per_period):
            raise ValueError(
                "returns and trades_per_period must have same length. "
                "Got {} and {}".format(len(returns), len(trades_per_period))
            )

        cost_per_roundtrip = 2.0 * self.taker_fee_per_side
        return [
            r - t * cost_per_roundtrip
            for r, t in zip(returns, trades_per_period)
        ]


def implied_min_rebalance_pct(sleeve_usd, instruments_held, min_trade_notional_usd):
    """Compute the minimum rebalance percentage from the order floor.

    The $10 (or configured) minimum order size creates a floor below which
    adjustments cannot be executed. This function computes that floor as a
    fraction of position size.

    implied_min_rebalance_pct = min_trade_notional / (sleeve / instruments)

    Args:
        sleeve_usd: Total sleeve capital in USD.
        instruments_held: Number of instruments in the portfolio.
        min_trade_notional_usd: Minimum order size in USD (e.g., 10).

    Returns:
        float: The minimum rebalance percentage (e.g., 0.04 = 4%).
    """
    if sleeve_usd <= 0:
        raise ValueError("sleeve_usd must be positive, got {}".format(sleeve_usd))
    if instruments_held <= 0:
        raise ValueError("instruments_held must be positive, got {}".format(instruments_held))

    position_size = sleeve_usd / instruments_held
    return min_trade_notional_usd / position_size


def check_feasibility(sleeve_usd, instruments_held, min_trade_notional_usd,
                      rebalance_band):
    """Check whether a strategy spec is executable at the given sleeve size.

    A spec is unexecutable if the implied minimum rebalance percentage
    exceeds the registered rebalance band — the strategy cannot make the
    trades it needs to make.

    This is flagged retroactively per the power_check pattern, not refused
    at registration.

    Args:
        sleeve_usd: Total sleeve capital in USD.
        instruments_held: Number of instruments.
        min_trade_notional_usd: Minimum order size in USD.
        rebalance_band: Registered rebalance band (e.g., 0.15 = 15%).

    Returns:
        Dict with keys:
            executable: bool — True if the spec can be executed.
            implied_min_rebalance_pct: The computed floor.
            rebalance_band: The registered band (for comparison).
            position_size_usd: Per-instrument position size.
    """
    implied = implied_min_rebalance_pct(sleeve_usd, instruments_held,
                                        min_trade_notional_usd)
    position_size = sleeve_usd / instruments_held

    return {
        "executable": implied <= rebalance_band,
        "implied_min_rebalance_pct": implied,
        "rebalance_band": rebalance_band,
        "position_size_usd": position_size,
    }


def min_viable_sleeve_usd(instruments_held, min_trade_notional_usd,
                           rebalance_band):
    """Compute the minimum sleeve at which the strategy is executable.

    Solves: min_trade_notional / (sleeve / instruments) = rebalance_band
    → sleeve = min_trade_notional × instruments / rebalance_band

    B4: This is an output, not an input. It feeds the power check.

    Args:
        instruments_held: Number of instruments.
        min_trade_notional_usd: Minimum order size in USD.
        rebalance_band: Registered rebalance band.

    Returns:
        float: Minimum viable sleeve in USD.
    """
    if rebalance_band <= 0:
        raise ValueError("rebalance_band must be positive, got {}".format(rebalance_band))

    return min_trade_notional_usd * instruments_held / rebalance_band
