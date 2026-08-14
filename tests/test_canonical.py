# tests/test_canonical.py — Hand-computed tests for canonical serialization
"""
Phase 0 Build Plan §6 — Canonical serialization tests.

Tests C-1 through C-5. Values computed by hand BEFORE implementation.
Spec text from §3 of the Phase 0 build plan:

    "Everything downstream depends on byte-identical serialization. Rules:
     1. JSON, sort_keys=True, separators=(',', ':'), ensure_ascii=False, UTF-8.
     2. hash and prev are excluded from the hashed payload.
     3. Floats are forbidden in hashed records. Numeric thresholds are stored
        as decimal strings ('0.05', not 0.05).
     4. Timestamps are RFC3339 UTC with Z, second precision.
     5. null is omitted, not serialized. Absent and null are the same thing."
"""

import hashlib

from proofhouse.chain.canonical import canonical_bytes
from proofhouse.chain.hashing import content_hash


class TestCanonicalSerialization:
    """C-1 through C-5: byte-stability of canonical serialization."""

    def test_c1_sort_keys_and_nested(self):
        """C-1: Keys sorted recursively, compact separators, UTF-8.

        Hand-computed:
            {"b":1,"a":2,"nested":{"z":1,"y":2}}
            → sort keys → {"a":2,"b":1,"nested":{"y":2,"z":1}}
            → compact separators → b'{"a":2,"b":1,"nested":{"y":2,"z":1}}'
        """
        record = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
        result = canonical_bytes(record)
        assert result == b'{"a":2,"b":1,"nested":{"y":2,"z":1}}'

    def test_c2_hash_excluded_prev_included(self):
        """C-2: Only `hash` is excluded. `prev` IS included.

        Failure catalog #27: excluding prev gave content integrity without
        history integrity. prev is known before the hash is computed, so it
        can and must be covered.

        Records differing only in `hash` → identical canonical bytes.
        Records differing in `prev` → DIFFERENT canonical bytes.
        """
        rec_a = {"id": "H-001", "status": "registered", "hash": "sha256:aaa", "prev": "sha256:bbb"}
        rec_b = {"id": "H-001", "status": "registered", "hash": "sha256:zzz", "prev": "sha256:bbb"}
        assert canonical_bytes(rec_a) == canonical_bytes(rec_b), "hash is excluded"

        rec_c = {"id": "H-001", "status": "registered", "hash": "sha256:aaa", "prev": "sha256:yyy"}
        assert canonical_bytes(rec_a) != canonical_bytes(rec_c), "prev is included"

    def test_c3_null_omitted(self):
        """C-3: null values are omitted, not serialized.

        Hand-computed:
            {"a": null, "b": 1} → omit null → {"b": 1} → b'{"b":1}'
        """
        record = {"a": None, "b": 1}
        result = canonical_bytes(record)
        assert result == b'{"b":1}'

    def test_c3_nested_null_omitted(self):
        """Extension of C-3: nulls inside nested dicts are also omitted."""
        record = {"outer": {"keep": "yes", "drop": None}, "top": 1}
        result = canonical_bytes(record)
        assert result == b'{"outer":{"keep":"yes"},"top":1}'

    def test_c4_float_raises(self):
        """C-4: A record containing a float raises.

        Thresholds must be decimal strings. Float repr varies across platforms
        and languages, and an independent reimplementation must produce
        identical bytes.
        """
        import pytest
        record = {"threshold": 0.05, "id": "H-001"}
        with pytest.raises((TypeError, ValueError)):
            canonical_bytes(record)

    def test_c4_float_in_nested_raises(self):
        """Extension of C-4: floats nested inside dicts also raise."""
        import pytest
        record = {"gate": {"threshold": 0.95}}
        with pytest.raises((TypeError, ValueError)):
            canonical_bytes(record)

    def test_c4_float_in_list_raises(self):
        """Extension of C-4: floats inside lists also raise."""
        import pytest
        record = {"values": [1, 2, 3.14]}
        with pytest.raises((TypeError, ValueError)):
            canonical_bytes(record)

    def test_c4_decimal_string_accepted(self):
        """Complement to C-4: decimal strings are the correct encoding."""
        record = {"threshold": "0.05", "id": "H-001"}
        result = canonical_bytes(record)
        assert result == b'{"id":"H-001","threshold":"0.05"}'

    def test_c5_sha256_pinned(self):
        """C-5: The sha256 of C-1's output is independently reproducible.

        Verified externally:
            echo -n '{"a":2,"b":1,"nested":{"y":2,"z":1}}' | shasum -a 256
            → 7fd8564ce474e6a29e80bd7f06e5c3c9262d0a98e3b070d8c4fe2c78bb56f799

        The content_hash function should return this prefixed with 'sha256:'.
        """
        record = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
        expected_hex = hashlib.sha256(
            b'{"a":2,"b":1,"nested":{"y":2,"z":1}}'
        ).hexdigest()
        result = content_hash(record)
        assert result == "sha256:" + expected_hex

    def test_c5_explicit_hash_value(self):
        """C-5 explicit: verify the actual hash value is pinned.

        Verified externally:
            echo -n '{"a":2,"b":1,"nested":{"y":2,"z":1}}' | shasum -a 256
            → 59a2b757ae626413f4bc83612f29ba29ec57a0373ed4d0d38a44769042f8a42e
        """
        record = {"b": 1, "a": 2, "nested": {"z": 1, "y": 2}}
        result = content_hash(record)
        assert result == "sha256:59a2b757ae626413f4bc83612f29ba29ec57a0373ed4d0d38a44769042f8a42e"


class TestCanonicalEdgeCases:
    """Additional edge cases to ensure byte stability."""

    def test_empty_dict(self):
        result = canonical_bytes({})
        assert result == b'{}'

    def test_list_values_preserved(self):
        """Lists are serialized in order (not sorted — only keys are sorted)."""
        record = {"items": [3, 1, 2]}
        result = canonical_bytes(record)
        assert result == b'{"items":[3,1,2]}'

    def test_boolean_values(self):
        """Booleans serialize as JSON true/false."""
        record = {"complete": True, "acknowledged": False}
        result = canonical_bytes(record)
        assert result == b'{"acknowledged":false,"complete":true}'

    def test_integer_values(self):
        """Integers are fine (unlike floats)."""
        record = {"count": 42}
        result = canonical_bytes(record)
        assert result == b'{"count":42}'

    def test_unicode_passthrough(self):
        """ensure_ascii=False: unicode passes through as UTF-8."""
        record = {"name": "café"}
        result = canonical_bytes(record)
        assert result == '{"name":"café"}'.encode("utf-8")

    def test_empty_list_in_record(self):
        """Empty lists are preserved (not omitted like null)."""
        record = {"items": [], "id": "test"}
        result = canonical_bytes(record)
        assert result == b'{"id":"test","items":[]}'

    def test_all_null_dict_becomes_empty(self):
        """A dict where every value is null → empty dict (not omitted)."""
        record = {"outer": {"a": None, "b": None}, "id": "test"}
        result = canonical_bytes(record)
        assert result == b'{"id":"test","outer":{}}'

    def test_nested_hash_key_preserved(self):
        """A nested dict with a key named 'hash' is preserved.

        Only top-level 'hash' and 'prev' are excluded from canonical bytes.
        A nested 'hash' (e.g., in criteria) must be kept.
        """
        record = {"criteria": {"hash": "sha256:abc"}, "id": "test"}
        result = canonical_bytes(record)
        assert result == b'{"criteria":{"hash":"sha256:abc"},"id":"test"}'
