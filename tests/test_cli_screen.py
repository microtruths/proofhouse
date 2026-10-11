# tests/test_cli_screen.py — the `proofhouse screen` command
"""Screening a batch from the command line.

The command reads runs the ledger already holds, the way `gate` does, and
records one `screen` row. What these tests pin:

  * the survivors are exactly the ones the step-up rule predicts, including
    the rank that fails its own threshold and is rejected anyway;
  * the screen row carries the batch, m, q, method and threshold, so the
    decision can be rebuilt from the record rather than taken on trust;
  * a batch carrying a validation block is refused before any result is
    read, and nothing is written;
  * a batch running two FDR procedures is refused, and so is an unknown
    stage.
"""

import os
import shutil
import tempfile
import unittest

from click.testing import CliRunner

from proofhouse.cli.main import cli
from proofhouse.schema.versions import SCHEMA_VERSION


ARTIFACT = """
def run(data, parameters):
    # The adapter supplies predictions and outcomes; the artifact forwards
    # them so the test controls exactly what gets scored.
    return (data["predictions"], data["outcomes"])
"""


class _Adapter:
    """Minimal adapter: .data_hash and .load_returns()."""

    def __init__(self, data_hash):
        self.data_hash = data_hash

    def load_returns(self):
        return {"predictions": [1, 2, 3], "outcomes": [1, 2, 3]}


def _entry(entry_id, pin, blocks, fdr_method="bh"):
    """A registrable entry. Shaped after tests/test_integration.py."""
    return {
        "id": entry_id,
        "schema_version": SCHEMA_VERSION,
        "name": "Screen CLI test entry",
        "created_utc": "2026-09-14T00:00:00Z",
        "status": "registered",
        "depends_on": [],
        "universe": {
            "id": "test-universe",
            "venues": ["test-venue"],
            "instruments": "synthetic 20-day series",
        },
        "mechanism": {
            "class": "toy-model",
            "counterparty": "Random noise",
            "claim": "Toy strategy for the screen CLI test",
            "expected_effect_size": "1.5",
            "expected_decay": "N/A",
        },
        "signal": {
            "inputs": ["return"],
            "transform": "index rank",
            "frequency": "daily",
            "combination": "panel_ic",
            "parameters": {"polarity": 1},
            "implementation": pin,
            "benchmark_sensitivity": {
                "benchmarks": ["btc-subtraction", "eth-subtraction"],
            },
        },
        "feasibility": {
            "min_order_notional_usd": "10",
            "sleeve_range": ["100", "20000"],
            "instruments_held": 8,
            "rebalance_band": "0.15",
        },
        "costs": {"model_ref": "costs/test-v1"},
        "gate": {
            "statistic": "panel_ic",
            "combination": "g1",
            "criteria": {
                "g1": {"comparator": ">", "threshold": {"literal": "0.05"}},
            },
            "benchmark": "btc-subtraction",
            "benchmark_justification": "BTC subtraction as common factor",
            "benchmark_sensitivity": ["btc-subtraction", "eth-subtraction"],
            "on_pass": "promote-to-live",
            "on_fail": "retire",
        },
        "p_value_sidedness": "one-sided",
        "fdr_method": fdr_method,
        "fdr_method_justification": (
            "Hypotheses share one universe and are therefore dependent."),
        "pass_licenses": "Deploy to half-sleeve for 30 days.",
        "pass_does_not_establish": "Persistent alpha or robustness.",
        "live_gate": {"min_duration_weeks": 4},
        "validation_blocks": list(blocks),
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": "1.5", "blocks": 1},
            "value": {"pending": True,
                      "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }


class CliScreenBase(unittest.TestCase):
    """A real project directory: registry, ledger, blocks, manifests."""

    def setUp(self):
        from proofhouse.core.artifact_pin import create_implementation_pin
        from proofhouse.core.blocks import BlockRegistry

        self.tmp = tempfile.mkdtemp()
        self.ph = os.path.join(self.tmp, ".proofhouse")
        for sub in ("registry", "ledger", "manifests", "blocks", "fills",
                    "amendments", "rejected"):
            os.makedirs(os.path.join(self.ph, sub), exist_ok=True)

        self.artifact = os.path.join(self.tmp, "strategy.py")
        with open(self.artifact, "w") as f:
            f.write(ARTIFACT)
        self.pin = create_implementation_pin(self.artifact)

        self.blocks = BlockRegistry(
            path=os.path.join(self.ph, "blocks", "blocks.yaml"))
        self.blocks.add_block({
            "id": "B-S1", "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z", "segment": "S1",
            "data_complete": True, "completed_at": "2025-03-02T00:00:00Z",
            "sources": ["csv"], "seams": [],
        })
        self.blocks.add_block({
            "id": "B-V", "start": "2025-03-01T00:00:00Z",
            "end": "2025-05-01T00:00:00Z", "segment": "V",
            "data_complete": True, "completed_at": "2025-05-02T00:00:00Z",
            "sources": ["csv"], "seams": [],
        })

        self.cwd = os.getcwd()
        os.chdir(self.tmp)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- project helpers ---------------------------------------------------

    def _registry(self):
        from proofhouse.core.registry import Registry

        return Registry(os.path.join(self.ph, "registry"),
                        block_registry=self.blocks)

    def _ledger(self):
        from proofhouse.core.ledger import Ledger

        return Ledger(os.path.join(self.ph, "ledger", "ledger.jsonl"))

    def _register(self, entry_id, blocks=("B-S1",), fdr_method="bh"):
        entry = _entry(entry_id, self.pin, blocks, fdr_method)
        return self._registry().register(entry)

    def _record_eval(self, entry_id, p_value, value):
        """Run one stage-1 eval, so the ledger holds a run to screen."""
        from proofhouse.eval.runner import run_eval
        from proofhouse.eval.types import Diagnostics, ScoredResult

        def score_fn(predictions, outcomes, trial_count):
            return (
                ScoredResult(statistic="panel_ic", value=value,
                             trial_count=trial_count, p_value=p_value),
                Diagnostics({}),
            )

        return run_eval(
            entry=self._registry().get_entry(entry_id),
            adapter=_Adapter("sha256:data-{}".format(entry_id)),
            block_registry=self.blocks,
            ledger=self._ledger(),
            stage=1,
            score_fn=score_fn,
            manifests_dir=os.path.join(self.ph, "manifests"),
        )

    def _screen_rows(self):
        return [r for r in self._ledger().all_rows() if r["kind"] == "screen"]

    def _invoke(self, *args):
        return CliRunner().invoke(cli, ["screen"] + list(args))


class TestScreenCommand(CliScreenBase):
    """m = 4 at q = 0.2. Step-up thresholds: 0.05, 0.1, 0.15, 0.2."""

    def setUp(self):
        CliScreenBase.setUp(self)
        # p = 0.12 FAILS its own threshold (0.12 > 0.1) and is rejected
        # anyway, because rank 3 passes (0.14 <= 0.15). That is the step-up
        # property; reading it as "stop at the first failure" would return
        # one survivor instead of three.
        self.cases = [
            ("H-A", 0.001, 0.08),
            ("H-B", 0.12, 0.06),
            ("H-C", 0.14, 0.04),
            ("H-D", 0.9, 0.02),
        ]
        for entry_id, p, value in self.cases:
            self._register(entry_id)
            self._record_eval(entry_id, p, value)

    def _run_batch(self, *extra):
        ids = [c[0] for c in self.cases]
        return self._invoke(*(ids + ["--q", "0.2"] + list(extra)))

    def test_survivors_are_what_the_step_up_predicts(self):
        result = self._run_batch()
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("survivors: 3 of 4 — H-A, H-B, H-C", result.output)
        self.assertIn("H-D", result.output)
        # Ranked by magnitude, and H-D is dropped rather than ranked.
        self.assertIn("dropped", result.output)

    def test_batch_m_q_method_and_threshold_are_printed(self):
        result = self._run_batch()
        self.assertIn("(m=4)", result.output)
        self.assertIn("q        : 0.2", result.output)
        self.assertIn("method   : bh", result.output)
        self.assertIn("threshold: 0.15", result.output)
        self.assertIn("stage    : 1a", result.output)

    def test_the_screen_row_rebuilds_the_decision(self):
        result = self._run_batch()
        rows = self._screen_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["batch"], ["H-A", "H-B", "H-C", "H-D"])
        self.assertEqual(row["m"], 4)
        self.assertEqual(float(row["q"]), 0.2)
        self.assertEqual(row["fdr_method"], "bh")
        # (3/4)*0.2 is 0.15000000000000002 in binary floating point, and the
        # row stores that exactly rather than a tidied 0.15 — a threshold a
        # few ulps out is the difference between surviving and not.
        self.assertEqual(row["threshold"], "0.15000000000000002")
        self.assertIn(row["threshold"], result.output)
        self.assertEqual(row["survivors"], ["H-A", "H-B", "H-C"])
        self.assertEqual(row["screen_stage"], "1a")
        self.assertEqual(row["universe"], "test-universe")
        # The run id is printed, so the row can be found.
        self.assertTrue(row["run_id"].startswith("SCR-"))
        self.assertIn(row["run_id"], result.output)

    def test_the_screen_is_not_a_trial(self):
        """The runs were counted when they ran; the decision counts nothing."""
        before = self._ledger().count_trials("test-universe")
        self._run_batch()
        self.assertEqual(self._ledger().count_trials("test-universe"), before)
        self.assertEqual(
            self._ledger().count_trials_stage2("test-universe"), 0)

    def test_a_named_source_run_is_screened(self):
        run_id = self._ledger().all_rows()[0]["run_id"]
        result = self._run_batch("--source-run", "H-A={}".format(run_id))
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("run {}".format(run_id), result.output)

    def test_unknown_stage_refused(self):
        result = self._run_batch("--stage", "2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("screen_stage must be '1a' or '1b'", result.output)
        self.assertEqual(self._screen_rows(), [])

    def test_unknown_entry_refused(self):
        result = self._invoke("H-A", "H-NOPE", "--q", "0.2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No such entry: H-NOPE", result.output)
        self.assertEqual(self._screen_rows(), [])


class TestScreenRefusals(CliScreenBase):

    def test_validation_block_in_the_batch_is_refused(self):
        """Refused before a result is read, and nothing is written."""
        self._register("H-A")
        self._record_eval("H-A", 0.001, 0.08)
        self._register("H-V", blocks=("B-V",))

        result = self._invoke("H-A", "H-V", "--q", "0.2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("B-V", result.output)
        self.assertIn("segment V", result.output)
        self.assertIn("stage 1", result.output)
        self.assertEqual(self._screen_rows(), [])
        # Nothing consumed the validation block on the way to the refusal.
        block = self.blocks.get_block("B-V")
        self.assertEqual(block.get("consumed_by", []), [])

    def test_mixed_fdr_method_is_refused(self):
        """FDR correction applies to the family; two procedures is not one."""
        self._register("H-A")
        self._record_eval("H-A", 0.001, 0.08)
        self._register("H-BY", fdr_method="by")
        self._record_eval("H-BY", 0.002, 0.07)

        result = self._invoke("H-A", "H-BY", "--q", "0.2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("mixed fdr_method", result.output)
        self.assertEqual(self._screen_rows(), [])

    def test_entry_with_no_recorded_run_is_refused(self):
        """Screening the rest of the batch without it would shrink m."""
        self._register("H-A")
        self._record_eval("H-A", 0.001, 0.08)
        self._register("H-B")

        result = self._invoke("H-A", "H-B", "--q", "0.2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No stage-1 eval run recorded for H-B", result.output)
        self.assertEqual(self._screen_rows(), [])

    def test_a_missing_p_value_refuses_the_whole_batch(self):
        """Dropping the entry would change every threshold."""
        self._register("H-A")
        self._record_eval("H-A", 0.001, 0.08)
        self._register("H-NOP")
        self._record_eval("H-NOP", None, 0.07)

        result = self._invoke("H-A", "H-NOP", "--q", "0.2")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("p_value", result.output)
        self.assertEqual(self._screen_rows(), [])


if __name__ == "__main__":
    unittest.main()
