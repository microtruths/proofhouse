# tests/test_sidedness_and_polarity.py — V-13 + polarity amendment enforcement
"""One-sided testing and the constraint that makes it legitimate.

Two changes under test:

  V-13  p_value_sidedness is required with no default. The choice halves
        or doubles every p-value and therefore decides FDR survivors —
        the same class of load-bearing choice as gate.combination (A5).

  Polarity amendment enforcement. One-sided p-values are only defensible
  because the direction was fixed before the data was seen. If polarity
  could be flipped after registration, every one-sided test would
  silently be a two-sided test at half the stated p-value.

  Enforcement, not annotation: an entry that changes polarity (or
  expected_effect_size) relative to its parent must validate on blocks
  the parent did not use.
"""

import os
import shutil
import tempfile
import unittest


def _entry(entry_id="H-2026-001", **overrides):
    e = {
        "schema_version": 1,
        "id": entry_id,
        "depends_on": [],
        "mechanism": {
            "class": "funding-basis",
            "counterparty": "Leveraged longs paying funding",
            "claim": "Funding rate predicts returns",
            "expected_effect_size": "1.2",
            "expected_decay": "6 months",
        },
        "signal": {
            "inputs": ["funding_rate"],
            "transform": "z-score",
            "frequency": "8h",
            "parameters": {"k": 12, "polarity": 1},
        },
        "universe": {
            "id": "hl-perps-top20",
            "venues": ["hyperliquid"],
            "instruments": "top 20",
        },
        "feasibility": {
            "min_order_notional_usd": "10",
            "sleeve_range": ["100", "20000"],
            "instruments_held": 8,
            "rebalance_band": "0.15",
        },
        "costs": {"model_ref": "costs/hl-taker-v1"},
        "gate": {
            "statistic": "dsr",
            "combination": "g1",
            "criteria": {
                "g1": {"comparator": ">", "threshold": {"literal": "0"}},
            },
            "benchmark": "btc-subtraction",
            "benchmark_justification": "Standard benchmark for crypto alpha",
            "benchmark_sensitivity": ["market-index", "eth-subtraction"],
            "on_pass": "promote-to-live",
            "on_fail": "retire",
        },
        "p_value_sidedness": "one-sided",
        "fdr_method": "by",
        "fdr_method_justification": "Hypotheses share the hl-perps universe and are therefore dependent.",
        "pass_licenses": "A shadow trial on the live sleeve for 6 weeks.",
        "pass_does_not_establish": "Deployable alpha.",
        "live_gate": {"min_duration_weeks": 6},
        "validation_blocks": [],
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": "1.2"},
            "value": {"pending": True, "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }
    e.update(overrides)
    return e


class TestV13Sidedness(unittest.TestCase):

    def _validate(self, entry):
        from proofhouse.schema.validate import validate_entry

        validate_entry(entry)

    def test_one_sided_accepted(self):
        self._validate(_entry())

    def test_two_sided_accepted(self):
        """Two-sided remains available for mechanisms that imply no sign."""
        self._validate(_entry(p_value_sidedness="two-sided"))

    def test_missing_rejected_no_default(self):
        """No default. A silent default here would decide FDR survivors."""
        from proofhouse.schema.validate import ValidationError

        e = _entry()
        del e["p_value_sidedness"]
        with self.assertRaises(ValidationError) as ctx:
            self._validate(e)
        self.assertIn("V-13", str(ctx.exception))

    def test_empty_rejected(self):
        from proofhouse.schema.validate import ValidationError

        with self.assertRaises(ValidationError):
            self._validate(_entry(p_value_sidedness=""))

    def test_invalid_value_rejected(self):
        from proofhouse.schema.validate import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            self._validate(_entry(p_value_sidedness="left-tailed"))
        self.assertIn("V-13", str(ctx.exception))


class TestPolarityAmendment(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.dir = os.path.join(self.tmp, "registry")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _registry(self):
        from proofhouse.core.registry import Registry

        return Registry(self.dir)

    # --- the check function itself ---

    def test_check_detects_flip(self):
        from proofhouse.core.screen import check_polarity_amendment

        orig = _entry()
        amended = _entry()
        amended["signal"]["parameters"]["polarity"] = -1

        r = check_polarity_amendment(amended, orig)
        self.assertTrue(r["amended"])
        self.assertEqual(r["original"], 1)
        self.assertEqual(r["current"], -1)

    def test_check_no_change_is_not_an_amendment(self):
        from proofhouse.core.screen import check_polarity_amendment

        r = check_polarity_amendment(_entry(), _entry())
        self.assertFalse(r["amended"])

    def test_check_absent_in_both_is_not_an_amendment(self):
        from proofhouse.core.screen import check_polarity_amendment

        a, b = _entry(), _entry()
        del a["signal"]["parameters"]["polarity"]
        del b["signal"]["parameters"]["polarity"]
        self.assertFalse(check_polarity_amendment(a, b)["amended"])

    def test_check_added_polarity_is_an_amendment(self):
        """Adding a polarity where there was none changes the hypothesis."""
        from proofhouse.core.screen import check_polarity_amendment

        orig = _entry()
        del orig["signal"]["parameters"]["polarity"]
        self.assertTrue(check_polarity_amendment(_entry(), orig)["amended"])

    # --- enforcement at registration ---

    def test_polarity_flip_reusing_parent_blocks_is_refused(self):
        """The core constraint: a flipped polarity may not reuse the
        parent's validation blocks. Without this the rule is advisory and
        one-sided testing is unearned."""
        from proofhouse.schema.validate import ValidationError

        reg = self._registry()
        parent = _entry("H-2026-001", validation_blocks=["B-1", "B-2"])
        reg.register(parent)

        child = _entry("H-2026-002", validation_blocks=["B-2", "B-3"])
        child["depends_on"] = ["H-2026-001"]
        child["signal"]["parameters"]["polarity"] = -1

        with self.assertRaises(ValidationError) as ctx:
            reg.register(child)
        msg = str(ctx.exception)
        self.assertIn("polarity", msg)
        self.assertIn("B-2", msg)

    def test_polarity_flip_on_fresh_blocks_is_allowed_and_recorded(self):
        reg = self._registry()
        parent = _entry("H-2026-001", validation_blocks=["B-1"])
        reg.register(parent)

        child = _entry("H-2026-002", validation_blocks=["B-9"])
        child["depends_on"] = ["H-2026-001"]
        child["signal"]["parameters"]["polarity"] = -1

        stored = reg.register(child)
        fields = [a["field"] for a in stored.get("amendments", [])]
        self.assertIn("signal.parameters.polarity", fields)

    def test_effect_size_change_reusing_blocks_is_refused(self):
        """The same enforcement now applies to expected_effect_size, which
        previously annotated but did not enforce."""
        from proofhouse.schema.validate import ValidationError

        reg = self._registry()
        parent = _entry("H-2026-001", validation_blocks=["B-1"])
        reg.register(parent)

        child = _entry("H-2026-002", validation_blocks=["B-1"])
        child["depends_on"] = ["H-2026-001"]
        child["mechanism"]["expected_effect_size"] = "2.5"

        with self.assertRaises(ValidationError) as ctx:
            reg.register(child)
        self.assertIn("expected_effect_size", str(ctx.exception))

    def test_unchanged_child_may_reuse_blocks(self):
        """No invalidating change means no fresh-block requirement."""
        reg = self._registry()
        parent = _entry("H-2026-001", validation_blocks=["B-1"])
        reg.register(parent)

        child = _entry("H-2026-002", validation_blocks=["B-1"])
        child["depends_on"] = ["H-2026-001"]
        stored = reg.register(child)
        self.assertEqual(stored.get("amendments", []), [])


if __name__ == "__main__":
    unittest.main()
