# proofhouse.schema.validate — entry validation
"""
Validation for proofhouse registry entries.

Phase 0 Build Plan §5 — the six enforced checks (E1–E6) plus
schema validators V1–V14.

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
V-13: p_value_sidedness required, no default (one-sided | two-sided).
      Defines schema version 2; a record carrying a valid earlier version
      is not asked for it (schema/versions.py)
V-14: fdr_method required, no default (bh | by), with a stated
      dependence justification. Schema version 2, as V-13
V-15: literal gate thresholds must be finite (-inf passes unconditionally,
      inf/nan fail unconditionally, 1e400 overflows silently)
V-16: power_events requires a declared clustering unit — the icc is
      meaningless without knowing what it correlates within
V-17: the feasibility block must be internally executable — a rebalance
      band smaller than the smallest possible trade is not a design
"""

import math
import re

from proofhouse.schema.models import (
    VALID_COMPARATORS,
    VALID_SEGMENTS,
    VALID_FDR_METHODS,
    VALID_SIDEDNESS,
    is_benchmark_term,
)
from proofhouse.schema.versions import (
    SIDEDNESS_AND_FDR_SINCE,
    check_version,
    is_valid_version,
)


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

    Runs schema validators (V1–V14) and behavioral checks (E1–E6).

    Args:
        entry: Dict representing the registry entry.
        block_registry: Optional BlockRegistry for block-level checks (E2, E4, E6).
        existing_entries: Optional dict of {entry_id: entry} for reference checks (V-9).

    Raises:
        ValidationError: If any check fails, with all errors collected.
    """
    errors = []

    # --- Schema validators (V1–V14) ---

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

    # Which rules the record is held to. V-13 and V-14 define schema
    # version 2: a record carrying a valid version from before them was
    # registered when the fields did not exist, and the choices they record
    # cannot be made for it after the fact. A missing or invalid version
    # earns no exemption — V-11 has already refused it, and the strict
    # reading costs nothing. Every other check applies to every version.
    predates_sidedness_and_fdr = (
        is_valid_version(sv) and sv < SIDEDNESS_AND_FDR_SINCE
    )

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

    # V-13: p_value_sidedness required, no default.
    #
    # The choice halves or doubles every p-value, which directly decides
    # what survives an FDR batch — the same class of load-bearing choice
    # as gate.combination (A5), and it gets the same treatment: required,
    # no default, registration fails without it.
    #
    # One-sided is the appropriate choice for this harness because the
    # direction is genuinely fixed before the data is seen: polarity is a
    # registered parameter, the artifact reads parameters from the entry
    # rather than embedding constants, the artifact hash gates execution,
    # and changing polarity post-registration invalidates the validation
    # window. That is the guarantee published research usually lacks —
    # Harvey/Liu/Zhu use two-sided precisely because factor signs there are
    # chosen post hoc, while White's Reality Check, Hansen's SPA and the
    # Deflated Sharpe Ratio are all one-sided by construction.
    #
    # Two-sided remains available for mechanisms that genuinely do not
    # imply a sign.
    #
    # Schema version 2 and later. A v1 record predates the field; see
    # `predates_sidedness_and_fdr` above.
    sidedness = entry.get("p_value_sidedness")
    if predates_sidedness_and_fdr:
        pass
    elif not sidedness:
        errors.append(
            "V-13: p_value_sidedness is missing. Required, no default "
            "(one-sided | two-sided). The choice halves or doubles every "
            "p-value and therefore decides FDR survivors."
        )
    elif sidedness not in VALID_SIDEDNESS:
        errors.append(
            "V-13: p_value_sidedness must be one of {}; got {!r}".format(
                sorted(VALID_SIDEDNESS), sidedness
            )
        )

    # V-14: fdr_method required, no default; dependence assumption justified.
    #
    # Same A5 pattern as gate.combination (V-3) and p_value_sidedness (V-13).
    # BH and BY control FDR under different dependence assumptions and give
    # materially different answers — on the published BH (1995) example, BH
    # rejects 4 and BY rejects 3. A silent default would pick the dependence
    # assumption on the researcher's behalf.
    #
    # The justification is required because writing "hypotheses are
    # independent" is what forces you to notice when they are not.
    #
    # Schema version 2 and later, as V-13.
    fdr = entry.get("fdr_method")
    if predates_sidedness_and_fdr:
        pass
    elif not fdr:
        errors.append(
            "V-14: fdr_method is missing. Required, no default (bh | by). "
            "BH assumes independence or positive regression dependence; BY "
            "holds under arbitrary dependence at the cost of deflating q by "
            "H(m)."
        )
    elif fdr not in VALID_FDR_METHODS:
        errors.append(
            "V-14: fdr_method must be one of {}; got {!r}".format(
                sorted(VALID_FDR_METHODS), fdr
            )
        )
    elif not entry.get("fdr_method_justification", "").strip():
        errors.append(
            "V-14: fdr_method_justification is required — state the "
            "dependence assumption. Hypotheses screened on a shared "
            "universe are dependent, which is where 'bh' stops being safe."
        )

    # V-15: literal thresholds must be finite numbers.
    #
    # Added by the Phase 2 audit. A non-finite threshold does not fail loudly,
    # it fails invisibly: "-inf" passes unconditionally, "inf" and "nan" fail
    # unconditionally, and "1e400" silently overflows to inf. All four produce
    # a record that reads as a real verdict.
    #
    # core/gate.py refuses these at evaluation time and is the authoritative
    # check — the same predicate is enforced here only so the problem surfaces
    # at registration rather than after a validation block has been spent.
    # These are not two rules that could drift; they are one predicate checked
    # at two moments, and the later one governs.
    for crit_name, crit in sorted((_nested_get(entry, "gate", "criteria")
                                   or {}).items()):
        if not isinstance(crit, dict):
            continue
        threshold = crit.get("threshold")
        if not isinstance(threshold, dict) or "literal" not in threshold:
            continue
        raw = threshold["literal"]
        try:
            parsed = float(raw)
        except (TypeError, ValueError):
            errors.append(
                "V-15: gate.criteria.{}.threshold.literal is not a number: "
                "{!r}".format(crit_name, raw)
            )
            continue
        if math.isnan(parsed) or math.isinf(parsed):
            errors.append(
                "V-15: gate.criteria.{}.threshold.literal is {!r}, which "
                "resolves to {}. A threshold of -inf passes unconditionally "
                "and inf/nan fail unconditionally — a gate no observation can "
                "move is not a gate. Register a finite threshold.".format(
                    crit_name, raw, parsed)
            )

    # V-16: power_events must declare what its icc correlates WITHIN.
    #
    # An intra-cluster correlation is a number about a grouping, and the
    # number is uninterpretable without the grouping. icc=0.067 within a
    # trading day and icc=0.067 within an instrument are different claims
    # about the world, and they license different designs.
    #
    # Declared as an input rather than inferred from prose. H-2026-PFR1's unit
    # is currently recoverable only by reading a diagnostic's name
    # (`intra_date_icc_realized`) and a statistic's (`clustered_t_stat`) — an
    # inference, and one that gets harder to make as the record ages, not
    # easier. A validator is what turns it into a claim someone filed.
    power_block = entry.get("power_check") or {}
    power_value = power_block.get("value") or {}
    fill_rule = power_value.get("fill_rule") or ""
    # Exact name, not a substring: `power_events_v2(...)` would match `in`
    # and get checked under a rule it does not use. Parsed inline rather than
    # with core.fill.parse_fill_rule, because schema/ never imports from
    # core/ — enforced by test S-1.
    rule_name = str(fill_rule).split("(")[0].strip()
    if rule_name == "power_events":
        unit = (power_block.get("inputs") or {}).get("clustering_unit")
        if not isinstance(unit, str) or not unit.strip():
            errors.append(
                "V-16: power_check.inputs.clustering_unit is required when "
                "the fill rule is power_events. An icc without a declared "
                "clustering unit is not a number a reader can check — 0.067 "
                "within a day and 0.067 within an instrument are different "
                "claims. State the unit (e.g. 'trading date')."
            )

    # V-17: the feasibility block must describe something executable.
    #
    # The schema collected min_order_notional_usd, sleeve_range,
    # instruments_held and rebalance_band — every input eval/costs needs —
    # and nothing ran the check. An entry could register declaring a 1%
    # rebalance band while its smallest possible trade was 12.5% of a
    # position. Parent plan §2: "a strategy whose edge is real but smaller
    # than the quantization drag at the intended sleeve size — feasibility,
    # not statistics."
    #
    # REFUSED ONLY WHEN NO DECLARED SIZE WORKS. sleeve_range is a range, and
    # a design that is infeasible at $2,000 and fine at $20,000 is not
    # broken — it has a minimum size. Refusing the whole range would reject
    # legitimate designs, and a check that refuses legitimate work gets
    # routed around.
    feasibility = entry.get("feasibility") or {}
    sleeve = feasibility.get("sleeve_range")
    try:
        top_sleeve = float(sleeve[1])
        held = int(feasibility["instruments_held"])
        min_order = float(feasibility["min_order_notional_usd"])
        band = float(feasibility["rebalance_band"])
    except (KeyError, IndexError, TypeError, ValueError):
        # Shape problems are V-12's and the required-field checks'; this
        # validator only speaks to coherence between well-formed numbers.
        pass
    else:
        if held > 0 and top_sleeve > 0:
            position = top_sleeve / held
            implied = min_order / position if position else float("inf")
            if band < implied:
                errors.append(
                    "V-17: feasibility is not executable at ANY declared "
                    "sleeve size. At the top of sleeve_range ({:g}) across {} "
                    "instruments, each position is {:g} USD, so the smallest "
                    "possible order ({:g} USD) is {:.1%} of a position — but "
                    "rebalance_band is {:.1%}. The band cannot be smaller "
                    "than one tradeable increment.".format(
                        top_sleeve, held, position, min_order, implied, band)
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
