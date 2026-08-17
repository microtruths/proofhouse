# tests/test_registry.py — Integration tests for the registry
"""
Integration tests for the full registration flow:
validate → hash → chain → persist → reload → verify.
"""

import os
import tempfile

import pytest

from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.registry import Registry
from proofhouse.schema.validate import ValidationError


def _make_valid_entry(entry_id="H-2026-001"):
    """Return a minimally valid entry."""
    return {
        "id": entry_id,
        "depends_on": [],
        "mechanism": {
            "class": "funding-basis",
            "counterparty": "Leveraged longs paying funding",
            "claim": "Funding rate predicts returns",
            "expected_effect_size": "1.2",
            "expected_decay": "6 months",
        },
        "signal": {
            "inputs": ["funding_rate"],
            "transform": "z-score",
            "frequency": "8h",
            "parameters": {"k": 12},
        },
        "universe": {
            "id": "hl-perps-top20",
            "venues": ["hyperliquid"],
            "instruments": "top 20",
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
            "benchmark_justification": "Standard benchmark for crypto alpha",
            "benchmark_sensitivity": ["market-index", "eth-subtraction"],
            "on_pass": "promote-to-live",
            "on_fail": "retire",
        },
        "p_value_sidedness": "one-sided",
        "fdr_method": "by",
        "fdr_method_justification": "Hypotheses share the hl-perps universe and are therefore dependent.",
        "pass_licenses": "A shadow trial on the live sleeve for 6 weeks.",
        "pass_does_not_establish": "Deployable alpha.",
        "live_gate": {"min_duration_weeks": 6},
        "validation_blocks": [],
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": "1.2"},
            "value": {"pending": True, "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }


class TestRegistry:
    """Integration tests for the registry."""

    def test_register_and_retrieve(self):
        """Register an entry, retrieve it, verify fields set."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        entry = _make_valid_entry()

        result = registry.register(entry)

        assert result["id"] == "H-2026-001"
        assert result["status"] == "registered"
        assert result["schema_version"] == 1
        assert result["hash"].startswith("sha256:")
        assert "prev" not in result  # Genesis

        # Retrieve
        fetched = registry.get_entry("H-2026-001")
        assert fetched["id"] == "H-2026-001"

    def test_chain_across_entries(self):
        """Two entries are properly chained."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        e1 = registry.register(_make_valid_entry("H-2026-001"))
        e2 = registry.register(_make_valid_entry("H-2026-002"))

        assert e2["prev"] == e1["hash"]

    def test_reload_from_disk(self):
        """Entries survive a registry reload from disk."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        registry.register(_make_valid_entry("H-2026-001"))
        registry.register(_make_valid_entry("H-2026-002"))

        # Reload
        registry2 = Registry(reg_dir)
        entries = registry2.list_entries()
        assert len(entries) == 2

    def test_chain_verification(self):
        """Chain verifies correctly after registration."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        registry.register(_make_valid_entry("H-2026-001"))
        registry.register(_make_valid_entry("H-2026-002"))

        ok, errors = registry.verify()
        assert ok
        assert errors == []

    def test_duplicate_id_refused(self):
        """Registering the same ID twice raises."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        registry.register(_make_valid_entry("H-2026-001"))

        with pytest.raises(ValueError, match="already exists"):
            registry.register(_make_valid_entry("H-2026-001"))

    def test_invalid_entry_refused(self):
        """An invalid entry is refused — nothing written to disk."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        entry = _make_valid_entry()
        entry["gate"]["combination"] = None  # V-3 violation

        with pytest.raises(ValidationError):
            registry.register(entry)

        # Nothing should have been written
        assert len(registry.list_entries()) == 0

    def test_depends_on_existing_entry(self):
        """An entry depending on an existing entry passes V-9."""
        tmpdir = tempfile.mkdtemp()
        reg_dir = os.path.join(tmpdir, "registry")

        registry = Registry(reg_dir)
        registry.register(_make_valid_entry("H-2026-001"))

        entry2 = _make_valid_entry("H-2026-002")
        entry2["depends_on"] = ["H-2026-001"]
        result = registry.register(entry2)
        assert result["depends_on"] == ["H-2026-001"]
