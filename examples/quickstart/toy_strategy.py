# The demo's strategy artifact. proofhouse pins this file by hash at
# registration and refuses to run any other file, or this file edited.
#
# Two functions live here, and the pin covers both:
#
#   run(data, parameters)                      the strategy
#   score_fn(predictions, outcomes, trial_count)  the gated statistic
#
# `data` is a list of (date, value) tuples from the adapter, oldest first.
# `parameters` is signal.parameters from the registered entry: the artifact
# reads them as arguments, never as constants written into this file, so a
# parameter cannot drift between the registered words and the code that ran.
# `trial_count` arrives from the runner, which read it from the ledger; a
# score function must use that number and never compute its own.
#
# The toy signal is one-day momentum: yesterday's return predicts today's.

from proofhouse.eval.stats import p_from_t, panel_ic, spearman_t
from proofhouse.eval.types import Diagnostics, ScoredResult


def run(data, parameters):
    """Return (predictions, outcomes), one of each per row of data."""
    polarity = parameters.get("polarity", 1)
    lookback = parameters.get("lookback_days", 1)
    returns = [value for _, value in data]
    predictions = []
    outcomes = []
    for i in range(lookback, len(returns)):
        predictions.append(polarity * returns[i - lookback])
        outcomes.append(returns[i])
    return predictions, outcomes


def score_fn(predictions, outcomes, trial_count):
    """Turn the artifact's output into a gate-evaluable result."""
    n = len(predictions)
    ic = panel_ic(predictions, outcomes)
    t = spearman_t(ic, n)
    p = p_from_t(t, n - 2, "one-sided")
    scored = ScoredResult(
        statistic="panel_ic",
        value=ic,
        trial_count=trial_count,
        p_value=p,
        n_effective=n,
        n_effective_basis="one trading day; the series is synthetic and "
                          "its days are not clustered",
        metadata={"n": n, "t": t},
    )
    diagnostics = Diagnostics()
    diagnostics.add("n_pairs", n)
    diagnostics.add("first_five_predictions", predictions[:5])
    return scored, diagnostics
