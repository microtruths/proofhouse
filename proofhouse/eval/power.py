# proofhouse.eval.power — registration-time power check
"""
Phase 1 Build Plan §1a — Power check.

Parent plan §5.6:
    At register, compute from expected_effect_size, the validation
    window span, the current per-universe ledger count, and the
    feasibility block: the probability of passing the gate if the
    effect is real at the claimed size, net of costs and quantization
    drag at the intended sleeve size.

    Power < 0.5 → flagged, stored, printed on every report.

    The stored power_check block records all inputs, so a later
    reader can re-derive independently.

Two failure modes it catches:
    1. A strategy whose edge is real but smaller than the quantization
       drag at the intended sleeve size (feasibility, not statistics).
    2. A class like regime-prediction whose calendar span looks adequate
       while its count of independent events does not.
"""

import math

from proofhouse.eval.stats import _expected_max_n_standard_normals


def _phi(z):
    """Standard normal CDF (Abramowitz & Stegun approximation).

    Accurate to ~1e-7 for all z.
    """
    # Use the error function identity: Φ(z) = 0.5 * (1 + erf(z / √2))
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def se_sharpe(sharpe, span_years):
    """Compute the standard error of a Sharpe estimate.

    SE ≈ √((1 + S²/2) / T), T in calendar years.

    This is the iid case. Autocorrelation makes it worse.

    Args:
        sharpe: The true (or expected) Sharpe ratio (annualized).
        span_years: Validation window span in calendar years.

    Returns:
        float: Standard error of the Sharpe estimate.
    """
    if span_years <= 0:
        raise ValueError("span_years must be positive, got {}".format(span_years))
    return math.sqrt((1.0 + sharpe ** 2 / 2.0) / span_years)


def compute_power_check(expected_sharpe, span_years, trial_count,
                        threshold_quantile=0.95):
    """Compute the registration-time power check.

    Power = P(reject H0 | H1 is true at expected_sharpe)
          = Φ(z_effect - z_threshold)

    where z_effect = expected_sharpe / SE(sharpe)
    and z_threshold accounts for multiple testing via the expected
    maximum of trial_count standard normal draws.

    Args:
        expected_sharpe: The expected annualized Sharpe if the effect is real.
        span_years: Validation window span in calendar years.
        trial_count: Number of trials from the ledger.
        threshold_quantile: Gate threshold quantile (default 0.95 → one-sided).

    Returns:
        Dict with keys:
            power: Probability of passing gate if effect is real.
            flagged: True if power < 0.5.
            se: Standard error of the Sharpe estimate.
            z_effect: z-score of the expected effect.
            z_threshold: z-score threshold including multiple testing.
            inputs: Dict of all inputs for re-derivation.
    """
    se = se_sharpe(expected_sharpe, span_years)
    z_effect = expected_sharpe / se

    # Multiple testing threshold: the expected maximum of trial_count
    # standard normal draws. With 1 trial, this is 0 (no correction).
    e_max = _expected_max_n_standard_normals(trial_count)

    # The DSR multiple-testing correction is additive: the observed
    # Sharpe must clear the baseline significance threshold z_alpha
    # PLUS the expected noise maximum E[max(N)]. With trial_count=1,
    # e_max=0 and the threshold is just z_alpha.
    z_alpha = _phi_inv(threshold_quantile)
    z_threshold = z_alpha + e_max

    # Power = P(z_obs > z_threshold | H1)
    # Under H1: z_obs ~ N(z_effect, 1)
    # Power = Φ(z_effect - z_threshold)
    power = _phi(z_effect - z_threshold)

    return {
        "power": power,
        "flagged": power < 0.5,
        "se": se,
        "z_effect": z_effect,
        "z_threshold": z_threshold,
        "inputs": {
            "expected_sharpe": expected_sharpe,
            "span_years": span_years,
            "trial_count": trial_count,
            "threshold_quantile": threshold_quantile,
        },
    }


def affordable_n(expected_sharpe, span_years, target_power=0.5,
                 threshold_quantile=0.95):
    """Compute the affordable number of trials (derived budget).

    Solves compute_power_check for the largest N where power ≥ target_power.
    This is the derived budget — affordable N from the power calculation
    solved for N.

    Args:
        expected_sharpe: Expected Sharpe if real.
        span_years: Validation window span in years.
        target_power: Minimum acceptable power (default 0.5).
        threshold_quantile: Gate threshold quantile.

    Returns:
        int: Maximum affordable N.
    """
    se = se_sharpe(expected_sharpe, span_years)
    z_effect = expected_sharpe / se

    # Binary search for the largest N where power >= target_power
    lo, hi = 1, 10000

    # Check if even N=1 is feasible
    result_1 = compute_power_check(expected_sharpe, span_years, 1,
                                    threshold_quantile)
    if result_1["power"] < target_power:
        return 0  # Can't even afford 1 trial

    while lo < hi:
        mid = (lo + hi + 1) // 2
        result = compute_power_check(expected_sharpe, span_years, mid,
                                      threshold_quantile)
        if result["power"] >= target_power:
            lo = mid
        else:
            hi = mid - 1

    return lo


def _phi_inv(p):
    """Approximate inverse standard normal CDF.

    Uses the rational approximation from Abramowitz & Stegun (26.2.23).
    Accurate to ~4.5e-4.
    """
    if p <= 0 or p >= 1:
        raise ValueError("p must be in (0, 1), got {}".format(p))

    if p < 0.5:
        return -_phi_inv(1.0 - p)

    # Rational approximation for p in [0.5, 1)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    c0 = 2.515517
    c1 = 0.802853
    c2 = 0.010328
    d1 = 1.432788
    d2 = 0.189269
    d3 = 0.001308

    return t - (c0 + c1 * t + c2 * t ** 2) / (1.0 + d1 * t + d2 * t ** 2 + d3 * t ** 3)
