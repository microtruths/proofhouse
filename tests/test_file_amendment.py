# tests/test_file_amendment.py — the two amendment halves, reconciled
"""
Phase 2 Completion §6.1, decision (a).

Two representations of an amendment existed and did not agree:

  * the registry's — a NEW ENTRY with `depends_on`, which is what enforces
    fresh blocks for an invalidating amendment;
  * AmendmentLog's — a RECORD with diff, rationale, impact and anchor, which
    is what makes the change auditable and orderable.

They are two halves of one event. Writing either alone leaves the gap where
they drift. These tests pin that both are written, or neither is.
"""

import copy
import os
import tempfile

import pytest
import yaml

from proofhouse.core.amend import (
    AmendmentLog, AmendmentRefused, file_amendment,
)
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.registry import Registry
from proofhouse.schema.validate import ValidationError

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "agentcopy_v10_ic_gate.yaml",
)


def _blocks():
    registry = BlockRegistry()
    for name, segment in [("B-V-01", "V"), ("B-V-02", "V"), ("B-E-01", "E")]:
        registry.add_block({
            "id": name, "start": "2025-01-01", "end": "2025-03-01",
            "segment": segment, "data_complete": True,
            "completed_at": "2025-03-15", "sources": ["hl"], "seams": [],
        })
    return registry


@pytest.fixture
def setup():
    root = tempfile.mkdtemp()
    blocks = _blocks()
    registry = Registry(os.path.join(root, "registry"),
                        block_registry=blocks)
    log = AmendmentLog(os.path.join(root, "amendments.jsonl"))
    with open(FIXTURE) as fh:
        parent = yaml.safe_load(fh)
    registry.register(copy.deepcopy(parent))
    return registry, log, parent, blocks


def _successor(parent, entry_id="H-2026-AC10-A1", **changes):
    successor = copy.deepcopy(parent)
    successor["id"] = entry_id
    successor["depends_on"] = [parent["id"]]
    successor.pop("hash", None)
    successor.pop("prev", None)
    for key, value in changes.items():
        successor[key] = value
    return successor


class TestBothHalvesOrNeither:
    def test_a_valid_amendment_writes_both(self, setup):
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        successor["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"

        registered, record = file_amendment(
            registry, log, parent["id"], successor,
            rationale="0.05 was transcribed from the wrong row of the plan.",
            impact="Changes which results pass. Successor runs on fresh blocks.",
            ledger_anchor="R-000003",
        )

        assert registered["id"] == "H-2026-AC10-A1"
        assert registry.get_entry("H-2026-AC10-A1")["depends_on"] == [
            parent["id"]]
        assert record["entry_id"] == parent["id"]
        assert record["number"] == 1
        assert record["ledger_anchor"] == "R-000003"
        assert record["entry_hash_after"] == registered["hash"]

    def test_an_invalid_successor_writes_neither(self, setup):
        """Registration runs first because it is the half that can
        legitimately refuse. Filing the record first would leave a governance
        record describing a change that was then rejected — worse than no
        record, because it reads as though the change happened."""
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        del successor["gate"]["combination"]        # E1/V-3

        with pytest.raises(ValidationError):
            file_amendment(registry, log, parent["id"], successor,
                           rationale="r", impact="i")

        assert log.for_entry(parent["id"]) == []
        with pytest.raises(KeyError):
            registry.get_entry("H-2026-AC10-A1")

    def test_an_empty_diff_writes_neither(self, setup):
        """Refused BEFORE registering, so a no-op amendment cannot leave a
        registered successor behind after the record is rejected."""
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        successor["depends_on"] = [parent["id"]]

        with pytest.raises(ValueError):
            file_amendment(registry, log, parent["id"], successor,
                           rationale="r", impact="i")

        assert log.for_entry(parent["id"]) == []
        with pytest.raises(KeyError):
            registry.get_entry("H-2026-AC10-A1")

    def test_a_stale_schema_version_alone_is_not_a_change(self, setup):
        """The registry stamps schema_version on everything it writes. A
        successor copied from an older template differs from its parent in
        that field alone; the empty-diff check must see what will be
        registered, or the successor registers and the log then refuses an
        empty diff — one half written. Found when the version moved to 2."""
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        successor["schema_version"] = 1

        with pytest.raises(ValueError, match="empty diff"):
            file_amendment(registry, log, parent["id"], successor,
                           rationale="r", impact="i")

        assert log.for_entry(parent["id"]) == []
        with pytest.raises(KeyError):
            registry.get_entry("H-2026-AC10-A1")

    def test_missing_rationale_writes_neither(self, setup):
        registry, log, parent, blocks = setup
        successor = _successor(parent, id="H-X")
        successor["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"

        with pytest.raises(ValueError):
            file_amendment(registry, log, parent["id"], successor,
                           rationale="", impact="i")
        assert log.for_entry(parent["id"]) == []


class TestSuccessorDiscipline:
    def test_overwriting_the_parent_is_refused(self, setup):
        """Registry entries are chained across files by prev -> hash, so
        editing one in place orphans every later entry pointing at it."""
        registry, log, parent, blocks = setup
        successor = _successor(parent, id=parent["id"])

        with pytest.raises(AmendmentRefused) as exc:
            file_amendment(registry, log, parent["id"], successor,
                           rationale="r", impact="i")
        assert "own id" in str(exc.value)

    def test_a_successor_that_does_not_name_its_parent_is_refused(self, setup):
        """The depends_on link is what makes the registry enforce fresh blocks
        for an invalidating amendment. Without it the successor reads as an
        unrelated hypothesis."""
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        successor["depends_on"] = []
        successor["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"

        with pytest.raises(AmendmentRefused) as exc:
            file_amendment(registry, log, parent["id"], successor,
                           rationale="r", impact="i")
        assert "depends_on" in str(exc.value)

    def test_amending_an_unknown_entry_is_refused(self, setup):
        registry, log, parent, blocks = setup
        with pytest.raises(AmendmentRefused):
            file_amendment(registry, log, "H-NOPE", _successor(parent),
                           rationale="r", impact="i")


class TestInvalidatingAmendments:
    """The case the registry half exists for."""

    def test_a_polarity_change_reusing_parent_blocks_is_refused(self, setup):
        """One-sided p-values are legitimate only because the direction was
        fixed before the data was seen. A post-hoc flip on the same window
        would silently convert every one-sided test into a two-sided one at
        half the stated p-value."""
        registry, log, parent, blocks = setup

        # Give the parent a consumed validation window to collide with.
        parent_with_blocks = copy.deepcopy(parent)
        parent_with_blocks["id"] = "H-POL"
        parent_with_blocks["validation_blocks"] = ["B-V-01"]
        parent_with_blocks["signal"]["parameters"]["polarity"] = "long"
        parent_with_blocks.pop("hash", None)
        parent_with_blocks.pop("prev", None)
        registry.register(parent_with_blocks)
        # ACTUALLY consume it. This test's docstring always said "a consumed
        # validation window" and the setup never consumed anything, so it
        # passed against the old declaration-only check for the wrong reason.
        blocks.consume_block("B-V-01", "H-POL")

        successor = _successor(parent_with_blocks, id="H-POL-A1")
        successor["validation_blocks"] = ["B-V-01"]        # same window
        successor["signal"]["parameters"]["polarity"] = "short"

        with pytest.raises(ValidationError) as exc:
            file_amendment(registry, log, "H-POL", successor,
                           rationale="Direction was backwards.",
                           impact="Invalidates the pre-registration.")
        assert "polarity" in str(exc.value)
        assert log.for_entry("H-POL") == []

    def test_a_polarity_change_on_fresh_blocks_is_allowed_and_flagged(self, setup):
        registry, log, parent, blocks = setup

        parent_with_blocks = copy.deepcopy(parent)
        parent_with_blocks["id"] = "H-POL2"
        parent_with_blocks["validation_blocks"] = ["B-V-01"]
        parent_with_blocks["signal"]["parameters"]["polarity"] = "long"
        parent_with_blocks.pop("hash", None)
        parent_with_blocks.pop("prev", None)
        registry.register(parent_with_blocks)

        successor = _successor(parent_with_blocks, id="H-POL2-A1")
        successor["validation_blocks"] = ["B-V-02"]        # fresh
        successor["signal"]["parameters"]["polarity"] = "short"

        registered, record = file_amendment(
            registry, log, "H-POL2", successor,
            rationale="Direction was backwards in the artifact.",
            impact="Invalidates the pre-registration; fresh window.",
        )
        assert record["invalidating"] is True
        assert registered["amendments"][0]["field"] == (
            "signal.parameters.polarity")


class TestNumberingAndChain:
    def test_a_second_amendment_numbers_from_the_parent(self, setup):
        registry, log, parent, blocks = setup

        first = _successor(parent, id="H-A1")
        first["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"
        file_amendment(registry, log, parent["id"], first,
                       rationale="r1", impact="i1")

        second = _successor(parent, id="H-A2")
        second["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.03"
        _, record = file_amendment(registry, log, parent["id"], second,
                                   rationale="r2", impact="i2")

        assert record["number"] == 2
        assert log.verify()[0]

    def test_the_diff_names_the_field_that_moved(self, setup):
        registry, log, parent, blocks = setup
        successor = _successor(parent)
        successor["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"

        _, record = file_amendment(registry, log, parent["id"], successor,
                                   rationale="r", impact="i")
        fields = {c["field"] for c in record["diff"]}
        assert "gate.criteria.g1.threshold.literal" in fields
        # id and depends_on moved too, but the threshold is what an auditor
        # is looking for and it must not be buried.
        assert "hash" not in fields
