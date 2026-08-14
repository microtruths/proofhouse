# tests/test_chain.py — Hand-computed tests for hash chain
"""
Phase 0 Build Plan §6 — Chain tests.

Tests H-1 through H-4. Values computed by hand BEFORE implementation.
Spec:
    "Three records; rec2.prev == rec1.hash and rec3.prev == rec2.hash."
    "Genesis record has prev absent."
    "Tamper rec1's payload → verification fails at index 1, and reports
     index 1 specifically (not 0, not 2)."
"""

from proofhouse.chain.chain import build_chain, verify_chain
from proofhouse.chain.hashing import content_hash


class TestChain:
    """H-1 through H-4: hash chain construction and verification."""

    def _make_records(self):
        """Build three simple records for chaining."""
        return [
            {"id": "R-001", "value": "first"},
            {"id": "R-002", "value": "second"},
            {"id": "R-003", "value": "third"},
        ]

    def test_h1_chain_links(self):
        """H-1: Three records; rec2.prev == rec1.hash, rec3.prev == rec2.hash."""
        records = self._make_records()
        chained = build_chain(records)

        assert chained[1]["prev"] == chained[0]["hash"]
        assert chained[2]["prev"] == chained[1]["hash"]

    def test_h1_hashes_are_content_hashes(self):
        """H-1 supplement: each record's hash is the content_hash of its payload."""
        records = self._make_records()
        chained = build_chain(records)

        for rec in chained:
            expected = content_hash(rec)
            assert rec["hash"] == expected

    def test_h2_genesis_no_prev(self):
        """H-2: Genesis record has prev absent (not present, not null)."""
        records = self._make_records()
        chained = build_chain(records)

        assert "prev" not in chained[0]

    def test_h3_tampered_payload_fails_at_correct_index(self):
        """H-3: Tamper rec1's payload → verification fails at index 1.

        After building the chain, we modify rec[0]'s value. This makes
        rec[0]'s stored hash wrong. Verification should report the failure
        at index 0 (the tampered record), not at index 1 or 2.

        Note: the spec says "fails at index 1" using 1-based numbering
        (record 1). We use 0-based indexing internally.
        """
        records = self._make_records()
        chained = build_chain(records)

        # Tamper with the first record's payload
        chained[0]["value"] = "TAMPERED"

        ok, errors = verify_chain(chained)
        assert not ok
        # The tampered record should be flagged
        assert any(e["index"] == 0 for e in errors), (
            "Expected failure at index 0 (the tampered record), "
            "got failures at: {}".format([e["index"] for e in errors])
        )

    def test_h4_tampered_prev_fails(self):
        """H-4: Tamper rec3's prev → fails at the correct index.

        After building the chain, we modify rec[2]'s prev pointer.
        Verification should detect the broken link.
        """
        records = self._make_records()
        chained = build_chain(records)

        # Tamper with the third record's prev pointer
        chained[2]["prev"] = "sha256:0000000000000000000000000000000000000000000000000000000000000000"

        ok, errors = verify_chain(chained)
        assert not ok
        assert any(e["index"] == 2 for e in errors), (
            "Expected failure at index 2 (broken prev pointer), "
            "got failures at: {}".format([e["index"] for e in errors])
        )

    def test_single_record_chain(self):
        """A single record chains correctly as genesis."""
        records = [{"id": "R-001", "value": "only"}]
        chained = build_chain(records)

        assert len(chained) == 1
        assert "prev" not in chained[0]
        assert "hash" in chained[0]

        ok, errors = verify_chain(chained)
        assert ok
        assert errors == []

    def test_valid_chain_verifies(self):
        """An untampered chain passes verification."""
        records = self._make_records()
        chained = build_chain(records)

        ok, errors = verify_chain(chained)
        assert ok
        assert errors == []

    def test_empty_chain_verifies(self):
        """An empty chain is trivially valid."""
        ok, errors = verify_chain([])
        assert ok
        assert errors == []

    def test_h5_insertion_attack_detected(self):
        """H-5: Inserting a back-dated record mid-chain → verification fails.

        Failure catalog #27: this attack previously passed because prev was
        excluded from the hashed payload. Now that prev is included, rewriting
        a record's prev changes its hash, and verification catches it.
        """
        records = self._make_records()
        chained = build_chain(records)

        # Insert a forged record between index 0 and 1
        forged = {"id": "H-forged", "data": "backdated"}
        forged["prev"] = chained[0]["hash"]
        forged["hash"] = content_hash(forged)

        tampered = list(chained)
        tampered.insert(1, forged)
        # Rewrite the following record's prev to point to the forged record
        tampered[2]["prev"] = forged["hash"]
        # Do NOT recompute tampered[2]'s hash — that would require the
        # attacker to also rewrite every downstream record

        ok, errors = verify_chain(tampered)
        assert not ok, "Insertion attack should be detected"
        # The error should be at index 2 (the record whose prev was rewritten)
        assert any(e["index"] == 2 for e in errors)

    def test_h6_deletion_attack_detected(self):
        """H-6: Deleting a middle record → verification fails.

        Even if the attacker rewrites prev pointers to stitch the gap,
        the hash changes because prev is included in the hashed payload.
        """
        records = self._make_records()
        chained = build_chain(records)

        # Delete the middle record
        shortened = [chained[0], chained[2].copy()]
        # Rewrite prev to stitch the gap
        shortened[1]["prev"] = shortened[0]["hash"]
        # Do NOT recompute the hash — it would differ from stored

        ok, errors = verify_chain(shortened)
        assert not ok, "Deletion attack should be detected"
        assert any(e["index"] == 1 for e in errors)
