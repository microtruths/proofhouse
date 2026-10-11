# tests/test_review_gaps.py — coverage gaps found in the Parts A/B review
"""Three gaps the Parts A/B code review surfaced.

1. V-14 (fdr_method + justification) shipped with zero test coverage.
2. decimal_str had only an indirect round-trip assertion.
3. The amendment/fresh-block rule was enforced ONLY at registration, and
   registration is exactly where validation_blocks is empty — so it was
   bypassable by deferring block assignment, which is the normal workflow.
   Both shipped fixtures register with validation_blocks: [].
"""

import os
import shutil
import tempfile
import unittest

from proofhouse.schema.versions import SCHEMA_VERSION


def _valid_entry(entry_id="H-2026-001", **overrides):
    """A schema-valid entry. Inlined rather than imported from another test
    module — pytest does not put sibling test files on sys.path."""
    e = {
        "schema_version": SCHEMA_VERSION,
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
        "fdr_method_justification": "Shared universe implies dependence.",
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


# --------------------------------------------------------------------------
# 1. V-14 — fdr_method required, no default, justification mandatory
# --------------------------------------------------------------------------

class TestV14FdrMethod(unittest.TestCase):

    def _entry(self, **overrides):
        return _valid_entry(**overrides)

    def _validate(self, entry):
        from proofhouse.schema.validate import validate_entry

        validate_entry(entry)

    def test_by_accepted(self):
        self._validate(self._entry(fdr_method="by"))

    def test_bh_accepted(self):
        self._validate(self._entry(fdr_method="bh"))

    def test_missing_rejected_no_default(self):
        """BH and BY give materially different answers — on the published
        BH (1995) example, 4 rejections vs 3. A silent default would pick
        the dependence assumption on the researcher's behalf."""
        from proofhouse.schema.validate import ValidationError

        e = self._entry()
        del e["fdr_method"]
        with self.assertRaises(ValidationError) as ctx:
            self._validate(e)
        self.assertIn("V-14", str(ctx.exception))

    def test_invalid_method_rejected(self):
        from proofhouse.schema.validate import ValidationError

        with self.assertRaises(ValidationError) as ctx:
            self._validate(self._entry(fdr_method="bonferroni"))
        self.assertIn("V-14", str(ctx.exception))

    def test_missing_justification_rejected(self):
        """Writing 'hypotheses are independent' is what forces you to
        notice when they are not."""
        from proofhouse.schema.validate import ValidationError

        e = self._entry()
        e["fdr_method_justification"] = ""
        with self.assertRaises(ValidationError) as ctx:
            self._validate(e)
        self.assertIn("V-14", str(ctx.exception))

    def test_whitespace_justification_rejected(self):
        from proofhouse.schema.validate import ValidationError

        with self.assertRaises(ValidationError):
            self._validate(self._entry(fdr_method_justification="   "))


# --------------------------------------------------------------------------
# 2. decimal_str — direct coverage
# --------------------------------------------------------------------------

class TestDecimalStr(unittest.TestCase):

    def test_float_round_trips(self):
        from proofhouse.chain.canonical import decimal_str

        for v in (0.0048, 0.05, 1e-300, 0.1 + 0.2, -2.5, 0.0):
            self.assertEqual(float(decimal_str(v)), v)

    def test_int_passes_through(self):
        from proofhouse.chain.canonical import decimal_str

        self.assertEqual(decimal_str(4431), "4431")

    def test_str_passes_through_unchanged(self):
        """Hand-written schema values like "0.05" must not be reformatted."""
        from proofhouse.chain.canonical import decimal_str

        self.assertEqual(decimal_str("0.05"), "0.05")

    def test_bool_rejected(self):
        """bool is an int subclass in Python; silently storing True as "1"
        would be a type confusion in a hashed record."""
        from proofhouse.chain.canonical import decimal_str

        for v in (True, False):
            with self.assertRaises(TypeError):
                decimal_str(v)

    def test_none_rejected(self):
        from proofhouse.chain.canonical import decimal_str

        with self.assertRaises(TypeError):
            decimal_str(None)

    def test_output_survives_canonical_bytes(self):
        """The whole point: the result must be hashable, i.e. not a float."""
        from proofhouse.chain.canonical import canonical_bytes, decimal_str

        canonical_bytes({"p_value": decimal_str(0.0048)})  # must not raise
        with self.assertRaises(TypeError):
            canonical_bytes({"p_value": 0.0048})


# --------------------------------------------------------------------------
# 3. Amendment / fresh-block rule enforced at EXECUTION, not just registration
# --------------------------------------------------------------------------

class TestAmendmentBlockReuseAtExecution(unittest.TestCase):
    """The registration-time check is necessary but not sufficient.

    Registering an amended entry with validation_blocks=[] (the normal
    workflow — both fixtures do this) passes the registration check
    trivially. Without a second gate at consumption time, the entry can
    then spend the parent's block.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # A real artifact with a real pin: the pin check is Step 1, so a
        # fake path would raise before the amendment check is reached.
        self.artifact = os.path.join(self.tmp, "strategy.py")
        with open(self.artifact, "w") as f:
            f.write("def run(data, parameters):\n    return ([], [])\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self):
        """A real ledger — the contamination check (Step 3) reads it before
        the amendment check (Step 4) is reached."""
        from proofhouse.core.ledger import Ledger

        return Ledger(os.path.join(self.tmp, "ledger.jsonl"))

    def _fixture(self):
        """Parent has consumed B-1; child amends polarity and targets B-1."""
        from proofhouse.core.blocks import BlockRegistry

        br = BlockRegistry()
        br.add_block({
            "id": "B-1",
            "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2025-03-02T00:00:00Z",
            "sources": ["csv"],
            "seams": [],
        })
        br.consume_block("B-1", "H-parent")
        return br

    def _child(self):
        from proofhouse.core.artifact_pin import create_implementation_pin

        return {
            "id": "H-child",
            "signal": {
                "implementation": create_implementation_pin(self.artifact),
                "parameters": {"polarity": -1},
            },
            "universe": {"id": "u"},
            "validation_blocks": ["B-1"],
            "amendments": [{
                "field": "signal.parameters.polarity",
                "original": 1, "current": -1, "parent_id": "H-parent",
            }],
        }

    def test_parent_consumed_block_is_refused_at_execution(self):
        from proofhouse.eval.runner import run_eval

        br = self._fixture()
        with self.assertRaises(ValueError) as ctx:
            run_eval(self._child(), adapter=None, block_registry=br,
                     ledger=self._ledger(), stage=2, score_fn=None,
                     manifests_dir=self.tmp)

        msg = str(ctx.exception)
        self.assertIn("H-parent", msg)
        self.assertIn("B-1", msg)
        self.assertIn("amends", msg)

    def test_refusal_precedes_consumption(self):
        """A refused run must not spend the block it was refused over."""
        from proofhouse.eval.runner import run_eval

        br = self._fixture()
        with self.assertRaises(ValueError):
            run_eval(self._child(), adapter=None, block_registry=br,
                     ledger=self._ledger(), stage=2, score_fn=None,
                     manifests_dir=self.tmp)

        consumers = {
            c["entry_id"] for c in br.get_block("B-1").get("consumed_by", [])
        }
        self.assertNotIn("H-child", consumers)
        self.assertEqual(consumers, {"H-parent"})


if __name__ == "__main__":
    unittest.main()
