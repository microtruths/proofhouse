# tests/test_ledger.py — Hand-computed tests for the ledger
"""
Phase 0 Build Plan §6 — Ledger tests.

Tests L-1 through L-9 plus overlap and run_id uniqueness.
Spec from §4.3 and governance sub-rules 7a–7h:
    "kind: explore | eval | gate | maintenance"
    "7b: Per-universe counters, with declared overlap policy."
    "7e: Dedup by code hash + data hash + universe."
    "7f: Row written at scoring time, not completion."
    "7g: kind: maintenance rows: logged, excluded from multiplicity."
"""

import os
import tempfile

import pytest

from proofhouse.core.ledger import Ledger


class TestLedger:
    """L-1 through L-9: ledger counting, chaining, and persistence."""

    def _make_ledger(self, tmpdir=None):
        """Create a ledger in a temp directory."""
        if tmpdir is None:
            tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "ledger.jsonl")
        return Ledger(path)

    def test_l1_dedup(self):
        """L-1 (dedup, rule 7e): identical code_hash + data_hash + universe → counted once.

        Rows: (cA, dA, uX), (cB, dB, uX), (cA, dA, uX).
        count_trials("uX") == 2, not 3.
        """
        ledger = self._make_ledger()

        ledger.append_row(code_hash="cA", data_hash="dA", universe="uX",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="cB", data_hash="dB", universe="uX",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="cA", data_hash="dA", universe="uX",
                          kind="explore", blocks_touched=[], stats_scored=[])

        assert ledger.count_trials("uX") == 2

    def test_l2_maintenance_excluded(self):
        """L-2 (maintenance, 7g): maintenance rows excluded from count.

        Four rows, one kind: maintenance → count_trials == 3.
        """
        ledger = self._make_ledger()

        ledger.append_row(code_hash="c1", data_hash="d1", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c2", data_hash="d2", universe="u1",
                          kind="eval", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c3", data_hash="d3", universe="u1",
                          kind="maintenance", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c4", data_hash="d4", universe="u1",
                          kind="gate", blocks_touched=[], stats_scored=[])

        assert ledger.count_trials("u1") == 3

    def test_l3_per_universe(self):
        """L-3 (per-universe, 7b): independent counts per universe.

        2 rows on uA, 3 on uB → count("uA") == 2, count("uB") == 3.
        """
        ledger = self._make_ledger()

        # 2 on uA
        ledger.append_row(code_hash="c1", data_hash="d1", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c2", data_hash="d2", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])

        # 3 on uB
        ledger.append_row(code_hash="c3", data_hash="d3", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c4", data_hash="d4", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c5", data_hash="d5", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])

        assert ledger.count_trials("uA") == 2
        assert ledger.count_trials("uB") == 3

    def test_l4_overlap_policy(self):
        """L-4 (overlap policy, 7b): overlapping universes share count.

        uA and uB share an instrument. Rows on either universe should
        be counted when querying the shared universe.

        Implementation: declare_overlap("uA", "uB") → they share a count.
        """
        ledger = self._make_ledger()
        ledger.declare_overlap("uA", "uB")

        # 3 on uA
        ledger.append_row(code_hash="c1", data_hash="d1", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c2", data_hash="d2", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c3", data_hash="d3", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])

        # 2 on uB
        ledger.append_row(code_hash="c4", data_hash="d4", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c5", data_hash="d5", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])

        # Both should report 5 total (shared count)
        assert ledger.count_trials("uA") == 5
        assert ledger.count_trials("uB") == 5

    def test_l5_write_timing(self):
        """L-5 (write timing, 7f): a run that raises after scoring still
        leaves its ledger row.

        The ledger row is written at scoring time (append_row), not at
        run completion. If subsequent processing raises, the row persists.
        """
        tmpdir = tempfile.mkdtemp()
        ledger = self._make_ledger(tmpdir)

        # Write a row
        ledger.append_row(code_hash="c1", data_hash="d1", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=["alpha"])

        # Simulate: subsequent processing raises
        # (The ledger row should already be persisted to disk)

        # Reload from disk — the row must survive
        ledger2 = Ledger(os.path.join(tmpdir, "ledger.jsonl"))
        assert ledger2.count_trials("u1") == 1

    def test_dedup_different_universe_not_deduped(self):
        """Same code+data on different universes → counted independently."""
        ledger = self._make_ledger()

        ledger.append_row(code_hash="c1", data_hash="d1", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c1", data_hash="d1", universe="uB",
                          kind="explore", blocks_touched=[], stats_scored=[])

        assert ledger.count_trials("uA") == 1
        assert ledger.count_trials("uB") == 1

    def test_dedup_row_still_logged(self):
        """Dedup prevents counting but the row is still written to the file."""
        tmpdir = tempfile.mkdtemp()
        ledger = self._make_ledger(tmpdir)

        ledger.append_row(code_hash="c1", data_hash="d1", universe="uX",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c1", data_hash="d1", universe="uX",
                          kind="explore", blocks_touched=[], stats_scored=[])

        # Count excludes duplicates
        assert ledger.count_trials("uX") == 1

        # But both rows exist in the file
        with open(os.path.join(tmpdir, "ledger.jsonl")) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 2

    def test_empty_universe_count(self):
        """Counting trials for an unseen universe returns 0."""
        ledger = self._make_ledger()
        assert ledger.count_trials("never-seen") == 0

    def test_l6_rows_carry_hash_and_prev(self):
        """L-6: Ledger rows carry hash and prev fields forming a chain.

        The ledger is the multiple-testing counter — the highest-value
        tampering target. Every row must be chained.
        """
        ledger = self._make_ledger()

        ledger.append_row(code_hash="c1", data_hash="d1", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c2", data_hash="d2", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])

        rows = ledger.all_rows()
        assert "hash" in rows[0], "row 0 must have hash"
        assert "prev" not in rows[0], "row 0 is genesis — no prev"
        assert "hash" in rows[1], "row 1 must have hash"
        assert "prev" in rows[1], "row 1 must have prev"
        assert rows[1]["prev"] == rows[0]["hash"], "prev must point to preceding hash"

    def test_l7_deletion_detected(self):
        """L-7: Deleting a ledger row → chain verification fails.

        Even if prev pointers are rewritten, the hash changes because
        prev is included in the hashed payload.
        """
        tmpdir = tempfile.mkdtemp()
        ledger = self._make_ledger(tmpdir)

        ledger.append_row(code_hash="c1", data_hash="d1", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c2", data_hash="d2", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])
        ledger.append_row(code_hash="c3", data_hash="d3", universe="u1",
                          kind="explore", blocks_touched=[], stats_scored=[])

        # Verify the intact chain
        ok, errors = ledger.verify()
        assert ok, "Intact chain should verify"

        # Tamper: delete the middle row from disk and memory
        import json
        path = os.path.join(tmpdir, "ledger.jsonl")
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip()]
        assert len(lines) == 3

        # Delete middle line and rewrite file
        tampered_lines = [lines[0], lines[2]]
        with open(path, "w") as f:
            for line in tampered_lines:
                f.write(line + "\n")

        # Reload — should detect the break
        ledger2 = Ledger(path)
        ok2, errors2 = ledger2.verify()
        assert not ok2, "Deletion should be detected"
        assert len(errors2) > 0

    def test_l8_canonical_json_on_disk(self):
        """L-8: Ledger writes canonical JSON (sorted keys, compact separators)."""
        tmpdir = tempfile.mkdtemp()
        ledger = self._make_ledger(tmpdir)

        ledger.append_row(code_hash="c1", data_hash="d1", universe="u1",
                          kind="explore", blocks_touched=["B-1"],
                          stats_scored=["alpha"])

        import json
        path = os.path.join(tmpdir, "ledger.jsonl")
        with open(path) as f:
            line = f.readline().strip()

        # Canonical JSON has sorted keys and compact separators
        parsed = json.loads(line)
        # Re-serialize with canonical rules and verify it matches
        reserialized = json.dumps(parsed, sort_keys=True, separators=(",", ":"))
        assert line == reserialized, (
            "Ledger should write canonical JSON. Got:\n{}\nExpected:\n{}".format(
                line, reserialized
            )
        )

    def test_overlap_persisted_across_sessions(self):
        """Overlap declarations survive reload.

        Declarations are stored as special rows in the JSONL,
        so they are replayed on init.
        """
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "ledger.jsonl")

        # Session 1: declare overlap and add rows
        ledger1 = Ledger(path)
        ledger1.declare_overlap("uA", "uB")
        ledger1.append_row(code_hash="c1", data_hash="d1", universe="uA",
                           kind="explore", blocks_touched=[], stats_scored=[])
        ledger1.append_row(code_hash="c2", data_hash="d2", universe="uB",
                           kind="explore", blocks_touched=[], stats_scored=[])
        assert ledger1.count_trials("uA") == 2

        # Session 2: reload — overlap should be replayed
        ledger2 = Ledger(path)
        assert ledger2.count_trials("uA") == 2, "Overlap must survive reload"
        assert ledger2.count_trials("uB") == 2

    def test_l9_verify_survives_reload(self):
        """L-9: verify() passes after reload from disk.

        This is the round-trip integrity test. Write rows, reload from
        the JSONL file, and verify the chain. If hash were missing from
        the on-disk format, this test would fail.
        """
        tmpdir = tempfile.mkdtemp()
        path = os.path.join(tmpdir, "ledger.jsonl")

        # Session 1: write rows
        ledger1 = Ledger(path)
        ledger1.append_row(code_hash="c1", data_hash="d1", universe="u1",
                           kind="explore", blocks_touched=[], stats_scored=[])
        ledger1.append_row(code_hash="c2", data_hash="d2", universe="u1",
                           kind="eval", blocks_touched=[], stats_scored=[])
        ledger1.append_row(code_hash="c3", data_hash="d3", universe="u1",
                           kind="gate", blocks_touched=[], stats_scored=[])

        # In-memory verify
        ok1, errors1 = ledger1.verify()
        assert ok1, "In-memory chain must verify"

        # Session 2: reload and verify
        ledger2 = Ledger(path)
        ok2, errors2 = ledger2.verify()
        assert ok2, (
            "Reloaded chain must verify. Errors: {}".format(errors2)
        )

        # Also verify hash and prev are present
        rows = ledger2.all_rows()
        assert rows[0].get("hash") is not None, "hash must survive reload"
        assert "prev" not in rows[0], "genesis has no prev"
        assert rows[1].get("prev") == rows[0].get("hash")

    def test_overlap_and_append_run_ids_unique(self):
        """OVL and R run_ids must not collide.

        declare_overlap must increment _next_run so that a subsequent
        append_row doesn't reuse the same number.
        """
        ledger = self._make_ledger()

        ledger.declare_overlap("uA", "uB")
        ledger.append_row(code_hash="c1", data_hash="d1", universe="uA",
                          kind="explore", blocks_touched=[], stats_scored=[])

        rows = ledger.all_rows()
        run_ids = [r["run_id"] for r in rows]
        assert len(run_ids) == len(set(run_ids)), (
            "run_ids must be unique, got: {}".format(run_ids)
        )
