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

    def test_stage2_count_respects_overlap(self):
        """Overlapping universes share stage-2 counts too."""
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            ledger.declare_overlap("u-A", "u-B")

            # Stage-1 row in u-A
            ledger.append_row(
                code_hash="sha256:s1", data_hash="sha256:d1",
                universe="u-A", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )

            # Stage-2 row in u-B (overlaps with u-A)
            ledger.append_row(
                code_hash="sha256:s2", data_hash="sha256:d2",
                universe="u-B", kind="gate",
                blocks_touched=["v-block"],
                stats_scored=["ic"],
                stage=2, segment="V",
            )

            # Query u-A: total=2 (cross-universe overlap), stage-2=1
            assert ledger.count_trials("u-A") == 2
            assert ledger.count_trials_stage2("u-A") == 1

    def test_stage_fields_survive_reload(self):
        """Stage and segment fields are persisted and survive reload."""
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "ledger.jsonl")

            ledger = Ledger(path)
            ledger.append_row(
                code_hash="sha256:code", data_hash="sha256:data",
                universe="u", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )

            # Reload from disk
            ledger2 = Ledger(path)
            rows = ledger2.all_rows()
            assert len(rows) == 1
            assert rows[0].get("stage") == 1
            assert rows[0].get("segment") == "E"

            # Stage-2 count still correct after reload
            assert ledger2.count_trials_stage2("u") == 0  # only stage-1 row



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


class TestScreenStage1a:
    """Stage 1a: FDR decides who passes; magnitude only orders survivors.

    Rewritten when the BH stub was replaced (Part C). The previous tests
    encoded the stub's behaviour — "any non-zero magnitude survives" — which
    was the deliberately permissive placeholder, not the intended contract.
    """

    def _make_result(self, value, p_value):
        from proofhouse.eval.types import ScoredResult
        return ScoredResult(
            statistic="ic", value=value, trial_count=1, p_value=p_value
        )

    def test_fdr_decides_membership_magnitude_only_orders(self):
        """A large magnitude with a weak p-value does NOT survive.

        This is the whole point of un-stubbing: under the stub, `huge`
        passed on magnitude alone.
        """
        from proofhouse.core.screen import screen_stage1a

        results = {
            "huge": self._make_result(0.90, 0.99),
            "small": self._make_result(0.01, 0.001),
        }
        out = screen_stage1a([], results, fdr_q=0.05, fdr_method="bh")
        assert out["survivors"] == ["small"]
        assert "huge" not in out["survivors"]

    def test_survivors_ranked_by_magnitude(self):
        """Among survivors, order is by descending |value|."""
        from proofhouse.core.screen import screen_stage1a

        results = {
            "weak": self._make_result(0.01, 0.001),
            "strong": self._make_result(0.10, 0.002),
            "medium": self._make_result(0.05, 0.003),
        }
        out = screen_stage1a([], results, fdr_q=0.3, fdr_method="bh")
        assert out["survivors"] == ["strong", "medium", "weak"]

    def test_negative_values_ranked_by_absolute(self):
        from proofhouse.core.screen import screen_stage1a

        results = {
            "neg_strong": self._make_result(-0.10, 0.001),
            "pos_weak": self._make_result(0.02, 0.002),
        }
        out = screen_stage1a([], results, fdr_q=0.3, fdr_method="bh")
        assert out["survivors"] == ["neg_strong", "pos_weak"]

    def test_missing_p_value_refuses(self):
        """Dropping the entry would shrink m and change every threshold."""
        from proofhouse.core.screen import screen_stage1a

        results = {
            "ok": self._make_result(0.05, 0.01),
            "no_p": self._make_result(0.05, None),
        }
        try:
            screen_stage1a([], results, fdr_q=0.3, fdr_method="bh")
        except ValueError as e:
            assert "p_value" in str(e)
        else:
            raise AssertionError("expected refusal on missing p_value")

    def test_by_is_stricter_than_bh(self):
        from proofhouse.core.screen import screen_stage1a

        results = {
            "a": self._make_result(0.10, 0.001),
            "b": self._make_result(0.09, 0.02),
            "c": self._make_result(0.08, 0.04),
        }
        bh = screen_stage1a([], results, fdr_q=0.05, fdr_method="bh")
        by = screen_stage1a([], results, fdr_q=0.05, fdr_method="by")
        assert set(by["survivors"]).issubset(set(bh["survivors"]))

    def test_unknown_method_rejected(self):
        from proofhouse.core.screen import screen_stage1a

        try:
            screen_stage1a([], {}, fdr_q=0.3, fdr_method="bonferroni")
        except ValueError as e:
            assert "fdr_method" in str(e)
        else:
            raise AssertionError("expected refusal on unknown fdr_method")

    def test_empty_results(self):
        from proofhouse.core.screen import screen_stage1a

        out = screen_stage1a([], {}, fdr_q=0.3, fdr_method="bh")
        assert out["survivors"] == []
        assert out["fdr"]["m"] == 0

    def test_fdr_block_returned_for_the_screen_record(self):
        """run_screen needs m/k/threshold/method to write the screen row."""
        from proofhouse.core.screen import screen_stage1a

        results = {"a": self._make_result(0.1, 0.001)}
        out = screen_stage1a([], results, fdr_q=0.05, fdr_method="bh")
        for key in ("m", "k", "threshold", "method", "q"):
            assert key in out["fdr"]


class TestScreenStage1b:
    """Stage 1b consistency screen tests."""

    def _make_result(self, value):
        from proofhouse.eval.types import ScoredResult
        return ScoredResult(statistic="ic", value=value, trial_count=1)

    def test_consistent_entry_survives(self):
        """Entry with >50% sign consistency passes."""
        from proofhouse.core.screen import screen_stage1b

        results = {"entry1": self._make_result(0.05)}
        # 4 of 5 sub-periods have same sign
        sub_periods = {"entry1": [0.03, 0.02, 0.01, 0.04, -0.01]}

        survivors = screen_stage1b(["entry1"], results, sub_periods)
        assert "entry1" in survivors

    def test_inconsistent_entry_filtered(self):
        """Entry with <=50% sign consistency is filtered."""
        from proofhouse.core.screen import screen_stage1b

        results = {"entry1": self._make_result(0.05)}
        # Only 1 of 4 sub-periods has positive sign
        sub_periods = {"entry1": [0.01, -0.02, -0.03, -0.04]}

        survivors = screen_stage1b(["entry1"], results, sub_periods)
        assert "entry1" not in survivors

    def test_missing_sub_period_data_filtered(self):
        """Entry with no sub-period results is filtered."""
        from proofhouse.core.screen import screen_stage1b

        results = {"entry1": self._make_result(0.05)}
        sub_periods = {}  # No sub-period data

        survivors = screen_stage1b(["entry1"], results, sub_periods)
        assert "entry1" not in survivors

    def test_empty_sub_periods_filtered(self):
        """Entry with empty sub-period list is filtered."""
        from proofhouse.core.screen import screen_stage1b

        results = {"entry1": self._make_result(0.05)}
        sub_periods = {"entry1": []}

        survivors = screen_stage1b(["entry1"], results, sub_periods)
        assert "entry1" not in survivors

