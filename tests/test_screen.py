# tests/test_screen.py — Screening engine tests
"""
Phase 1 Build Plan §1b — Screening tests.

Exit criteria:
- V-block touch during stage-1 fails hard
- Stage-1 rows excluded from stage-2 count
- Transitive contamination
- Winner's curse constraint
"""

import os
import tempfile


def _make_block_registry_with_blocks(tmpdir):
    """Create a block registry with E and V blocks for testing."""
    from proofhouse.core.blocks import BlockRegistry

    br = BlockRegistry()
    br.add_block({
        "id": "fills-2024-01",
        "start": "2024-01-01",
        "end": "2024-02-28",
        "segment": "E",
        "data_complete": True,
        "completed_at": "2024-03-15",
        "sources": ["hl-node"],
        "seams": [],
    })
    br.add_block({
        "id": "fills-2024-07",
        "start": "2024-07-01",
        "end": "2024-08-31",
        "segment": "V",
        "data_complete": True,
        "completed_at": "2024-09-15",
        "sources": ["hl-node"],
        "seams": [],
    })
    return br


class TestBlockIsolation:
    """V-block touch during stage-1 must fail hard."""

    def test_stage1_touching_v_block_fails(self):
        """Stage-1 run referencing a V-block raises ValueError.

        This is the exit criterion from parent plan §6.
        """
        import pytest
        br = _make_block_registry_with_blocks(None)

        with pytest.raises(ValueError, match="cannot be touched at stage 1"):
            br.enforce_block_isolation(["fills-2024-07"], stage=1)

    def test_stage1_touching_e_block_ok(self):
        """Stage-1 run may touch E blocks."""
        br = _make_block_registry_with_blocks(None)
        # Should not raise
        br.enforce_block_isolation(["fills-2024-01"], stage=1)

    def test_stage2_may_touch_v_block(self):
        """Stage-2 run may touch V blocks."""
        br = _make_block_registry_with_blocks(None)
        # Should not raise
        br.enforce_block_isolation(["fills-2024-07"], stage=2)


class TestStageAwareCounting:
    """Stage-1 rows excluded from stage-2 multiplicity correction."""

    def test_stage1_rows_excluded_from_stage2_count(self):
        """count_trials_stage2 excludes rows with stage=1."""
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            # Two stage-1 rows
            ledger.append_row(
                code_hash="sha256:aaa", data_hash="sha256:bbb",
                universe="hl-perps", kind="explore",
                blocks_touched=["fills-2024-01"],
                stats_scored=["panel_ic"],
                stage=1, segment="E",
            )
            ledger.append_row(
                code_hash="sha256:ccc", data_hash="sha256:ddd",
                universe="hl-perps", kind="explore",
                blocks_touched=["fills-2024-01"],
                stats_scored=["panel_ic"],
                stage=1, segment="E",
            )

            # One stage-2 row
            ledger.append_row(
                code_hash="sha256:eee", data_hash="sha256:fff",
                universe="hl-perps", kind="eval",
                blocks_touched=["fills-2024-07"],
                stats_scored=["panel_ic"],
                stage=2, segment="V",
            )

            # Total count includes all 3
            assert ledger.count_trials("hl-perps") == 3

            # Stage-2 count excludes the 2 stage-1 rows
            assert ledger.count_trials_stage2("hl-perps") == 1

    def test_legacy_rows_included_in_stage2_count(self):
        """Legacy rows (no stage field) count toward stage-2 correction.

        This is the CONSERVATIVE default: unknown provenance counts
        toward multiplicity. A legacy row might have touched a V-block.
        """
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            # Legacy row (no stage/segment)
            ledger.append_row(
                code_hash="sha256:legacy", data_hash="sha256:old",
                universe="hl-perps", kind="explore",
                blocks_touched=["fills-2024-01"],
                stats_scored=["panel_ic"],
            )

            # Stage-2 count should include it
            assert ledger.count_trials_stage2("hl-perps") == 1


class TestTransitiveContamination:
    """Amended descendants of contaminated entries inherit contamination."""

    def test_descendant_inherits_contamination(self):
        """Entry depending on a contaminated entry is also contaminated."""
        from proofhouse.core.screen import check_contamination
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            # Parent entry touched V in stage 1
            ledger.append_row(
                code_hash="sha256:parent", data_hash="sha256:pdata",
                universe="hl-perps", kind="explore",
                blocks_touched=["fills-2024-07"],
                stats_scored=["panel_ic"],
                registry_ref="parent-001",
                stage=1, segment="V",
            )

            # Check parent is contaminated
            result = check_contamination("parent-001", ledger)
            assert result["contaminated"]

            # Create a mock registry with a child that depends on parent
            class MockRegistry:
                def get_entry(self, entry_id):
                    if entry_id == "child-001":
                        return {"depends_on": ["parent-001"]}
                    elif entry_id == "parent-001":
                        return {"depends_on": []}
                    raise KeyError(entry_id)

            # Child inherits contamination
            child_result = check_contamination(
                "child-001", ledger, MockRegistry()
            )
            assert child_result["contaminated"], (
                "Child of contaminated entry should be contaminated"
            )
            assert "parent-001" in child_result["reason"]


class TestWinnersCurse:
    """Updating expected_effect_size after stage-1 requires new window."""

    def test_effect_size_change_detected(self):
        """Changing expected_effect_size is detected as an amendment."""
        from proofhouse.core.screen import check_effect_size_amendment

        original = {
            "mechanism": {"expected_effect_size": 0.05},
        }
        amended = {
            "mechanism": {"expected_effect_size": 0.10},
        }

        result = check_effect_size_amendment(amended, original)
        assert result["amended"]
        assert result["original"] == 0.05
        assert result["current"] == 0.10

    def test_unchanged_effect_size_ok(self):
        """Unchanged expected_effect_size is not flagged."""
        from proofhouse.core.screen import check_effect_size_amendment

        original = {
            "mechanism": {"expected_effect_size": 0.05},
        }

        result = check_effect_size_amendment(original, original)
        assert not result["amended"]
