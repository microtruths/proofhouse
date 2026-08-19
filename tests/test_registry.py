# tests/test_registry.py — Integration tests for the registry
"""
Integration tests for the full registration flow:
validate → hash → chain → persist → reload → verify.
"""

import copy
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


class TestFreshBlocksMeansUNSPENTNotUNDECLARED:
    """The invalidating-amendment check must read consumption, not intent.

    `validation_blocks` is a statement of intent written at registration.
    Consumption happens later, at gate time. Between those two moments a
    block is named and untouched, and comparing declared lists cannot tell
    that state from a spent one.

    It bit H-2026-PFR1: registered and amended the same day, six validation
    blocks all `consumed_by: []`, manifests empty, no gate ever run. The
    amendment was refused, and the only compliant route was to spend four
    DIFFERENT blocks to protect six that were already clean — more data
    consumed for less isolation.

    Third in the family with #28 and #29: a reader walking past a fact the
    registry already holds.
    """

    def _blocks(self):
        registry = BlockRegistry()
        for i in range(1, 7):
            registry.add_block({
                "id": "B{}".format(i), "start": "2026-01-01",
                "end": "2026-03-01", "segment": "V", "data_complete": True,
                "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
            })
        return registry

    def _registry(self, blocks=None):
        return Registry(os.path.join(tempfile.mkdtemp(), "registry"),
                        block_registry=blocks)

    def _entry(self, entry_id, vblocks, effect="1.2", depends=()):
        entry = _make_valid_entry(entry_id)
        entry["validation_blocks"] = list(vblocks)
        entry["mechanism"]["expected_effect_size"] = effect
        entry["depends_on"] = list(depends)
        return entry

    def test_reuse_of_unconsumed_blocks_is_allowed(self):
        """THE CASE THAT MOTIVATED THIS. Parent never gated."""
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-P", ["B1", "B2"]))

        successor = registry.register(
            self._entry("H-S", ["B1", "B2"], effect="9.9", depends=["H-P"]))

        assert successor["validation_blocks"] == ["B1", "B2"]

    def test_reuse_of_a_consumed_block_is_refused(self):
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-P", ["B1", "B2"]))
        blocks.consume_block("B1", "H-P")

        with pytest.raises(ValidationError) as exc:
            registry.register(
                self._entry("H-S", ["B1"], effect="9.9", depends=["H-P"]))

        assert "B1" in str(exc.value)
        assert "H-P" in str(exc.value)

    def test_partial_overlap_names_only_the_spent_block(self):
        """A message listing clean blocks alongside spent ones teaches the
        reader to skim it."""
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-P", ["B1", "B2"]))
        blocks.consume_block("B1", "H-P")

        with pytest.raises(ValidationError) as exc:
            registry.register(self._entry("H-S", ["B1", "B2"], effect="9.9",
                                          depends=["H-P"]))

        message = str(exc.value)
        assert "B1" in message
        assert "B2" not in message

    def test_consumption_cannot_be_laundered_through_an_intermediate(self):
        """A LIVE HOLE, not just a design point for the fix.

        The loop compared against immediate parents only, so A -> B -> C let
        C reuse a block A had spent: C's list overlaps A's, but not B's, and
        B was the only entry compared.
        """
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-A", ["B1"]))
        blocks.consume_block("B1", "H-A")
        registry.register(self._entry("H-B", ["B2"], effect="5.5",
                                      depends=["H-A"]))

        with pytest.raises(ValidationError) as exc:
            registry.register(self._entry("H-C", ["B1"], effect="9.9",
                                          depends=["H-B"]))

        assert "B1" in str(exc.value)
        assert "H-A" in str(exc.value)

    def test_an_unconsumed_grandparent_block_is_still_allowed(self):
        """The mirror of the case above, and the one that separates 'walks
        ancestors' from 'reads consumption'.

        A fix that walked the chain correctly but still compared DECLARATIONS
        would pass every other test here and fail this one.
        """
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-A", ["B1"]))
        registry.register(self._entry("H-B", ["B2"], effect="5.5",
                                      depends=["H-A"]))

        registry.register(self._entry("H-C", ["B1"], effect="9.9",
                                      depends=["H-B"]))

    def test_without_a_block_registry_reuse_stays_refused(self):
        """The conservative branch stays conservative. Absence of evidence
        must not read as evidence of cleanliness."""
        registry = self._registry(blocks=None)
        registry.register(self._entry("H-P", ["B1"]))

        with pytest.raises(ValidationError):
            registry.register(self._entry("H-S", ["B1"], effect="9.9",
                                          depends=["H-P"]))

    def test_a_non_invalidating_amendment_never_reaches_the_check(self):
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-P", ["B1"]))
        blocks.consume_block("B1", "H-P")

        successor = self._entry("H-S", ["B1"], depends=["H-P"])
        successor["costs"] = {"model_ref": "costs/hl-maker-v2"}
        registry.register(successor)

    def test_a_siblings_consumption_is_not_this_checks_error(self):
        """B and C both descend from A. B spends a block, C reuses it.

        B is not an ancestor of C, so this check does not see it and E4
        refuses at gate time instead. Recorded so the omission is deliberate
        rather than overlooked.
        """
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-A", ["B1"]))
        registry.register(self._entry("H-B", ["B2"], effect="5.5",
                                      depends=["H-A"]))
        blocks.consume_block("B2", "H-B")

        registry.register(self._entry("H-C", ["B2"], effect="9.9",
                                      depends=["H-A"]))

    def test_a_permitted_reuse_does_not_claim_fresh_blocks(self):
        """The note said 'this entry validates on fresh blocks'. Allowing
        reuse without touching it puts a falsehood in the record."""
        blocks = self._blocks()
        registry = self._registry(blocks)
        registry.register(self._entry("H-P", ["B1"]))
        successor = registry.register(
            self._entry("H-S", ["B1"], effect="9.9", depends=["H-P"]))

        note = successor["amendments"][0]["note"]
        assert "fresh blocks" not in note
        assert "unconsumed" in note
        assert "B1" in note


class TestRevisionCountsTheLineage:
    """`len(amendments)` means two things for two identical successors.

    The list is inherited or not depending on whether the caller built the
    successor by copying the parent, so a three-deep lineage reported
    "revision 1" when the successor happened to be built fresh. That
    under-reports, which is the wrong direction for a disclosure whose whole
    job is to show accumulated forks.
    """

    def _setup(self):
        blocks = BlockRegistry()
        for i in range(1, 5):
            blocks.add_block({
                "id": "B{}".format(i), "start": "2026-01-01",
                "end": "2026-03-01", "segment": "V", "data_complete": True,
                "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
            })
        registry = Registry(os.path.join(tempfile.mkdtemp(), "registry"),
                            block_registry=blocks)
        return registry, blocks

    def _entry(self, entry_id, vblocks, effect, depends=()):
        entry = _make_valid_entry(entry_id)
        entry["validation_blocks"] = list(vblocks)
        entry["mechanism"]["expected_effect_size"] = effect
        entry["depends_on"] = list(depends)
        return entry

    def test_revision_increments_along_the_chain(self):
        registry, _ = self._setup()
        registry.register(self._entry("H-A", ["B1"], "1.2"))
        b = registry.register(self._entry("H-B", ["B2"], "5.5",
                                          depends=["H-A"]))
        c = registry.register(self._entry("H-C", ["B3"], "9.9",
                                          depends=["H-B"]))

        assert b["amendments"][0]["revision"] == 1
        assert c["amendments"][0]["revision"] == 2

    def test_the_count_does_not_depend_on_how_the_successor_was_built(self):
        """A successor deepcopied from its parent inherits the amendments
        list; one built fresh does not. The revision must not care."""
        registry, _ = self._setup()
        registry.register(self._entry("H-A2", ["B1"], "1.2"))
        parent = registry.register(self._entry("H-B2", ["B2"], "5.5",
                                               depends=["H-A2"]))

        copied = copy.deepcopy(parent)
        copied.update({"id": "H-C2", "depends_on": ["H-B2"],
                       "validation_blocks": ["B3"]})
        copied["mechanism"]["expected_effect_size"] = "9.9"
        copied.pop("hash", None)
        copied.pop("prev", None)
        from_copy = registry.register(copied)

        fresh = registry.register(self._entry("H-D2", ["B4"], "7.7",
                                              depends=["H-B2"]))

        assert max(a["revision"] for a in from_copy["amendments"]) == 2
        assert max(a["revision"] for a in fresh["amendments"]) == 2
