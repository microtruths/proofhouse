# tests/test_blocks.py — Hand-computed tests for block registry
"""
Phase 0 Build Plan §6 — Block tests.

Tests B-1 through B-4 plus persistence. Written before implementation.
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
    """B-1 through B-4: block registry, consumption, and persistence."""

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
        """B-4: V→R is refused (V is terminal — blocks do not un-spend)."""
        reg = self._make_registry()

        with pytest.raises(ValueError, match="terminal segment"):
            reg.promote_block("B-2025-02", "R")

    def test_b4_promote_v_to_l_refused(self):
        """V→L is refused. V is terminal — spent blocks don't become live data."""
        reg = self._make_registry()

        with pytest.raises(ValueError, match="terminal segment"):
            reg.promote_block("B-2025-02", "L")

    # ---------------------------------------------------------------
    # B-5 through B-9: the registry reads its own consumption record.
    #
    # Until 2026-08-19 it did not. B-2026-03 in the live instance carried
    # segment R ("Reserve — untouched") while two pipelines had read it,
    # and promote_block, verify and `blocks list` all looked straight past
    # the consumed_by list sitting next to the label.
    # ---------------------------------------------------------------

    def test_b5_promote_to_v_refused_when_consumed(self):
        """B-5: a block something has already read is not reserve data.

        Promotion to V is the act of claiming a block is unexamined
        validation data. Making that claim about a block whose consumed_by
        is non-empty is false on its face, and nothing used to object.
        """
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-ACCOHORT")

        with pytest.raises(ValueError, match="already been read"):
            reg.promote_block("B-2025-03", "V")

    def test_b5_promote_to_v_allowed_with_acknowledgment(self):
        """B-5: the escape hatch, modeled on the seam precedent (#12).

        An absolute refusal gets routed around. The rationale is mandatory
        and is recorded on the block.
        """
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-ACCOHORT")

        reg.promote_block("B-2025-03", "V",
                          acknowledge_prior_consumption=True,
                          rationale="Exploration only; no return statistic "
                                    "was scored on this window.")
        block = reg.get_block("B-2025-03")
        assert block["segment"] == "V"
        assert block["promotion_acknowledgment"]["rationale"].startswith(
            "Exploration only")
        assert block["promotion_acknowledgment"]["prior_consumption"] == [
            "H-2026-ACCOHORT"]

    def test_b5_acknowledgment_without_rationale_refused(self):
        """B-5: acknowledging without saying why is not acknowledging."""
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-ACCOHORT")

        with pytest.raises(ValueError, match="rationale"):
            reg.promote_block("B-2025-03", "V",
                              acknowledge_prior_consumption=True)
        with pytest.raises(ValueError, match="rationale"):
            reg.promote_block("B-2025-03", "V",
                              acknowledge_prior_consumption=True,
                              rationale="   ")

    def test_b5_promotion_to_lower_segments_untouched(self):
        """B-5: only promotion TO V is guarded. R→V is the spending act."""
        reg = self._make_registry()
        reg.consume_block("B-2025-01", "H-001")     # segment E
        reg.promote_block("B-2025-01", "S2")        # no acknowledgment needed
        assert reg.get_block("B-2025-01")["segment"] == "S2"

    def test_b6_train_then_validate_on_one_block_is_blocked(self):
        """B-6: THE hole. Train on a reserve block, promote it, validate on it.

        E4 refuses a V-block consumed by a DIFFERENT entry. It never refused
        the same entry — that path returns early as idempotent. So one entry
        could walk a block from reserve to validation and score itself on the
        data it trained on, and the finished record was byte-for-byte
        identical to a block held clean and validated once.
        """
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-X")          # trains on it

        with pytest.raises(ValueError, match="already been read"):
            reg.promote_block("B-2025-03", "V")             # was allowed

    def test_b7_seam_check_survives_reconsumption(self):
        """B-7: the seam gate ran after E4's idempotent return, so it was
        skippable.

        consume as R (seam check does not apply to R) → promote to V →
        consume again as the same entry → the early return fired and the
        seam was never checked. Failure catalog #12 defeated by statement
        ordering.

        Asserts the PROPERTY — a seam is never crossed into V unacknowledged
        — rather than that a particular call happened.
        """
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-SEAM-RV",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "R",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["node_fills", "node_fills_by_block"],
            "seams": ["2025-07-27"],
        })

        reg.consume_block("B-SEAM-RV", "H-001")     # legal: R has no seam gate
        reg.promote_block("B-SEAM-RV", "V",
                          acknowledge_prior_consumption=True,
                          rationale="test fixture")

        with pytest.raises(ValueError, match="seam"):
            reg.consume_block("B-SEAM-RV", "H-001", seam_acknowledged=False)

    def test_b7_late_seam_acknowledgment_is_recorded(self):
        """B-7: an acknowledgment arriving on re-consumption is not dropped.

        The early return discarded it silently, so the caller had no way to
        tell that what they passed had been ignored.
        """
        reg = BlockRegistry()
        reg.add_block({
            "id": "B-SEAM-LATE",
            "start": "2025-05-25T00:00:00Z",
            "end": "2025-09-25T00:00:00Z",
            "segment": "R",
            "data_complete": True,
            "completed_at": "2026-01-01T00:00:00Z",
            "sources": ["a", "b"],
            "seams": ["2025-07-27"],
        })
        reg.consume_block("B-SEAM-LATE", "H-001")
        reg.promote_block("B-SEAM-LATE", "V",
                          acknowledge_prior_consumption=True,
                          rationale="test fixture")

        reg.consume_block("B-SEAM-LATE", "H-001", seam_acknowledged=True)
        record = reg.get_block("B-SEAM-LATE")["consumed_by"][0]
        assert record["seam_acknowledged"] is True

    def test_b8_acknowledge_consumption_records_a_note(self):
        """B-8: option 3 — the segment stays, an explanation is attached."""
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-ACCOHORT")

        reg.acknowledge_consumption(
            "B-2025-03", "H-2026-ACCOHORT",
            rationale="W6 overlapped this window and the cohort pipeline "
                      "trained through it. Contaminated for wallet-selection "
                      "hypotheses; holdout-grade at best for anything else.",
            acknowledged_at="2026-08-19T00:00:00Z")

        notes = reg.get_block("B-2025-03")["consumption_notes"]
        assert len(notes) == 1
        assert notes[0]["entry_id"] == "H-2026-ACCOHORT"
        assert notes[0]["acknowledged_at"] == "2026-08-19T00:00:00Z"
        assert reg.get_block("B-2025-03")["segment"] == "R"   # unchanged

    def test_b8_note_before_consumption_refused(self):
        """B-8: no blank cheques.

        A note filed before the entry consumed anything would pre-clear all
        future contamination — verify would pass on a block nobody had
        examined yet and on every reading of it thereafter.
        """
        reg = self._make_registry()

        with pytest.raises(ValueError, match="has not consumed"):
            reg.acknowledge_consumption(
                "B-2025-03", "H-2026-FUTURE", rationale="pre-clearing",
                acknowledged_at="2026-08-19T00:00:00Z")

    def test_b8_note_requires_a_rationale(self):
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-001")

        with pytest.raises(ValueError, match="rationale"):
            reg.acknowledge_consumption(
                "B-2025-03", "H-001", rationale="  ",
                acknowledged_at="2026-08-19T00:00:00Z")

    def test_b9_promotion_refusal_quotes_the_note(self):
        """B-9: the explanation surfaces when someone tries to spend the block.

        The whole return on option 3. A note in a document written in August
        does not stop anything in November; a refusal that reads the note
        back does.
        """
        reg = self._make_registry()
        reg.consume_block("B-2025-03", "H-2026-ACCOHORT")
        reg.acknowledge_consumption(
            "B-2025-03", "H-2026-ACCOHORT",
            rationale="cohort pipeline trained through this window",
            acknowledged_at="2026-08-19T00:00:00Z")

        with pytest.raises(ValueError, match="cohort pipeline trained"):
            reg.promote_block("B-2025-03", "V")

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

    def test_persistence_round_trip(self):
        """Path-backed BlockRegistry survives reload.

        Add blocks, consume one, reload from disk, and verify state.
        """
        import os
        import tempfile

        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "blocks.yaml")

        # Session 1: create and mutate
        reg1 = BlockRegistry(path=path)
        reg1.add_block({
            "id": "B-RT-01",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z",
            "segment": "E",
            "data_complete": True,
            "completed_at": "2025-04-01T00:00:00Z",
            "sources": ["test"],
            "seams": [],
        })
        reg1.add_block({
            "id": "B-RT-02",
            "start": "2025-03-01T00:00:00Z",
            "end": "2025-05-01T00:00:00Z",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2025-06-01T00:00:00Z",
            "sources": ["test"],
            "seams": [],
        })
        reg1.consume_block("B-RT-02", "H-001")

        # Session 2: reload from disk
        reg2 = BlockRegistry(path=path)
        assert len(reg2.list_blocks()) == 2
        block = reg2.get_block("B-RT-02")
        assert block["segment"] == "V"
        assert len(block["consumed_by"]) == 1
        assert block["consumed_by"][0]["entry_id"] == "H-001"
