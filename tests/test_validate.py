# tests/test_validate.py — Hand-computed tests for entry validation
"""
Phase 0 Build Plan §6 — Validation tests.

Tests V-1 through V-10. Written before implementation.
Each test uses a well-formed base entry and breaks exactly one thing.
"""

import pytest

from proofhouse.core.blocks import BlockRegistry
from proofhouse.schema.validate import validate_entry, ValidationError


def _make_valid_entry():
    """Return a minimally valid entry that passes all checks."""
    return {
        "schema_version": 1,
        "id": "H-2026-001",
        "created_utc": "2026-08-20T14:03:11Z",
        "status": "registered",
        "depends_on": [],
        "mechanism": {
            "class": "funding-basis",
            "counterparty": "Leveraged longs paying funding on perpetual contracts",
            "claim": "Funding rate predicts subsequent returns",
            "expected_effect_size": "1.2",
            "expected_decay": "6 months",
        },
        "signal": {
            "inputs": ["funding_rate"],
            "transform": "z-score over trailing 12 periods",
            "frequency": "8h",
            "parameters": {"k": 12, "threshold": "0.8"},
        },
        "universe": {
            "id": "hl-perps-top20",
            "venues": ["hyperliquid"],
            "instruments": "top 20 by OI",
        },
        "feasibility": {
            "min_order_notional_usd": "10",
            "sleeve_range": ["100", "20000"],
            "instruments_held": 8,
            "rebalance_band": "0.15",
        },
        "costs": {"model_ref": "costs/hl-taker-v1"},
        "gate": {
            "statistic": "dsr",
            "combination": "g1 and g2",
            "criteria": {
                "g1": {"comparator": ">", "threshold": {"literal": "0"}},
                "g2": {"comparator": ">=", "threshold": {"literal": "0.95"}},
            },
            "benchmark": "btc-subtraction",
            "benchmark_justification": "BTC subtraction chosen because...",
            "benchmark_sensitivity": ["market-index", "eth-subtraction"],
            "on_pass": "promote-to-live",
            "on_fail": "retire",
        },
        "pass_licenses": "A shadow trial on the live sleeve for 6 weeks.",
        "pass_does_not_establish": "Deployable alpha or strategy robustness.",
        "live_gate": {"min_duration_weeks": 6},
        "validation_blocks": [],
        "seam_acknowledged": False,
        "diagnostics": [
            {"name": "mark_dependence", "trigger": ">0.5 → written caveat"},
        ],
        "power_check": {
            "inputs": {"effect_size": "1.2", "blocks": 3},
            "value": {"pending": True, "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }


def _make_block_registry():
    """Create a block registry with blocks of various segments."""
    reg = BlockRegistry()
    reg.add_block({
        "id": "B-2025-01", "start": "2025-01-01T00:00:00Z",
        "end": "2025-03-01T00:00:00Z", "segment": "E",
        "data_complete": True, "completed_at": "2026-01-01T00:00:00Z",
        "sources": ["node_fills"], "seams": [],
    })
    reg.add_block({
        "id": "B-2025-02", "start": "2025-03-01T00:00:00Z",
        "end": "2025-05-01T00:00:00Z", "segment": "S1",
        "data_complete": True, "completed_at": "2026-01-01T00:00:00Z",
        "sources": ["node_fills"], "seams": [],
    })
    reg.add_block({
        "id": "B-2025-03", "start": "2025-05-01T00:00:00Z",
        "end": "2025-07-01T00:00:00Z", "segment": "V",
        "data_complete": True, "completed_at": "2026-01-01T00:00:00Z",
        "sources": ["node_fills"], "seams": [],
    })
    reg.add_block({
        "id": "B-2025-04", "start": "2025-07-01T00:00:00Z",
        "end": "2025-09-01T00:00:00Z", "segment": "R",
        "data_complete": True, "completed_at": "2026-01-01T00:00:00Z",
        "sources": ["node_fills"], "seams": [],
    })
    return reg


class TestValidation:
    """V-1 through V-10: schema validators and behavioral checks."""

    def test_valid_entry_passes(self):
        """A well-formed entry passes all checks."""
        entry = _make_valid_entry()
        validate_entry(entry)  # Should not raise

    def test_v1_benchmark_counterparty_rejected(self):
        """V-1: counterparty using benchmark language → rejected.

        'buy-and-hold investors underperform' identifies a performance
        comparison, not who loses money.
        """
        entry = _make_valid_entry()
        entry["mechanism"]["counterparty"] = "buy-and-hold investors underperform"

        with pytest.raises(ValidationError, match="V-1"):
            validate_entry(entry)

    def test_v1_valid_counterparty_accepted(self):
        """V-1 complement: a specific counterparty passes."""
        entry = _make_valid_entry()
        entry["mechanism"]["counterparty"] = (
            "Leveraged longs paying carry via funding rate"
        )
        validate_entry(entry)  # Should not raise

    def test_v2_parameter_list_rejected(self):
        """V-2: parameters with a list value → rejected.

        {k: [10,12,14]} is a grid search, which is unregisterable.
        """
        entry = _make_valid_entry()
        entry["signal"]["parameters"] = {"k": [10, 12, 14]}

        with pytest.raises(ValidationError, match="V-2"):
            validate_entry(entry)

    def test_v2_scalar_parameter_accepted(self):
        """V-2 complement: {k: 12} → accepted."""
        entry = _make_valid_entry()
        entry["signal"]["parameters"] = {"k": 12}
        validate_entry(entry)

    def test_v3_no_combination_rejected(self):
        """V-3: Entry with no gate.combination → rejected, no default applied."""
        entry = _make_valid_entry()
        entry["gate"]["combination"] = None

        with pytest.raises(ValidationError, match="V-3"):
            validate_entry(entry)

    def test_v3_empty_combination_rejected(self):
        """V-3: Empty string combination also rejected."""
        entry = _make_valid_entry()
        entry["gate"]["combination"] = ""

        with pytest.raises(ValidationError, match="V-3"):
            validate_entry(entry)

    def test_v4_derived_threshold_v_segment_rejected(self):
        """V-4 / E2: threshold.derived.source_blocks containing segment-V → rejected.

        segment E or S1 → accepted.
        """
        entry = _make_valid_entry()
        entry["gate"]["criteria"] = {
            "g1": {
                "comparator": ">",
                "threshold": {
                    "derived": {
                        "formula": "0.5 * median(sigma)",
                        "source_blocks": ["B-2025-03"],  # segment V
                    }
                },
            },
        }
        block_reg = _make_block_registry()

        with pytest.raises(ValidationError, match="V-4"):
            validate_entry(entry, block_registry=block_reg)

    def test_v4_derived_threshold_e_segment_accepted(self):
        """V-4 complement: source_blocks with segment E → accepted."""
        entry = _make_valid_entry()
        entry["gate"]["criteria"] = {
            "g1": {
                "comparator": ">",
                "threshold": {
                    "derived": {
                        "formula": "0.5 * median(sigma)",
                        "source_blocks": ["B-2025-01"],  # segment E
                    }
                },
            },
        }
        block_reg = _make_block_registry()
        validate_entry(entry, block_registry=block_reg)

    def test_v5_tie_handling_rejected(self):
        """V-5: tie_handling key present → rejected.

        The field does not exist; the comparator carries tie semantics.
        """
        entry = _make_valid_entry()
        entry["gate"]["tie_handling"] = "fail"

        with pytest.raises(ValidationError, match="V-5"):
            validate_entry(entry)

    def test_v5_tie_handling_top_level_rejected(self):
        """V-5: tie_handling at top level also rejected."""
        entry = _make_valid_entry()
        entry["tie_handling"] = "fail"

        with pytest.raises(ValidationError, match="V-5"):
            validate_entry(entry)

    def test_v6_pass_licenses_too_long_rejected(self):
        """V-6: pass_licenses of 201 chars → rejected."""
        entry = _make_valid_entry()
        entry["pass_licenses"] = "x" * 201

        with pytest.raises(ValidationError, match="V-6"):
            validate_entry(entry)

    def test_v6_pass_licenses_200_accepted(self):
        """V-6 complement: exactly 200 chars → accepted."""
        entry = _make_valid_entry()
        entry["pass_licenses"] = "x" * 200
        validate_entry(entry)

    def test_v7_benchmark_sensitivity_one_entry_rejected(self):
        """V-7: benchmark_sensitivity with 1 entry → rejected; 2 → accepted."""
        entry = _make_valid_entry()
        entry["gate"]["benchmark_sensitivity"] = ["market-index"]

        with pytest.raises(ValidationError, match="V-7"):
            validate_entry(entry)

    def test_v7_benchmark_sensitivity_two_accepted(self):
        """V-7 complement: 2 entries → accepted."""
        entry = _make_valid_entry()
        entry["gate"]["benchmark_sensitivity"] = ["market-index", "eth-subtraction"]
        validate_entry(entry)

    def test_v8_validation_blocks_e_segment_rejected(self):
        """V-8: validation_blocks referencing a segment-E block → rejected."""
        entry = _make_valid_entry()
        entry["validation_blocks"] = ["B-2025-01"]  # segment E
        block_reg = _make_block_registry()

        with pytest.raises(ValidationError, match="V-8"):
            validate_entry(entry, block_registry=block_reg)

    def test_v8_validation_blocks_v_segment_accepted(self):
        """V-8 complement: segment-V or R block → accepted."""
        entry = _make_valid_entry()
        entry["validation_blocks"] = ["B-2025-04"]  # segment R
        block_reg = _make_block_registry()
        validate_entry(entry, block_registry=block_reg)

    def test_v9_depends_on_nonexistent_rejected(self):
        """V-9: depends_on referencing a nonexistent entry → rejected."""
        entry = _make_valid_entry()
        entry["depends_on"] = ["H-2026-999"]

        with pytest.raises(ValidationError, match="V-9"):
            validate_entry(entry, existing_entries={})

    def test_v9_depends_on_existing_accepted(self):
        """V-9 complement: referencing an existing entry → accepted."""
        entry = _make_valid_entry()
        entry["depends_on"] = ["H-2026-000"]
        existing = {"H-2026-000": {"id": "H-2026-000"}}
        validate_entry(entry, existing_entries=existing)

    def test_v10_pending_without_fill_rule_rejected(self):
        """V-10: power_check.value.pending=True without fill_rule → rejected."""
        entry = _make_valid_entry()
        entry["power_check"] = {
            "inputs": {"effect_size": "1.2"},
            "value": {"pending": True, "fill_rule": None},
        }

        with pytest.raises(ValidationError, match="V-10"):
            validate_entry(entry)

    def test_v10_pending_with_fill_rule_accepted(self):
        """V-10 complement: pending with fill_rule → accepted."""
        entry = _make_valid_entry()
        entry["power_check"] = {
            "inputs": {"effect_size": "1.2"},
            "value": {"pending": True, "fill_rule": "power(effect_size, blocks)"},
        }
        validate_entry(entry)

    def test_e3_diagnostics_isolation(self):
        """E3: gate criteria sharing a name with a diagnostic → rejected.

        Enforced structurally: the verdict function must not receive
        the diagnostics block.
        """
        entry = _make_valid_entry()
        entry["diagnostics"] = [{"name": "g1", "trigger": ">0.5"}]
        # g1 is also a gate criterion name

        with pytest.raises(ValidationError, match="E3"):
            validate_entry(entry)

    def test_e5_pass_does_not_establish_missing_rejected(self):
        """E5: pass_does_not_establish missing → rejected."""
        entry = _make_valid_entry()
        entry["pass_does_not_establish"] = ""

        with pytest.raises(ValidationError, match="E5"):
            validate_entry(entry)

    def test_multiple_errors_collected(self):
        """Multiple violations are all collected and reported together."""
        entry = _make_valid_entry()
        entry["gate"]["combination"] = None  # V-3
        entry["pass_licenses"] = ""  # V-6
        entry["pass_does_not_establish"] = ""  # E5
        entry["gate"]["benchmark_sensitivity"] = []  # V-7

        with pytest.raises(ValidationError) as exc_info:
            validate_entry(entry)

        assert len(exc_info.value.errors) >= 4

    def test_v11_missing_schema_version_rejected(self):
        """V-11: missing schema_version → rejected."""
        entry = _make_valid_entry()
        del entry["schema_version"]

        with pytest.raises(ValidationError, match="V-11"):
            validate_entry(entry)

    def test_v11_future_schema_version_rejected(self):
        """V-11: schema_version from a newer proofhouse → rejected."""
        entry = _make_valid_entry()
        entry["schema_version"] = 999

        with pytest.raises(ValidationError, match="V-11"):
            validate_entry(entry)

    def test_v11_valid_schema_version_accepted(self):
        """V-11 complement: schema_version = 1 → accepted."""
        entry = _make_valid_entry()
        entry["schema_version"] = 1
        validate_entry(entry)

    def test_v12_sleeve_range_not_list_rejected(self):
        """V-12: sleeve_range as a scalar → rejected."""
        entry = _make_valid_entry()
        entry["feasibility"]["sleeve_range"] = "100"

        with pytest.raises(ValidationError, match="V-12"):
            validate_entry(entry)

    def test_v12_sleeve_range_inverted_rejected(self):
        """V-12: sleeve_range where min > max → rejected."""
        entry = _make_valid_entry()
        entry["feasibility"]["sleeve_range"] = ["20000", "100"]

        with pytest.raises(ValidationError, match="V-12"):
            validate_entry(entry)

    def test_v12_sleeve_range_valid_accepted(self):
        """V-12 complement: valid 2-element decimal string list → accepted."""
        entry = _make_valid_entry()
        entry["feasibility"]["sleeve_range"] = ["100", "20000"]
        validate_entry(entry)

    def test_v2_string_range_rejected(self):
        """V-2: string range like '10..14' → rejected."""
        entry = _make_valid_entry()
        entry["signal"]["parameters"] = {"k": "10..14"}

        with pytest.raises(ValidationError, match="V-2"):
            validate_entry(entry)

    def test_e3_limitation_documented(self):
        """E3 limitation: different name referencing diagnostic value → NOT caught.

        Phase 0 limitation: E3 only catches name collisions. True structural
        isolation requires the gate evaluation engine (Phase 2).
        This test documents the limitation.
        """
        entry = _make_valid_entry()
        entry["diagnostics"] = [{"name": "mark_dependence", "trigger": ">0.5"}]
        # Gate criterion 'g1' has a different name than diagnostic 'mark_dependence'
        # so E3 does not catch this case — but it could still read the value
        # in practice. This is the documented Phase 0 limitation.
        validate_entry(entry)  # Should pass (limitation)
