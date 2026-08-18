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

from proofhouse.chain.canonical import decimal_str


def _canonicalize(value):
    """Recursively render floats as canonical decimal strings.

    Same rule the ledger applies (canonical rule 3): a persisted number must
    reproduce byte-identically for an independent verifier. None passes
    through untouched — "not computed" is a distinct state from any number,
    and collapsing it would silently drop a hypothesis from an FDR batch or
    silently pass it.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        return decimal_str(value)
    if isinstance(value, dict):
        return {k: _canonicalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(v) for v in value]
    return value


class ScoredResult:
    """Gate-evaluable result from an eval run.

    Contains ONLY the statistic values that the gate engine may inspect.
    No diagnostics, no breakdowns, no regime analysis. The structural
    separation is the enforcement — there is no "if you promise not to
    look" convention.

    p_value and n_effective are FIRST-CLASS fields, deliberately not
    metadata. metadata is documented below as "not part of the gate
    decision" — but a p-value drives FDR selection, so it *is* part of
    the decision. Storing it in metadata would quietly contradict this
    type's own contract and reopen the A4 boundary Phase 1a closed.

    Attributes:
        statistic: Name of the primary statistic (e.g., 'dsr', 'panel_ic').
        value: The primary statistic value.
        trial_count: The trial count from the ledger at scoring time.
        p_value: Optional p-value in [0, 1]. None means "not computed" and
            is NEVER silently coerced — defaulting to 1.0 would silently
            drop the hypothesis from an FDR batch, and 0.0 would silently
            pass it. run_screen refuses a None rather than guessing.
        n_effective: Optional count of INDEPENDENT units — not the row
            count. Requires n_effective_basis when set.
        n_effective_basis: Prose stating what one independent unit is.
        benchmark_results: Dict of {benchmark_name: alpha_value} from the
            mandatory sensitivity sweep. Empty if not applicable.
        metadata: Dict of non-diagnostic metadata (n, SE, etc.) that is
            part of the result but not part of the gate decision.
    """

    def __init__(self, statistic, value, trial_count,
                 p_value=None, n_effective=None, n_effective_basis=None,
                 benchmark_results=None, metadata=None):
        """Initialize a scored result.

        Args:
            statistic: Name of the primary statistic.
            value: The primary statistic value.
            trial_count: Trial count from ledger at scoring time.
            p_value: Optional p-value in [0, 1]. None = not computed.
            n_effective: Optional number of independent units.
            n_effective_basis: Required prose when n_effective is set.
            benchmark_results: Optional dict of benchmark sensitivity results.
            metadata: Optional dict of non-diagnostic metadata.

        Raises:
            ValueError: If p_value is outside [0, 1], if n_effective is not
                a positive integer, or if n_effective is set without a
                non-empty n_effective_basis.
        """
        if p_value is not None:
            if not (0.0 <= p_value <= 1.0):
                raise ValueError(
                    "p_value must be in [0, 1]; got {!r}".format(p_value)
                )

        if n_effective is not None:
            if not isinstance(n_effective, int) or n_effective <= 0:
                raise ValueError(
                    "n_effective must be a positive integer; "
                    "got {!r}".format(n_effective)
                )
            # Failure catalog #4: "Reviewer recommended pooled daily returns
            # as '~300 observations' — overstating effective N (60 correlated
            # days per fixed cohort ~ 1 unit). Lesson: Effective N =
            # independent units."
            #
            # Effective N is declared, never inferred. Requiring the basis
            # is what forces the thought that #4 records someone skipping.
            if not n_effective_basis or not str(n_effective_basis).strip():
                raise ValueError(
                    "n_effective={} requires n_effective_basis stating what "
                    "one independent unit is (failure catalog #4: effective "
                    "N is independent units, not row count)".format(n_effective)
                )

        self.statistic = statistic
        self.value = value
        self.trial_count = trial_count
        self.p_value = p_value
        self.n_effective = n_effective
        self.n_effective_basis = n_effective_basis
        self.benchmark_results = benchmark_results or {}
        self.metadata = metadata or {}

    def to_dict(self):
        """Serialize for manifest storage.

        NUMBERS BECOME CANONICAL DECIMAL STRINGS, not floats.

        Until Phase 2b this method had no production call site at all — it
        claimed "for manifest storage" in its docstring while the manifest
        stored only provenance hashes. Wiring it is what makes Phase 1's
        stated exit criterion, "reproduce a past result from a manifest",
        actually checkable.

        Once a manifest is what the gate reads, its numbers decide verdicts.
        The ledger already settled this for p-values — "a p-value a few ulps
        either side of a Benjamini-Hochberg threshold decides whether a
        hypothesis survives" — and the argument is stronger here, because this
        value is compared directly against a registered threshold. A float
        that shifts on a YAML round-trip could flip a verdict at a boundary,
        which is precisely the case the strict-comparison rule exists for.

        None is preserved rather than omitted: p_value=None means "not
        computed" and is never silently coerced, so the distinction has to
        survive the round-trip.
        """
        return {
            "statistic": self.statistic,
            "value": _canonicalize(self.value),
            "trial_count": self.trial_count,
            "p_value": _canonicalize(self.p_value),
            "n_effective": self.n_effective,
            "n_effective_basis": self.n_effective_basis,
            "benchmark_results": _canonicalize(self.benchmark_results),
            # metadata is deliberately NOT canonicalized.
            #
            # It is an open dict of arbitrary values, so encoding its floats
            # as strings is irreversible: on read there is no way to tell a
            # canonicalized 0.015 from a value that was genuinely the string
            # "0.015". The fields above are numeric by contract, so the
            # conversion is safe there and unsafe here.
            #
            # This is sound rather than merely convenient because metadata is
            # documented as "not part of the gate decision" — nothing in it is
            # compared against a threshold, so no verdict can turn on an ulp
            # of it. The moment something in metadata does feed a decision, it
            # belongs in a first-class field instead, which is the same
            # argument that pulled p_value out of metadata in Phase 1a.
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data):
        """Rebuild a ScoredResult from its manifest form.

        The inverse of to_dict, and the reason to_dict is worth having: a
        stored result that cannot be read back is a record, not a result.

        Raises:
            ValueError: If a numeric field cannot be parsed. A manifest that
                does not round-trip is corrupt, and saying so beats handing
                back a plausible number.
        """
        def _num(key):
            raw = data.get(key)
            if raw is None:
                return None
            try:
                return float(raw)
            except (TypeError, ValueError):
                raise ValueError(
                    "manifest result field {!r} is not a number: {!r}".format(
                        key, raw)
                )

        return cls(
            statistic=data.get("statistic"),
            value=_num("value"),
            trial_count=data.get("trial_count"),
            p_value=_num("p_value"),
            n_effective=data.get("n_effective"),
            n_effective_basis=data.get("n_effective_basis"),
            benchmark_results={
                k: float(v)
                for k, v in (data.get("benchmark_results") or {}).items()
            },
            metadata=data.get("metadata") or {},
        )

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
