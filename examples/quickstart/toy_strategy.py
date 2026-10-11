# The demo's strategy artifact. proofhouse pins this file by hash at
# registration and refuses to run any other file, or this file edited.
#
# Contract: define run(data, parameters). `data` is a list of (date, value)
# tuples from the adapter, oldest first. `parameters` is signal.parameters
# from the registered entry: the artifact reads them as arguments, never as
# constants written into this file, so a parameter cannot drift between the
# registered words and the code that ran.
#
# The toy signal is one-day momentum: yesterday's return predicts today's.


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
