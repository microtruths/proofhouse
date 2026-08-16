# proofhouse.eval.types — result types for A4 diagnostics isolation
"""
Phase 1 Build Plan §1a — A4 diagnostics isolation.

The runner returns two structurally separate values:
  - ScoredResult — the gate-evaluable statistic (Sharpe, alpha, IC, etc.)
  - Diagnostics — everything else (plots, breakdowns, regime analysis)

Phase 2's gate function receives ScoredResult only and structurally
cannot access Diagnostics. This is the `prev` lesson: nearly free now,
expensive to retrofit.
"""


class ScoredResult:
    """Gate-evaluable result from an eval run.

    Contains ONLY the statistic values that the gate engine may inspect.
    No diagnostics, no breakdowns, no regime analysis. The structural
    separation is the enforcement — there is no "if you promise not to
    look" convention.

    Attributes:
        statistic: Name of the primary statistic (e.g., 'dsr', 'panel_ic').
        value: The primary statistic value.
        trial_count: The trial count from the ledger at scoring time.
        benchmark_results: Dict of {benchmark_name: alpha_value} from the
            mandatory sensitivity sweep. Empty if not applicable.
        metadata: Dict of non-diagnostic metadata (n, SE, etc.) that is
            part of the result but not part of the gate decision.
    """

    def __init__(self, statistic, value, trial_count,
                 benchmark_results=None, metadata=None):
        """Initialize a scored result.

        Args:
            statistic: Name of the primary statistic.
            value: The primary statistic value.
            trial_count: Trial count from ledger at scoring time.
            benchmark_results: Optional dict of benchmark sensitivity results.
            metadata: Optional dict of non-diagnostic metadata.
        """
        self.statistic = statistic
        self.value = value
        self.trial_count = trial_count
        self.benchmark_results = benchmark_results or {}
        self.metadata = metadata or {}

    def to_dict(self):
        """Serialize to a dict for manifest storage."""
        return {
            "statistic": self.statistic,
            "value": self.value,
            "trial_count": self.trial_count,
            "benchmark_results": self.benchmark_results,
            "metadata": self.metadata,
        }

    def __repr__(self):
        return "ScoredResult(statistic={!r}, value={!r}, trial_count={})".format(
            self.statistic, self.value, self.trial_count
        )


class Diagnostics:
    """Non-gate-evaluable output from an eval run.

    Contains everything the researcher wants to see but the gate must
    not see: breakdowns by regime, by instrument, by time period,
    plots, intermediate statistics, etc.

    The gate function's signature accepts ScoredResult, not Diagnostics.
    This is structural, not conventional.

    Attributes:
        entries: Dict of {name: value} diagnostic entries.
    """

    def __init__(self, entries=None):
        """Initialize diagnostics.

        Args:
            entries: Optional dict of diagnostic entries.
        """
        self.entries = entries or {}

    def add(self, name, value):
        """Add a diagnostic entry.

        Args:
            name: Name of the diagnostic.
            value: The diagnostic value (any type).
        """
        self.entries[name] = value

    def to_dict(self):
        """Serialize to a dict for storage."""
        return dict(self.entries)

    def __repr__(self):
        return "Diagnostics(entries={})".format(list(self.entries.keys()))
