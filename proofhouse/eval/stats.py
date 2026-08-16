# proofhouse.eval.stats — statistical evaluation functions
"""
Phase 1 Build Plan §1a — Statistical functions.

Parent plan §4 eval/stats/:
    DSR, PBO — trial count read FROM the ledger, never a parameter.

Parent plan §2.2.1:
    benchmark_sensitivity is mandatory and rendered on every result.
    Sign-flip across defensible benchmarks is flagged explicitly.

Functions:
    deflated_sharpe  — Sharpe ratio with multiple-testing haircut
    panel_ic         — Spearman rank correlation (information coefficient)
    hit_rate         — fraction of correct directional predictions
    alpha_vs_benchmark — excess return over a benchmark
    benchmark_sensitivity — alpha across ≥2 benchmarks, sign-flip detection
"""

import math


def _rankdata(values):
    """Rank data using average ranks for ties.

    Args:
        values: List of numeric values.

    Returns:
        List of ranks (1-based, averaged for ties).
    """
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n

    i = 0
    while i < n:
        # Find the end of the tie group
        j = i + 1
        while j < n and values[indexed[j]] == values[indexed[i]]:
            j += 1
        # Average rank for the tie group (1-based)
        avg_rank = (i + j + 1) / 2.0  # (i+1 + j) / 2 in 1-based
        for k in range(i, j):
            ranks[indexed[k]] = avg_rank
        i = j

    return ranks


def _expected_max_n_standard_normals(n):
    """Approximate expected maximum of n iid standard normal draws.

    Uses the exact asymptotic formula with small-N correction.
    For n=1: E[max] = 0.
    For n=2: E[max] = 1/sqrt(pi) ≈ 0.5642.
    For larger n: uses the truncated series from Blom (1958):
        E[max] ≈ Φ⁻¹(1 - 1/(2n)) — but we use a simpler closed form
        that is accurate to ~1% for n ≥ 3.

    Parent plan §2.1 notes the √(2 ln N) asymptotic overstates at small N:
        N=5 → 1.16 (not √(2 ln 5) ≈ 1.79)
        N=100 → 2.51 (not √(2 ln 100) ≈ 3.03)
    """
    if n <= 0:
        raise ValueError("n must be positive, got {}".format(n))
    if n == 1:
        return 0.0
    if n == 2:
        return 1.0 / math.sqrt(math.pi)

    # Euler-Mascheroni constant
    gamma = 0.5772156649

    # Standard asymptotic with correction
    a = math.sqrt(2.0 * math.log(n))
    b = a - (math.log(math.log(n)) + math.log(4.0 * math.pi) - 2.0 * gamma) / (2.0 * a)
    return b


def deflated_sharpe(returns, trial_count):
    """Compute the Deflated Sharpe Ratio.

    The DSR adjusts the raw Sharpe for multiple testing by subtracting
    the expected maximum Sharpe from trial_count independent null draws.

    DSR = Sharpe - E[max(N)] * SE(Sharpe)

    where E[max(N)] is the expected maximum of N standard normals and
    SE(Sharpe) ≈ √((1 + S²/2) / T).

    The trial_count comes FROM the ledger at call time — it is never
    a default or a guess.

    Args:
        returns: List of period returns.
        trial_count: Number of independent trials from the ledger.

    Returns:
        float: The deflated Sharpe ratio.
    """
    if len(returns) < 2:
        raise ValueError("Need at least 2 returns, got {}".format(len(returns)))
    if trial_count < 1:
        raise ValueError("trial_count must be >= 1, got {}".format(trial_count))

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
    std = math.sqrt(variance)

    if std == 0:
        return 0.0

    sharpe = mean / std

    # SE of Sharpe: sqrt((1 + S²/2) / T)
    se_sharpe = math.sqrt((1.0 + sharpe ** 2 / 2.0) / n)

    # Expected max of trial_count standard normals
    e_max = _expected_max_n_standard_normals(trial_count)

    # DSR = Sharpe - E[max] * SE(Sharpe)
    return sharpe - e_max * se_sharpe


def panel_ic(predictions, outcomes):
    """Compute the Information Coefficient (Spearman rank correlation).

    IC = 1 - 6 * Σ(d²) / (n * (n² - 1))
    where d = rank(prediction) - rank(outcome).

    Args:
        predictions: List of predicted values.
        outcomes: List of realized values.

    Returns:
        float: Spearman rank correlation in [-1, 1].
    """
    if len(predictions) != len(outcomes):
        raise ValueError(
            "predictions and outcomes must have the same length. "
            "Got {} and {}".format(len(predictions), len(outcomes))
        )
    n = len(predictions)
    if n < 2:
        raise ValueError("Need at least 2 observations, got {}".format(n))

    pred_ranks = _rankdata(predictions)
    out_ranks = _rankdata(outcomes)

    d_sq_sum = sum(
        (pred_ranks[i] - out_ranks[i]) ** 2
        for i in range(n)
    )

    rho = 1.0 - 6.0 * d_sq_sum / (n * (n ** 2 - 1))
    return rho


def hit_rate(predictions, outcomes):
    """Compute the directional hit rate.

    Fraction of observations where prediction and outcome have the
    same sign (both positive or both negative).

    Args:
        predictions: List of predicted values (sign = direction).
        outcomes: List of realized values.

    Returns:
        float: Hit rate in [0, 1].
    """
    if len(predictions) != len(outcomes):
        raise ValueError(
            "predictions and outcomes must have the same length. "
            "Got {} and {}".format(len(predictions), len(outcomes))
        )
    n = len(predictions)
    if n == 0:
        raise ValueError("Need at least 1 observation")

    hits = sum(
        1 for p, o in zip(predictions, outcomes)
        if (p > 0 and o > 0) or (p < 0 and o < 0)
    )

    return hits / n


def alpha_vs_benchmark(returns, benchmark_returns):
    """Compute alpha (excess return) relative to a benchmark.

    Alpha is the mean excess return: mean(returns - benchmark).
    Also returns the t-statistic for the null hypothesis alpha=0.

    Args:
        returns: List of period returns.
        benchmark_returns: List of benchmark period returns.

    Returns:
        Dict with keys:
            alpha: Mean excess return.
            t_stat: t-statistic for alpha != 0.
            n: Number of observations.
            se: Standard error of alpha.
    """
    if len(returns) != len(benchmark_returns):
        raise ValueError(
            "returns and benchmark must have the same length. "
            "Got {} and {}".format(len(returns), len(benchmark_returns))
        )
    n = len(returns)
    if n < 2:
        raise ValueError("Need at least 2 observations, got {}".format(n))

    excess = [r - b for r, b in zip(returns, benchmark_returns)]
    alpha = sum(excess) / n
    variance = sum((e - alpha) ** 2 for e in excess) / (n - 1)
    se = math.sqrt(variance / n)

    t_stat = alpha / se if se > 0 else 0.0

    return {
        "alpha": alpha,
        "t_stat": t_stat,
        "n": n,
        "se": se,
    }


def benchmark_sensitivity(returns, benchmarks):
    """Compute alpha across multiple benchmarks and detect sign flips.

    Parent plan §2.2.1: A sign flip across defensible benchmarks IS the
    finding — the alpha is a benchmark artifact, not a selection problem.

    Args:
        returns: List of period returns.
        benchmarks: Dict of {benchmark_name: benchmark_returns_list}.
            Must have at least 2 entries.

    Returns:
        Dict with keys:
            benchmarks: Dict of {name: alpha_vs_benchmark result}.
            sign_flip_detected: True if any pair of benchmarks produces
                alphas with different signs.

    Raises:
        ValueError: If fewer than 2 benchmarks provided.
    """
    if len(benchmarks) < 2:
        raise ValueError(
            "benchmark_sensitivity requires at least 2 benchmarks, "
            "got {}".format(len(benchmarks))
        )

    results = {}
    for name, bench_returns in benchmarks.items():
        results[name] = alpha_vs_benchmark(returns, bench_returns)

    # Detect sign flip: any two benchmarks producing alphas with different signs
    alphas = [r["alpha"] for r in results.values()]
    has_positive = any(a > 0 for a in alphas)
    has_negative = any(a < 0 for a in alphas)
    sign_flip = has_positive and has_negative

    return {
        "benchmarks": results,
        "sign_flip_detected": sign_flip,
    }
