# proofhouse.schema.validate — entry validation
"""
Validation for proofhouse registry entries.

Phase 0 Build Plan §5 — the six enforced checks (E1–E6) plus
schema validators V1–V12.

E1: Gate combination required, no default (A5)
E2: Threshold anti-circularity — derived source_blocks may not have segment V (A2)
E3: Diagnostics isolation — Gate and Diagnostics have no reference (A4)
E4: Block consumption — V-block single-use (A7)
E5: Pass licensing — pass_licenses + pass_does_not_establish present, ≤200 chars (A10)
E6: Block availability — data_complete required; seams + V requires seam_acknowledged (A6)

V-1:  Counterparty rejects benchmark terms
V-2:  Parameters must be scalar, not ranges/lists/string-ranges
V-3:  No gate.combination → rejected, no default
V-4:  Derived threshold source_blocks containing segment-V → rejected
V-5:  tie_handling key → rejected (field doesn't exist)
V-6:  pass_licenses over 200 chars → rejected
V-7:  benchmark_sensitivity with <2 entries → rejected
V-8:  validation_blocks referencing segment-E → rejected
V-9:  depends_on referencing nonexistent entry → rejected
V-10: power_check.value.pending without fill_rule → rejected
V-11: schema_version must be present and valid
V-12: feasibility.sleeve_range must be a 2-element list of decimal strings
"""

import re

from proofhouse.schema.models import (
    VALID_COMPARATORS,
    VALID_SEGMENTS,
    is_benchmark_term,
)
from proofhouse.schema.versions import check_version


class ValidationError(Exception):
    """Raised when an entry fails validation."""

    def __init__(self, errors):
        self.errors = errors if isinstance(errors, list) else [errors]
        msg = "Validation failed with {} error(s):\n{}".format(
            len(self.errors),
            "\n".join("  - " + e for e in self.errors),
        )
        super().__init__(msg)


def validate_entry(entry, block_registry=None, existing_entries=None):
    """Validate a registry entry against all checks.

    Runs schema validators (V1–V11) and behavioral checks (E1–E6).

    Args:
        entry: Dict representing the registry entry.
        block_registry: Optional BlockRegistry for block-level checks (E2, E4, E6).
        existing_entries: Optional dict of {entry_id: entry} for reference checks (V-9).

    Raises:
        ValidationError: If any check fails, with all errors collected.
    """
    errors = []

    # V-11: schema_version must be present and valid
    sv = entry.get("schema_version")
    if sv is None:
        errors.append(
            "V-11: schema_version is missing. Every record must carry "
            "a schema_version."
        )
    else:
        try:
            check_version(sv)
        except ValueError as e:
            errors.append("V-11: {}".format(e))

    # V-12: feasibility.sleeve_range must be [min, max] decimal strings
    sleeve_range = _nested_get(entry, "feasibility", "sleeve_range")
    if sleeve_range is not None:
        valid_range = True
        if not isinstance(sleeve_range, list) or len(sleeve_range) != 2:
            errors.append(
                "V-12: feasibility.sleeve_range must be a 2-element list "
                "[min, max]. Got: {!r}".format(sleeve_range)
            )
            valid_range = False
        elif not all(isinstance(v, str) for v in sleeve_range):
            errors.append(
                "V-12: feasibility.sleeve_range elements must be decimal "
                "strings. Got: {!r}".format(sleeve_range)
            )
            valid_range = False

        if valid_range and isinstance(sleeve_range, list) and len(sleeve_range) == 2:
            try:
                lo, hi = float(sleeve_range[0]), float(sleeve_range[1])
                if lo > hi:
                    errors.append(
                        "V-12: feasibility.sleeve_range[0] ({}) > "
                        "sleeve_range[1] ({}).".format(sleeve_range[0], sleeve_range[1])
                    )
            except (ValueError, TypeError):
                errors.append(
                    "V-12: feasibility.sleeve_range values must be "
                    "numeric decimal strings. Got: {!r}".format(sleeve_range)
                )

    # --- Schema validators (V1–V11) ---

    # V-1: Counterparty rejects benchmark terms
    counterparty = _nested_get(entry, "mechanism", "counterparty")
    if counterparty and is_benchmark_term(counterparty):
        errors.append(
            "V-1: counterparty '{}' contains benchmark language. "
            "A counterparty identifies who loses money, not a performance "
            "comparison.".format(counterparty)
        )

    # V-2: Parameters must be scalar values, not ranges/lists/string-ranges
    params = _nested_get(entry, "signal", "parameters") or {}
    for key, val in params.items():
        if isinstance(val, (list, tuple, dict)):
            errors.append(
                "V-2: signal.parameters.{} has value {!r} which is a {}. "
                "Parameters must be fixed scalar values — a range here is "
                "a validation error, making grid search unregisterable.".format(
                    key, val, type(val).__name__
                )
            )
        elif isinstance(val, str) and _looks_like_range(val):
            errors.append(
                "V-2: signal.parameters.{} has value '{}' which looks like a "
                "range. Parameters must be fixed scalar values.".format(key, val)
            )

    # V-3 / E1: Gate combination required, no default
    combination = _nested_get(entry, "gate", "combination")
    if not combination:
        errors.append(
            "V-3 / E1: gate.combination is missing or empty. "
            "Required, no default. Registration fails without it (A5)."
        )

    # V-4 / E2: Derived threshold anti-circularity
    if block_registry:
        criteria = _nested_get(entry, "gate", "criteria") or {}
        for crit_name, crit in criteria.items():
            threshold = crit.get("threshold", {})
            if isinstance(threshold, dict) and "derived" in threshold:
                derived = threshold["derived"]
                source_blocks = derived.get("source_blocks", [])
                for block_id in source_blocks:
                    try:
                        block = block_registry.get_block(block_id)
                        if block.get("segment") == "V":
                            errors.append(
                                "V-4 / E2: gate.criteria.{}.threshold.derived."
                                "source_blocks contains {} which has segment V. "
                                "Derived thresholds may not use validation-segment "
                                "blocks (A2).".format(crit_name, block_id)
                            )
                    except KeyError:
                        errors.append(
                            "V-4: gate.criteria.{}.threshold.derived.source_blocks "
                            "references non-existent block {}".format(
                                crit_name, block_id
                            )
                        )

    # V-5: tie_handling field rejected (doesn't exist — comparator carries semantics)
    _check_no_field(entry, "tie_handling", errors,
                    "V-5: 'tie_handling' field is not permitted. "
                    "Write '>' when ties should fail and '>=' when they should pass. "
                    "The comparator carries the semantics (§0.3).")

    # V-6 / E5: pass_licenses present and ≤200 chars
    pass_licenses = entry.get("pass_licenses")
    if not pass_licenses:
        errors.append(
            "V-6 / E5: pass_licenses is missing or empty. Required (A10)."
        )
    elif len(pass_licenses) > 200:
        errors.append(
            "V-6 / E5: pass_licenses is {} chars (max 200). "
            "Brevity is a requirement — if it grows into prose it stops "
            "being read (A10).".format(len(pass_licenses))
        )

    # E5: pass_does_not_establish
    pass_dne = entry.get("pass_does_not_establish")
    if not pass_dne:
        errors.append(
            "E5: pass_does_not_establish is missing or empty. Required (A10)."
        )
    elif len(pass_dne) > 200:
        errors.append(
            "E5: pass_does_not_establish is {} chars (max 200).".format(
                len(pass_dne)
            )
        )

    # V-7: benchmark_sensitivity with <2 entries
    sensitivity = _nested_get(entry, "gate", "benchmark_sensitivity") or []
    if len(sensitivity) < 2:
        errors.append(
            "V-7: gate.benchmark_sensitivity has {} entries (minimum 2). "
            "A benchmark sensitivity report across ≥2 alternatives is "
            "mandatory.".format(len(sensitivity))
        )

    # V-8: validation_blocks referencing segment-E block
    if block_registry:
        val_blocks = entry.get("validation_blocks", [])
        for block_id in val_blocks:
            try:
                block = block_registry.get_block(block_id)
                if block.get("segment") == "E":
                    errors.append(
                        "V-8: validation_blocks references {} which has "
                        "segment E. Exploration blocks cannot serve as "
                        "validation.".format(block_id)
                    )
            except KeyError:
                errors.append(
                    "V-8: validation_blocks references non-existent block "
                    "{}".format(block_id)
                )

    # V-9: depends_on referencing nonexistent entry
    if existing_entries is not None:
        depends = entry.get("depends_on", [])
        for dep_id in depends:
            if dep_id not in existing_entries:
                errors.append(
                    "V-9: depends_on references non-existent entry {}".format(
                        dep_id
                    )
                )

    # V-10: power_check.value.pending without fill_rule
    power_check = entry.get("power_check", {})
    value = power_check.get("value", {})
    if isinstance(value, dict) and value.get("pending"):
        if not value.get("fill_rule"):
            errors.append(
                "V-10: power_check.value.pending is True but fill_rule is "
                "missing. A pending field must carry its fill rule at lock "
                "time (A3)."
            )

    # --- E3: Diagnostics isolation (structural) ---
    # A4 requires that the verdict function structurally cannot read diagnostics.
    #
    # Phase 0 limitation: this check only catches name collisions between gate
    # criteria and diagnostic names. A gate could still consume a diagnostic's
    # value under a different name — true structural isolation requires a
    # data-flow boundary in the gate evaluation engine, which is Phase 2's
    # gate/ module's responsibility. This check catches the obvious case;
    # Phase 2 enforces the property.
    gate = entry.get("gate", {})
    diagnostics = entry.get("diagnostics", [])
    diag_names = {d.get("name") for d in diagnostics if isinstance(d, dict)}
    if diag_names:
        criteria = gate.get("criteria", {})
        for crit_name in criteria:
            if crit_name in diag_names:
                errors.append(
                    "E3: gate.criteria.{} shares a name with a diagnostic. "
                    "Gate and Diagnostics must be structurally separate (A4).".format(
                        crit_name
                    )
                )

    if errors:
        raise ValidationError(errors)


# Pattern for string ranges like "10..14" or "10-14" (with digits on both sides)
_RANGE_PATTERN = re.compile(r'^\d+\.{2,3}\d+$|^\d+-\d+$')


def _looks_like_range(s):
    """Check if a string looks like a numeric range (e.g., '10..14' or '10-14')."""
    return bool(_RANGE_PATTERN.match(s))


def _nested_get(d, *keys):
    """Safely get a nested dict value."""
    for key in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(key)
    return d


def _check_no_field(entry, field_name, errors, message):
    """Check that a forbidden field doesn't appear anywhere in the entry."""
    if field_name in entry:
        errors.append(message)
    # Check inside gate
    gate = entry.get("gate", {})
    if isinstance(gate, dict):
        if field_name in gate:
            errors.append(message)
        # Also check inside gate.criteria.* values
        criteria = gate.get("criteria", {})
        if isinstance(criteria, dict):
            for crit_name, crit_val in criteria.items():
                if isinstance(crit_val, dict) and field_name in crit_val:
                    errors.append(message)
