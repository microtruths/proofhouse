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

TWO RULES, AND WHY
-------------------
`compute_power_check` is Sharpe-denominated, and its z_effect converges to
sqrt(2T) — a ceiling that depends ONLY on the span. At 3 two-month blocks it
is 1.000 against a 1.645 threshold, so power asymptotes at 0.259 for any
effect size, Sharpe 100 included. No design clears it by expecting more.

`compute_power_events` scores designs whose information scales with the
EVENT COUNT rather than the calendar. Both share one multiple-testing
threshold, deliberately: see the parity test.

Two power rules is a new degree of freedom — a researcher could pick the
flattering one. The protocol covers it (both are pinned before data, and
changing a registered rule is an amendment), but the freedom is real and is
named here rather than discovered later.

ON _phi_inv's ACCURACY
-----------------------
`_phi_inv` is the Abramowitz & Stegun 26.2.23 rational approximation, error
bounded at 4.5e-4: it returns 1.6452114401 for p=0.95 where the true value
is 1.6448536270. That shifts power by ~1.4e-4, which decides nothing, and
it is now load-bearing in BOTH rules.

Kept rather than replaced, on purpose. An exact inverse would change the
Sharpe rule's fourth decimal, and numbers it has already produced sit in
records. Chasing 1e-4 by silently moving a shipped rule's outputs is the
worse trade. Hand-computed tests must therefore pin to what this function
RETURNS, not to the mathematics — deriving the expected value from theory
and asserting it against the implementation is failure catalog #22's shape.
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


def compute_power_events(effect_ratio, n_events, n_clusters, icc, trial_count,
                         threshold_quantile=0.95):
    """Power for a design whose information scales with events, not calendar.

    Power = Φ(z − z_threshold), with z = |effect_ratio| · √n_eff and n_eff the
    cluster-corrected sample size:

        m_bar = n_events / n_clusters
        deff  = 1 + (m_bar − 1) · icc
        n_eff = n_events / deff

    THE SATURATION, AND HOW IT DIFFERS FROM THE SHARPE CEILING. Substituting
    m_bar = n/G gives n_eff → G/icc as n grows with G fixed, so n_eff is
    bounded and more events eventually buy nothing. That much parallels the
    Sharpe rule. What does not parallel it: the Sharpe ceiling caps z_effect
    ITSELF and is independent of the effect size, so no strategy however good
    clears it. Here the cap binds the SAMPLE — z → |r|·√(G/icc) still rises
    without bound in r, so a large enough effect always clears any threshold.

    That is the reason this rule exists. But it does NOT mean event designs
    escape the span constraint, and the difference is easy to over-read: G is
    an event count, and event counts scale with the window. At r=0.05 and
    icc=0.067, G=10 gives power 0.15 where G=279 gives 0.94. The ceiling moved
    from √(2T) to √G with G >> T — a far better constant, not a different kind
    of constraint.

    icc IS NOT OPTIONAL and has no default. icc=0 asserts every event is
    independent: the most powerful and least defensible assumption available,
    and precisely the one that inflates power. Callers that mean it may
    register it explicitly, and then it is a claim someone made rather than a
    default nobody chose. See core/fill.py, which refuses without it.

    Args:
        effect_ratio: Mean per-event outcome ÷ per-event standard deviation.
        n_events: Events expected in the validation window.
        n_clusters: Clusters those events fall into.
        icc: Intra-cluster correlation of the outcome.
        trial_count: Prior trials, for the multiple-testing threshold.
        threshold_quantile: Gate threshold quantile (default 0.95, one-sided).

    Returns:
        Dict with power, flagged, saturated, and every intermediate needed to
        re-derive the number rather than trust it.
    """
    if n_events <= 0:
        raise ValueError("n_events must be positive, got {}".format(n_events))
    if n_clusters <= 0:
        raise ValueError(
            "n_clusters must be positive, got {}".format(n_clusters))
    if n_clusters > n_events:
        raise ValueError(
            "n_clusters ({}) exceeds n_events ({}) — every cluster would hold "
            "less than one event.".format(n_clusters, n_events))
    if not 0.0 <= icc <= 1.0:
        raise ValueError(
            "icc must be in [0, 1], got {}. Outside that range deff can go "
            "non-positive and n_eff becomes meaningless rather than "
            "wrong.".format(icc))

    m_bar = n_events / float(n_clusters)
    deff = 1.0 + (m_bar - 1.0) * icc
    n_eff = n_events / deff
    z_effect = abs(effect_ratio) * math.sqrt(n_eff)

    # Shared with compute_power_check. One correction, checked by a parity
    # test — two that could drift apart is worse than one rule.
    e_max = _expected_max_n_standard_normals(trial_count)
    z_threshold = _phi_inv(threshold_quantile) + e_max

    power = _phi(z_effect - z_threshold)

    # icc=0 means no saturation at all: n_eff = n, unbounded. Reported as
    # None rather than infinity — a record carrying "inf" reads as a number
    # to anything that parses it back, which is the same trap V-15 exists to
    # close for thresholds. Absent says "there is no cap"; "inf" says "the
    # cap is a value you cannot compute with".
    n_eff_cap = (n_clusters / float(icc)) if icc > 0 else None
    pct_of_cap = (n_eff / n_eff_cap) if n_eff_cap else None

    return {
        "power": power,
        "flagged": power < 0.5,
        # Deliberately NOT folded into `flagged`. That flag means one thing
        # today — power below 0.5 — and overloading it would make an existing
        # report line ambiguous about which problem it is reporting.
        "saturated": pct_of_cap is not None and pct_of_cap > 0.8,
        "m_bar": m_bar,
        "deff": deff,
        "n_eff": n_eff,
        "n_eff_cap": n_eff_cap,
        "pct_of_cap": pct_of_cap,
        "z_effect": z_effect,
        "z_threshold": z_threshold,
        "inputs": {
            "effect_ratio": effect_ratio,
            "n_events": n_events,
            "n_clusters": n_clusters,
            "icc": icc,
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
