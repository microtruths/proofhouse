# proofhouse.chain.canonical — deterministic serialization
"""
Canonical serialization for proofhouse records.

Phase 0 Build Plan §3 (AMENDED per failure catalog #27):
    "Everything downstream depends on byte-identical serialization. Rules:
     1. JSON, sort_keys=True, separators=(',', ':'), ensure_ascii=False, UTF-8.
     2. Only 'hash' is excluded from the hashed payload (a record cannot
        hash a field containing its own hash). 'prev' IS included — it is
        known before the hash is computed, and excluding it would allow
        insertion and deletion without detection.
     3. Floats are forbidden in hashed records. Numeric thresholds are stored
        as decimal strings ('0.05', not 0.05). Rationale: float repr varies
        across platforms and languages, and an independent reimplementation of
        the verifier must produce identical bytes. This also removes an entire
        class of 0.0495 ≈ 0.05 ambiguity at the storage layer.
     4. Timestamps are RFC3339 UTC with Z, second precision.
     5. null is omitted, not serialized. Absent and null are the same thing.
        Exception: nulls inside lists are preserved — list position is
        semantically meaningful and removing an element changes the list."
"""

import json


# Only 'hash' is excluded — it cannot be part of the payload it is computed
# from. 'prev' IS included: it is set before the hash is computed, and
# including it makes the hash commit to the record's position in the chain.
# Failure catalog #27: excluding prev gave content integrity without history
# integrity — insertion and deletion passed verification.
_EXCLUDED_FIELDS = frozenset({"hash"})


def decimal_str(value):
    """Convert a float to the canonical decimal string used in hashed records.

    Rule 3 forbids floats in hashed records, so any computed real-valued
    quantity (p-values, thresholds, effect sizes) is stored as a string.
    Hand-written schema values are already written that way ("0.05"); this
    is the same convention for values the code computes.

    Format is '%.17g': the shortest fixed precision that round-trips every
    IEEE-754 double exactly, and correctly-rounded decimal conversion is
    specified identically across platforms and languages. That matters most
    exactly where it looks like overkill — a p-value a few ulps either side
    of a Benjamini-Hochberg threshold decides whether a hypothesis survives.

    Args:
        value: A float, int, or an already-formatted decimal string.

    Returns:
        str: canonical decimal representation.

    Raises:
        TypeError: If value is not a real number or string.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        raise TypeError("decimal_str does not accept bool; got {!r}".format(value))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return "%.17g" % value
    raise TypeError(
        "decimal_str expects float, int, or str; got {}".format(type(value).__name__)
    )


def canonical_bytes(record):
    """Return the canonical byte representation of a record.

    The record is a dict. The canonical form is JSON with:
    - Keys sorted recursively
    - Compact separators (',', ':')
    - ensure_ascii=False, encoded as UTF-8
    - 'hash' field stripped (only — prev is included)
    - null/None values stripped recursively (except inside lists)
    - Floats forbidden (raises TypeError)

    Returns bytes.
    """
    cleaned = _strip_and_validate(record, top_level=True)
    return json.dumps(
        cleaned,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _strip_and_validate(obj, top_level=False):
    """Recursively strip nulls and reject floats. Strip hash at top level only.

    Returns a cleaned copy of the object suitable for canonical serialization.
    """
    if isinstance(obj, dict):
        result = {}
        for key, value in obj.items():
            # Strip excluded fields only at the top level
            if top_level and key in _EXCLUDED_FIELDS:
                continue
            # Strip null values
            if value is None:
                continue
            result[key] = _strip_and_validate(value)
        return result
    elif isinstance(obj, list):
        return [_strip_and_validate(item) for item in obj]
    elif isinstance(obj, float):
        raise TypeError(
            "Floats are forbidden in hashed records. "
            "Use decimal strings instead (e.g. '0.05', not 0.05). "
            "Got: {!r}".format(obj)
        )
    elif isinstance(obj, bool):
        # bool is a subclass of int in Python, so this must come before int check
        return obj
    elif isinstance(obj, int):
        return obj
    elif isinstance(obj, str):
        return obj
    else:
        # Pass through other JSON-serializable types
        return obj
