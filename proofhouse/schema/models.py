# proofhouse.schema.models — record type definitions
"""
Registry entry schema for proofhouse.

Phase 0 Build Plan §4.2 — full field set per parent plan §5.1.
All fields present in v1 so records never need migration.

Phase 0 enforces declaration-time constraints only. It does not
evaluate anything — a registration either is or isn't well-formed.
"""

from proofhouse.schema.versions import SCHEMA_VERSION


# Valid entry statuses
VALID_STATUSES = frozenset({
    "draft", "registered", "amended", "passed", "failed", "live", "retired"
})

# Controlled vocabulary for mechanism classes (§5.1.1)
# Adding a class is a schema amendment — logged, numbered, dated.
MECHANISM_CLASSES = frozenset({
    "funding-basis",
    "liquidation-cascade",
    "listing-event",
    "index-rebalance",
    "governance-event",
    "cross-venue-dislocation",
    "execution-quality",
    "flow-toxicity",
    "regime-prediction",
    "carry-other",
    "microstructure-other",
    "price-pattern",
    "selection-rule",
    # Non-crypto classes for domain-agnosticism
    "survey-experiment",
    "ab-test",
    "other",
})

# Valid comparators. > fails ties, >= passes them (§0.3).
VALID_COMPARATORS = frozenset({">", ">=", "<", "<="})

# Valid block segments
VALID_SEGMENTS = frozenset({"E", "S1", "S2", "V", "R", "L"})

# Benchmark terms that are NOT valid counterparties (V-1)
# These describe performance relative to a benchmark, not who loses money.
_BENCHMARK_TERMS = [
    "buy-and-hold",
    "buy and hold",
    "underperform",
    "outperform",
    "beats the market",
    "benchmark",
    "index investors",
    "passive investors",
    "hodlers",
]


def make_entry_template():
    """Return a template entry dict with all required fields.

    This is the full field set — every field is present from v1 so
    records never need migration. Only six checks are enforced at
    registration (E1–E6); the rest are structural presence checks.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "id": None,
        "created_utc": None,
        "status": "draft",
        "depends_on": [],
        "mechanism": {
            "class": None,
            "counterparty": None,
            "claim": None,
            "expected_effect_size": None,
            "expected_decay": None,
        },
        "signal": {
            "inputs": [],
            "transform": None,
            "frequency": None,
            "parameters": {},
        },
        "universe": {
            "id": None,
            "venues": [],
            "instruments": None,
        },
        "feasibility": {
            "min_order_notional_usd": None,
            "sleeve_range": [],
            "instruments_held": None,
            "rebalance_band": None,
        },
        "costs": {
            "model_ref": None,
        },
        "gate": {
            "statistic": None,
            "combination": None,
            "criteria": {},
            "benchmark": None,
            "benchmark_justification": None,
            "benchmark_sensitivity": [],
            "on_pass": None,
            "on_fail": None,
        },
        "pass_licenses": None,
        "pass_does_not_establish": None,
        "live_gate": {},
        "validation_blocks": [],
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {},
            "value": {"pending": True, "fill_rule": None},
        },
        "prior_art": [],
    }


def is_benchmark_term(text):
    """Check if text looks like a benchmark description rather than a counterparty.

    V-1: Benchmark terms are not counterparties. "buy-and-hold investors
    underperform" identifies a performance comparison, not who loses money.

    Args:
        text: The counterparty string to check.

    Returns:
        True if the text contains benchmark language.
    """
    if not text:
        return False
    lower = text.lower()
    return any(term in lower for term in _BENCHMARK_TERMS)
