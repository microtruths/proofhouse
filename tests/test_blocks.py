# tests/test_blocks.py — Hand-computed tests for block registry
"""
Phase 0 Build Plan §6 — Block tests.

Tests B-1 through B-4. Written before implementation.
Spec from §4.1:
    "data_complete is flipped by an explicit completion event, never inferred
     from end being in the past."
    "seams records data-source boundaries inside the block. A block with
     non-empty seams cannot be assigned segment V unless the consuming entry
     carries seam_acknowledged: true."
"""

import pytest

from proofhouse.core.blocks import BlockRegistry


class TestBlocks:
    """B-1 through B-4: block registry and consumption."""

    def _make_registry(self):
        """Create a block registry with test blocks."""
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-2025-01",
            "start": "2025-03-25T00:00:00Z",
            "end": "2025-05-25T00:00:00Z",
            "segment": "E",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills"],
            "seams": [],
        })
        reg.add_block({
            "id": "B-2025-02",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-07-25T00:00:00Z",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills"],
            "seams": [],
        })
        reg.add_block({
            "id": "B-2025-03",
            "start": "2025-07-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "R",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills"],
            "seams": [],
        })
        return reg

    def test_b1_double_consumption_refused(self):
        """B-1: Consuming a V-block twice for different entries → second refused.

        A V-block is single-use. Once consumed by entry H-001, attempting
        to consume it for H-002 must raise.
        """
        reg = self._make_registry()

        # First consumption succeeds
        reg.consume_block("B-2025-02", "H-001")

        # Second consumption for a different entry is refused
        with pytest.raises(ValueError, match="already consumed"):
            reg.consume_block("B-2025-02", "H-002")

    def test_b1_same_entry_idempotent(self):
        """B-1 supplement: consuming the same block for the same entry is OK."""
        reg = self._make_registry()
        reg.consume_block("B-2025-02", "H-001")
        # Should not raise — idempotent
        reg.consume_block("B-2025-02", "H-001")

    def test_b2_incomplete_block_refused(self):
        """B-2 (A6): data_complete=false + end in the past → consumption refused.

        A block with data_complete=false cannot be consumed, regardless of
        whether its end date has passed. Availability is keyed to data
        completion, not calendar dates.
        """
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-2025-99",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z",  # well in the past
            "segment": "V",
            "data_complete": False,  # NOT complete
            "completed_at": None,
            "sources": ["node_fills"],
            "seams": [],
        })

        with pytest.raises(ValueError, match="data_complete"):
            reg.consume_block("B-2025-99", "H-001")

    def test_b3_seam_without_acknowledgment_refused(self):
        """B-3 (§2.4.2): seam + segment V, no seam_acknowledged → refused.

        A block with non-empty seams assigned segment V requires the consuming
        entry to carry seam_acknowledged=True. This is failure catalog #12:
        "splices need overlap + reconciliation; never let a seam sit
        unvalidated inside a test period."
        """
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-2025-SEAM",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills", "node_fills_by_block"],
            "seams": ["2025-07-27"],
        })

        with pytest.raises(ValueError, match="seam"):
            reg.consume_block("B-2025-SEAM", "H-001", seam_acknowledged=False)

    def test_b3_seam_with_acknowledgment_allowed(self):
        """B-3 complement: seam + segment V + seam_acknowledged → allowed."""
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-2025-SEAM",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills", "node_fills_by_block"],
            "seams": ["2025-07-27"],
        })

        # Should succeed with acknowledgment
        reg.consume_block("B-2025-SEAM", "H-001", seam_acknowledged=True)

        block = reg.get_block("B-2025-SEAM")
        # The acknowledgment should be recorded in the consumption
        assert any(
            c.get("seam_acknowledged") for c in block.get("consumed_by", [])
        )

    def test_b3_seam_on_non_v_segment_no_check(self):
        """B-3 edge: seams on non-V segments don't require acknowledgment."""
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-SEAM-E",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "E",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills", "node_fills_by_block"],
            "seams": ["2025-07-27"],
        })

        # E-segment with seams: no acknowledgment needed
        reg.consume_block("B-SEAM-E", "H-001", seam_acknowledged=False)

    def test_b4_promote_r_to_v(self):
        """B-4: Promoting R→V is logged."""
        reg = self._make_registry()

        reg.promote_block("B-2025-03", "V")
        block = reg.get_block("B-2025-03")
        assert block["segment"] == "V"

    def test_b4_demote_v_to_r_refused(self):
        """B-4: V→R is refused (blocks do not un-spend)."""
        reg = self._make_registry()

        with pytest.raises(ValueError, match="cannot demote"):
            reg.promote_block("B-2025-02", "R")

    def test_consume_e_block_allowed(self):
        """E-blocks can be consumed freely (unlimited reuse)."""
        reg = self._make_registry()

        reg.consume_block("B-2025-01", "H-001")
        reg.consume_block("B-2025-01", "H-002")
        reg.consume_block("B-2025-01", "H-003")

        block = reg.get_block("B-2025-01")
        assert len(block["consumed_by"]) == 3

    def test_block_not_found_raises(self):
        """Consuming a nonexistent block raises."""
        reg = BlockRegistry()

        with pytest.raises(KeyError):
            reg.consume_block("B-NONEXISTENT", "H-001")

    def test_complete_block(self):
        """Completing a block sets data_complete and completed_at."""
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-PENDING",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z",
            "segment": "R",
            "data_complete": False,
            "completed_at": None,
            "sources": ["node_fills"],
            "seams": [],
        })

        reg.complete_block("B-PENDING", "2026-08-14T00:00:00Z")
        block = reg.get_block("B-PENDING")
        assert block["data_complete"] is True
        assert block["completed_at"] == "2026-08-14T00:00:00Z"
