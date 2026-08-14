# proofhouse.chain.chain — hash chain construction and verification
"""
Hash chain over proofhouse records.

Phase 0 Build Plan §3/§6:
    Records are linked via prev pointers. Each record's hash is the
    SHA-256 of its canonical bytes. Each record's prev points to the
    hash of the preceding record. The genesis record has no prev.

    Verification recomputes every hash and checks every prev pointer.
    Failures report the specific index where the chain breaks.
"""

import copy

from proofhouse.chain.hashing import content_hash


def build_chain(records):
    """Build a hash chain over a list of records.

    Each record gets a 'hash' field (content hash of its payload) and
    a 'prev' field pointing to the preceding record's hash. The genesis
    record (index 0) has no 'prev'.

    Args:
        records: List of dicts. Each dict is deep-copied to avoid
                 mutating the caller's data.

    Returns:
        List of chained records (new list, deep-copied dicts).
    """
    chained = [copy.deepcopy(r) for r in records]

    for i, rec in enumerate(chained):
        # Remove any pre-existing chain fields so we compute fresh
        rec.pop("hash", None)
        rec.pop("prev", None)

        # Set prev pointer (genesis has none)
        if i > 0:
            rec["prev"] = chained[i - 1]["hash"]

        # Compute and store the content hash
        rec["hash"] = content_hash(rec)

    return chained


def verify_chain(records):
    """Verify the integrity of a hash chain.

    Checks two things for each record:
    1. The stored hash matches a fresh content_hash of the record.
    2. The stored prev matches the preceding record's hash
       (genesis must have no prev).

    Args:
        records: List of chained record dicts.

    Returns:
        Tuple of (ok: bool, errors: list of dicts).
        Each error dict has 'index', 'kind', and 'message'.
    """
    errors = []

    for i, rec in enumerate(records):
        # Check 1: hash integrity
        expected_hash = content_hash(rec)
        stored_hash = rec.get("hash")
        if stored_hash != expected_hash:
            errors.append({
                "index": i,
                "kind": "hash_mismatch",
                "message": (
                    "Record {}: stored hash {} does not match "
                    "recomputed hash {}".format(i, stored_hash, expected_hash)
                ),
            })

        # Check 2: prev pointer
        if i == 0:
            # Genesis: must NOT have prev
            if "prev" in rec:
                errors.append({
                    "index": i,
                    "kind": "unexpected_prev",
                    "message": "Genesis record (index 0) must not have a prev field",
                })
        else:
            # Non-genesis: prev must match preceding record's hash
            stored_prev = rec.get("prev")
            preceding_hash = records[i - 1].get("hash")
            if stored_prev != preceding_hash:
                errors.append({
                    "index": i,
                    "kind": "prev_mismatch",
                    "message": (
                        "Record {}: prev {} does not match "
                        "preceding record's hash {}".format(
                            i, stored_prev, preceding_hash
                        )
                    ),
                })

    return (len(errors) == 0, errors)
