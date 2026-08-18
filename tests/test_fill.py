# tests/test_fill.py — A3's pending-field fill
"""
Phase 2c §4 — hand-computed tests, written before implementation.

P-1  Pending field filled from fill_rule; re-fill refused.
P-2  `blocks: 3` resolves to span_years = 0.5, NOT 3.
P-3  An unknown fill rule refuses. No fallback, no partial fill.
P-4  A rule naming a function outside the whitelist refuses.
P-5  power < 0.5 sets `flagged` and does not block.

P-2 is the one that matters most. A power number is plausible at any
magnitude, so reading `blocks: 3` as three YEARS instead of three two-month
blocks produces no symptom at all — just a filled field that looks fine and is
wrong by sqrt(6) in the standard error. Nothing but this test stands between a
wrong constant and a credible-looking record.
"""

import pytest

from proofhouse.core.amend import check_amendment_ordering
from proofhouse.core.fill import (
    FillLog,
    FillRefused,
    blocks_to_span_years,
    fill_pending_field,
    parse_fill_rule,
    resolve_fill_rule,
)
from proofhouse.eval.power import compute_power_check


def _tmp(name):
    import os
    import tempfile
    return os.path.join(tempfile.mkdtemp(), name)


def _entry(pending=True, rule="power(effect_size, blocks, prior_trials)",
           inputs=None):
    return {
        "id": "H-2026-AC10",
        "hash": "h0",
        "power_check": {
            "inputs": inputs if inputs is not None else {
                "effect_size": "0.5", "blocks": 3, "prior_trials": 14,
            },
            "value": {"pending": pending, "fill_rule": rule},
        },
    }


class TestP2TheUnitConversion:
    """P-2: blocks are two months. This is the error with no symptom."""

    def test_three_blocks_is_half_a_year(self):
        assert blocks_to_span_years(3) == 0.5

    def test_six_blocks_is_one_year(self):
        assert blocks_to_span_years(6) == 1.0

    def test_resolved_power_matches_the_half_year_computation(self):
        """The value must equal compute_power_check(0.5, 0.5, 14) — the
        half-year reading — and NOT the three-year one."""
        resolved = resolve_fill_rule(
            "power(effect_size, blocks, prior_trials)",
            {"effect_size": "0.5", "blocks": 3, "prior_trials": 14},
        )
        correct = compute_power_check(0.5, 0.5, 14)
        wrong = compute_power_check(0.5, 3.0, 14)

        assert resolved["value"] == correct["power"]
        assert resolved["value"] != wrong["power"]

    def test_span_years_is_recorded_so_the_conversion_is_auditable(self):
        """The derivation exposes the step where the unit error would hide."""
        resolved = resolve_fill_rule(
            "power(effect_size, blocks, prior_trials)",
            {"effect_size": "0.5", "blocks": 3, "prior_trials": 14},
        )
        assert resolved["derivation"]["span_years"] == "0.5"


class TestP1FillAndRefill:
    """P-1: fill once, never again."""

    def test_fill_populates_the_field(self):
        filled, resolved = fill_pending_field(_entry(), "power_check")
        value = filled["power_check"]["value"]

        assert value["pending"] is False
        assert value["filled"] == "%.17g" % resolved["value"]
        assert value["fill_rule"] == "power(effect_size, blocks, prior_trials)"

    def test_refill_is_refused(self):
        filled, _ = fill_pending_field(_entry(), "power_check")
        with pytest.raises(FillRefused) as exc:
            fill_pending_field(filled, "power_check")
        assert "cannot be re-filled" in str(exc.value)

    def test_fill_does_not_mutate_the_caller_s_entry(self):
        """A refusal partway through must not leave a half-filled record."""
        entry = _entry()
        fill_pending_field(entry, "power_check")
        assert entry["power_check"]["value"]["pending"] is True

    def test_missing_field_refuses(self):
        with pytest.raises(FillRefused):
            fill_pending_field({"id": "H-1"}, "power_check")

    def test_already_false_pending_refuses(self):
        with pytest.raises(FillRefused):
            fill_pending_field(_entry(pending=False), "power_check")

    def test_filled_value_is_a_canonical_decimal_string(self):
        """The entry is hash-chained; canonical rule 3 forbids floats."""
        filled, _ = fill_pending_field(_entry(), "power_check")
        assert isinstance(filled["power_check"]["value"]["filled"], str)


class TestP3P4RuleWhitelist:
    """P-3, P-4: a rule is a named recipe, not arithmetic."""

    def test_unknown_rule_refuses(self):
        with pytest.raises(FillRefused) as exc:
            resolve_fill_rule("bogus(effect_size)", {"effect_size": "0.5"})
        assert "Unknown fill rule" in str(exc.value)

    @pytest.mark.parametrize("rule", [
        "__import__('os').system('x')",
        "power(effect_size) + 1",
        "power(power(effect_size, blocks, prior_trials))",
        "0.5 * power(a, b, c)",
        "power",
        "power(",
        "",
    ])
    def test_malformed_or_injected_rules_refuse(self, rule):
        with pytest.raises(FillRefused):
            resolve_fill_rule(rule, {"effect_size": "0.5"})

    def test_argument_names_are_checked_not_just_arity(self):
        """A rule whose arguments were silently reordered would still produce
        a plausible number, which is the whole danger."""
        with pytest.raises(FillRefused) as exc:
            resolve_fill_rule(
                "power(blocks, effect_size, prior_trials)",
                {"effect_size": "0.5", "blocks": 3, "prior_trials": 14},
            )
        assert "in that order" in str(exc.value)

    def test_parse_splits_name_and_args(self):
        assert parse_fill_rule("power(a, b, c)") == ("power", ["a", "b", "c"])

    def test_missing_input_refuses_rather_than_defaulting(self):
        with pytest.raises(FillRefused):
            resolve_fill_rule(
                "power(effect_size, blocks, prior_trials)",
                {"effect_size": "0.5", "blocks": 3},
            )


class TestP5FlaggingDoesNotBlock:
    """P-5: low power is something to think about, not something to forbid."""

    def test_low_power_sets_flagged_and_still_fills(self):
        entry = _entry(inputs={
            "effect_size": "0.1", "blocks": 1, "prior_trials": 200,
        })
        filled, resolved = fill_pending_field(entry, "power_check")

        assert resolved["flagged"] is True
        assert filled["power_check"]["value"]["flagged"] is True
        assert filled["power_check"]["value"]["pending"] is False

    def test_high_power_is_not_flagged(self):
        # Sharpe 2.0 over 24 blocks (4 years) against a single prior trial:
        # power 0.7467. Note how much it takes — 12 blocks at the same effect
        # lands at 0.4951 and IS flagged, which is the honest shape of the
        # thing rather than a comfortable one.
        entry = _entry(inputs={
            "effect_size": "2.0", "blocks": 24, "prior_trials": 1,
        })
        _, resolved = fill_pending_field(entry, "power_check")
        assert resolved["flagged"] is False

    def test_the_agentcopy_fixtures_own_inputs_are_flagged(self):
        """The registered inputs are effect_size 0.5, 3 blocks, 14 prior
        trials — power 0.0010. As registered, that study had roughly a one-in-
        a-thousand chance of detecting its own hypothesized effect. This is
        the pending field earning its keep on a real case rather than a
        synthetic one."""
        _, resolved = fill_pending_field(_entry(), "power_check")
        assert resolved["flagged"] is True
        assert resolved["value"] < 0.01


class TestFillLog:
    def test_fill_is_chained_and_verifies(self):
        path = _tmp("fills.jsonl")
        log = FillLog(path)
        _, resolved = fill_pending_field(_entry(), "power_check")
        log.record(
            entry_id="H-2026-AC10", field="power_check",
            rule="power(effect_size, blocks, prior_trials)",
            inputs={"effect_size": "0.5", "blocks": 3, "prior_trials": 14},
            resolved=resolved,
            entry_hash_before="h0", entry_hash_after="h1",
            ledger_anchor="R-000003",
        )
        reloaded = FillLog(path)
        assert reloaded.verify()[0]
        row = reloaded.for_entry("H-2026-AC10")[0]
        assert row["entry_hash_after"] == "h1"
        assert row["derivation"]["span_years"] == "0.5"

    def test_tampering_with_a_filled_value_is_detected(self):
        path = _tmp("fills.jsonl")
        log = FillLog(path)
        _, resolved = fill_pending_field(_entry(), "power_check")
        log.record("H-1", "power_check", "power(effect_size, blocks, "
                   "prior_trials)", {}, resolved, "h0", "h1")

        with open(path) as fh:
            content = fh.read()
        with open(path, "w") as fh:
            fh.write(content.replace('"flagged":true', '"flagged":false')
                     .replace('"flagged": true', '"flagged": false'))

        assert FillLog(path).verify()[0] is False


class TestFillsCountAsVersionEvents:
    """§7.2's consequence: ordering must consult fills, or every post-fill
    gate run is flagged as having run ahead of its paperwork."""

    def _gate_row(self, run_id, entry_hash):
        return {
            "run_id": run_id, "kind": "gate", "registry_ref": "H-1",
            "blocks_touched": ["B-V-01"], "entry_hash": entry_hash,
        }

    def test_post_fill_gate_run_is_clean_when_fills_are_supplied(self):
        findings = check_amendment_ordering(
            entry_id="H-1", original_hash="h0", amendments=[],
            ledger_rows=[self._gate_row("R-000002", "h1")],
            fills=[{"fill_id": "F-0001", "entry_id": "H-1",
                    "ledger_anchor": "R-000001", "entry_hash_after": "h1"}],
        )
        assert findings == []

    def test_the_same_run_is_flagged_when_fills_are_omitted(self):
        """Proves the parameter is load-bearing rather than decorative — this
        is the false positive §7.2 warned about."""
        findings = check_amendment_ordering(
            entry_id="H-1", original_hash="h0", amendments=[],
            ledger_rows=[self._gate_row("R-000002", "h1")],
        )
        assert len(findings) == 1

    def test_a_gate_run_before_its_fill_is_still_flagged(self):
        findings = check_amendment_ordering(
            entry_id="H-1", original_hash="h0", amendments=[],
            ledger_rows=[self._gate_row("R-000001", "h1")],
            fills=[{"fill_id": "F-0001", "entry_id": "H-1",
                    "ledger_anchor": "R-000005", "entry_hash_after": "h1"}],
        )
        assert len(findings) == 1
        assert "fill F-0001" in findings[0]["reason"]

    def test_fills_and_amendments_interleave_by_anchor(self):
        findings = check_amendment_ordering(
            entry_id="H-1", original_hash="h0",
            amendments=[{"amendment_id": "A-0001", "entry_id": "H-1",
                         "number": 1, "ledger_anchor": "R-000003",
                         "entry_hash_after": "h2"}],
            ledger_rows=[
                self._gate_row("R-000002", "h1"),   # after fill, before amend
                self._gate_row("R-000004", "h2"),   # after both
            ],
            fills=[{"fill_id": "F-0001", "entry_id": "H-1",
                    "ledger_anchor": "R-000001", "entry_hash_after": "h1"}],
        )
        assert findings == []

    def test_a_fill_does_not_trip_the_rerun_lock(self):
        """A fill must not invalidate the validation window — that would
        punish the mechanism A3 built precisely to avoid amendments."""
        from proofhouse.core.amend import check_rerun_lock
        check_rerun_lock(
            entry_id="H-1",
            requested_blocks=["B-V-01"],
            amendments=[],
            ledger_rows=[self._gate_row("R-000001", "h1")],
        )
