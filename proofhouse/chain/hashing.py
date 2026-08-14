# proofhouse.chain.hashing — content-addressed hashing
"""
SHA-256 content hashing over canonical bytes.

Phase 0 Build Plan §3:
    Hash is computed over the canonical byte representation of a record.
    The 'hash' and 'prev' fields are excluded from the payload before hashing.
    Result format: 'sha256:<hex>'
"""

import hashlib

from proofhouse.chain.canonical import canonical_bytes


def content_hash(record):
    """Compute the SHA-256 content hash of a record.

    Uses canonical_bytes() to produce deterministic bytes, then returns
    the hash as 'sha256:<hex>'.

    Args:
        record: A dict representing a proofhouse record.

    Returns:
        str: 'sha256:<64-char-hex>'
    """
    raw = canonical_bytes(record)
    digest = hashlib.sha256(raw).hexdigest()
    return "sha256:" + digest
