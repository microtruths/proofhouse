# tests/test_run_screen.py — Part C: the screening orchestrator
"""run_screen is the only public way to screen a batch.

Part C plan §2.7. The three refusals in §2.2 are the substance: a screen
that silently drops an entry has changed m, and m changes every BH
threshold and therefore the survivor set.
"""

import json
import os
import shutil
import tempfile
import unittest


ARTIFACT = """
def run(data, parameters):
    # Predictions and outcomes are supplied by the test's adapter; the
    # artifact just forwards them so scoring is fully controlled.
    return (data["predictions"], data["outcomes"])
"""


class _Adapter:
    """Minimal adapter: .data_hash and .load_returns()."""

    def __init__(self, data_hash, predictions=None, outcomes=None):
        self.data_hash = data_hash
        self._payload = {
            "predictions": predictions or [1, 2, 3],
            "outcomes": outcomes or [1, 2, 3],
        }

    def load_returns(self):
        return self._payload


class ScreenTestBase(unittest.TestCase):

    def setUp(self):
        from proofhouse.core.artifact_pin import create_implementation_pin

        self.tmp = tempfile.mkdtemp()
        self.artifact = os.path.join(self.tmp, "strategy.py")
        with open(self.artifact, "w") as f:
            f.write(ARTIFACT)
        self.pin = create_implementation_pin(self.artifact)
        self.manifests = os.path.join(self.tmp, "manifests")
        os.makedirs(self.manifests, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self):
        from proofhouse.core.ledger import Ledger

        return Ledger(os.path.join(self.tmp, "ledger.jsonl"))

    def _blocks(self, segment="S1"):
        from proofhouse.core.blocks import BlockRegistry

        br = BlockRegistry()
        br.add_block({
            "id": "B-1", "start": "2025-01-01T00:00:00Z",
            "end": "2025-03-01T00:00:00Z", "segment": segment,
            "data_complete": True, "completed_at": "2025-03-02T00:00:00Z",
            "sources": ["csv"], "seams": [],
        })
        return br

    def _entry(self, entry_id, fdr_method="bh", sidedness="one-sided"):
        return {
            "id": entry_id,
            "signal": {
                "implementation": dict(self.pin),
                "parameters": {"polarity": 1},
            },
            "universe": {"id": "u"},
            "validation_blocks": ["B-1"],
            "fdr_method": fdr_method,
            "p_value_sidedness": sidedness,
        }

    def _score_fn(self, predictions, outcomes, trial_count):
        """A self-contained score_fn for tests that refuse before scoring.

        Deliberately independent of any instance state: this is used only
        by tests that expect run_screen to raise during validation, and a
        helper that would crash if it were ever actually reached is a trap
        for the next test author.
        """
        from proofhouse.eval.types import ScoredResult, Diagnostics

        return (
            ScoredResult(statistic="panel_ic", value=0.05,
                         trial_count=trial_count, p_value=0.01),
            Diagnostics({"note": "test"}),
        )

    def _run(self, pvals, fdr_q=0.05, methods=None, sidedness=None,
             blocks_segment="S1"):
        """Run a screen where entry i has p-value pvals[i]."""
        from proofhouse.core.screen import run_screen

        methods = methods or ["bh"] * len(pvals)
        sidedness = sidedness or ["one-sided"] * len(pvals)
        entries, adapters = [], {}
        for i, p in enumerate(pvals):
            eid = "H-{:02d}".format(i)
            entries.append(self._entry(eid, methods[i], sidedness[i]))
            adapters[eid] = _Adapter("sha256:d{}".format(i))

        ledger = self._ledger()

        # score_fn has no entry context, so drive it from a cursor the
        # runner advances entry by entry.
        outer = self

        def score_fn(predictions, outcomes, trial_count):
            from proofhouse.eval.types import ScoredResult, Diagnostics
            return (
                ScoredResult(statistic="panel_ic", value=0.05,
                             trial_count=trial_count,
                             p_value=outer._cursor.pop(0)),
                Diagnostics({}),
            )

        self._cursor = list(pvals)
        result = run_screen(
            entries, adapters, self._blocks(blocks_segment), ledger,
            score_fn, self.manifests, fdr_q=fdr_q,
        )
        return result, ledger


class TestRunScreen(ScreenTestBase):

    def test_c1_survivors_match_hand_computed_bh(self):
        """C-1: the F-1 case — BH's step-up rejects all five."""
        result, _ = self._run([0.001, 0.008, 0.039, 0.041, 0.042], fdr_q=0.05)
        self.assertEqual(len(result["survivors"]), 5)
        self.assertEqual(result["fdr"]["k"], 5)
        self.assertEqual(result["fdr"]["m"], 5)

    def test_c2_mixed_fdr_method_refused(self):
        """FDR correction applies to the family; two procedures in one
        batch have no coherent interpretation."""
        with self.assertRaises(ValueError) as ctx:
            self._run([0.01, 0.02], methods=["bh", "by"])
        self.assertIn("mixed fdr_method", str(ctx.exception))

    def test_c3_mixed_sidedness_allowed(self):
        """Deliberate asymmetry with fdr_method: sidedness is per-hypothesis
        and BH consumes p-values without caring how each was derived."""
        result, _ = self._run(
            [0.001, 0.002],
            sidedness=["one-sided", "two-sided"],
            fdr_q=0.3,
        )
        self.assertEqual(len(result["survivors"]), 2)

    def test_c4_missing_p_value_refuses_whole_batch(self):
        """Dropping the entry would shrink m and change every threshold."""
        with self.assertRaises(ValueError) as ctx:
            self._run([0.001, None])
        self.assertIn("p_value", str(ctx.exception))

    def test_c5_screen_row_records_the_batch(self):
        result, ledger = self._run([0.001, 0.5], fdr_q=0.05)
        rows = [r for r in ledger.all_rows() if r["kind"] == "screen"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["m"], 2)
        self.assertEqual(sorted(row["batch"]), ["H-00", "H-01"])
        self.assertEqual(row["fdr_method"], "bh")
        self.assertEqual(row["screen_stage"], "1a")
        self.assertEqual(row["survivors"], result["survivors"])
        self.assertEqual(row["run_id"], result["screen_run_id"])

    def test_c10_q_and_threshold_stored_as_decimal_strings(self):
        """Canonical rule 3 — a threshold a few ulps out changes survivors."""
        _, ledger = self._run([0.001, 0.5], fdr_q=0.05)
        row = [r for r in ledger.all_rows() if r["kind"] == "screen"][0]
        self.assertIsInstance(row["q"], str)
        self.assertEqual(float(row["q"]), 0.05)
        self.assertIsInstance(row["threshold"], str)

    def test_c11_rescreening_appends_a_second_row(self):
        """q-shopping is visible in the ledger, not prevented (decision §3.1)."""
        _, ledger = self._run([0.001, 0.5], fdr_q=0.05)
        rows = [r for r in ledger.all_rows() if r["kind"] == "screen"]
        self.assertEqual(len(rows), 1)

        # A second screen on the same ledger leaves both records.
        from proofhouse.core.screen import run_screen
        entries = [self._entry("H-00")]
        adapters = {"H-00": _Adapter("sha256:dX")}
        self._cursor = [0.001]

        def score_fn(predictions, outcomes, trial_count):
            from proofhouse.eval.types import ScoredResult, Diagnostics
            return (ScoredResult("panel_ic", 0.05, trial_count, p_value=0.001),
                    Diagnostics({}))

        run_screen(entries, adapters, self._blocks(), ledger, score_fn,
                   self.manifests, fdr_q=0.3)
        rows = [r for r in ledger.all_rows() if r["kind"] == "screen"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["q"], rows[1]["q"])

    def test_c9_v_block_at_stage_one_fails_hard(self):
        """Screening always runs at eval stage 1, so a V-block is refused."""
        with self.assertRaises(ValueError) as ctx:
            self._run([0.001], blocks_segment="V")
        self.assertIn("stage", str(ctx.exception).lower())

    def test_empty_batch_refused(self):
        from proofhouse.core.screen import run_screen

        with self.assertRaises(ValueError):
            run_screen([], {}, self._blocks(), self._ledger(),
                       self._score_fn, self.manifests, fdr_q=0.3)

    def test_missing_adapter_refused(self):
        """Silently skipping an entry would shrink m."""
        from proofhouse.core.screen import run_screen

        with self.assertRaises(ValueError) as ctx:
            run_screen([self._entry("H-00")], {}, self._blocks(),
                       self._ledger(), self._score_fn, self.manifests,
                       fdr_q=0.3)
        self.assertIn("adapter", str(ctx.exception))

    def test_bad_screen_stage_refused(self):
        from proofhouse.core.screen import run_screen

        with self.assertRaises(ValueError):
            run_screen([self._entry("H-00")], {"H-00": _Adapter("sha256:d")},
                       self._blocks(), self._ledger(), self._score_fn,
                       self.manifests, fdr_q=0.3, screen_stage="2")


class TestSubperiodCounting(ScreenTestBase):
    """C-6/C-7/C-8: sub-period rows are logged but never counted."""

    def test_c7_subperiod_without_parent_refused(self):
        ledger = self._ledger()
        with self.assertRaises(ValueError) as ctx:
            ledger.append_row(
                code_hash="sha256:a", data_hash="sha256:b", universe="u",
                kind="subperiod", blocks_touched=["B-1"],
                stats_scored=["ic"],
            )
        self.assertIn("parent_run_id", str(ctx.exception))

    def test_parent_run_id_rejected_on_other_kinds(self):
        ledger = self._ledger()
        with self.assertRaises(ValueError):
            ledger.append_row(
                code_hash="sha256:a", data_hash="sha256:b", universe="u",
                kind="eval", blocks_touched=["B-1"], stats_scored=["ic"],
                parent_run_id="R-000001",
            )

    def test_c8_subperiods_do_not_inflate_the_trial_count(self):
        """k entries over s sub-periods is k trials, not k*s."""
        ledger = self._ledger()
        ledger.append_row(
            code_hash="sha256:a", data_hash="sha256:parent", universe="u",
            kind="eval", blocks_touched=["B-1"], stats_scored=["ic"],
        )
        parent_id = ledger.all_rows()[-1]["run_id"]
        for i in range(4):
            ledger.append_row(
                code_hash="sha256:a", data_hash="sha256:slice{}".format(i),
                universe="u", kind="subperiod", blocks_touched=["B-1"],
                stats_scored=["ic"], parent_run_id=parent_id,
            )
        self.assertEqual(ledger.count_trials("u"), 1)

    def test_c6_screen_and_subperiod_excluded_from_both_counters(self):
        ledger = self._ledger()
        ledger.append_row(
            code_hash="sha256:a", data_hash="sha256:d", universe="u",
            kind="eval", blocks_touched=["B-1"], stats_scored=["ic"], stage=2,
        )
        parent_id = ledger.all_rows()[-1]["run_id"]
        ledger.append_row(
            code_hash="sha256:a", data_hash="sha256:s", universe="u",
            kind="subperiod", blocks_touched=["B-1"], stats_scored=["ic"],
            parent_run_id=parent_id,
        )
        ledger.append_screen(
            batch=["H-00"], m=1, q=0.05, method="bh", threshold=0.05,
            survivors=["H-00"], screen_stage="1a", universe="u",
        )
        self.assertEqual(ledger.count_trials("u"), 1)
        self.assertEqual(ledger.count_trials_stage2("u"), 1)

    def test_screen_row_survives_reload_and_chain_verifies(self):
        ledger = self._ledger()
        ledger.append_screen(
            batch=["H-00", "H-01"], m=2, q=0.05, method="by", threshold=None,
            survivors=[], screen_stage="1a", universe="u",
        )
        reloaded = self._ledger()
        ok, errors = reloaded.verify()
        self.assertTrue(ok, errors)
        row = [r for r in reloaded.all_rows() if r["kind"] == "screen"][0]
        self.assertEqual(row["m"], 2)
        self.assertNotIn("threshold", row)  # None omitted, not stored as null

    def test_screen_row_on_disk_is_json(self):
        ledger = self._ledger()
        ledger.append_screen(
            batch=["H-00"], m=1, q=0.3, method="bh", threshold=0.3,
            survivors=["H-00"], screen_stage="1b", universe="u",
        )
        path = os.path.join(self.tmp, "ledger.jsonl")
        with open(path) as f:
            disk = json.loads(f.readline())
        self.assertEqual(disk["kind"], "screen")
        self.assertEqual(disk["screen_stage"], "1b")


if __name__ == "__main__":
    unittest.main()


class TestStage1bPath(ScreenTestBase):
    """The 1b branch, which the original Part C tests never exercised.

    Two type mismatches lived here undetected because every test drove the
    1a path:

      - run_screen passed entry DICTS to screen_stage1b, which uses them as
        dict keys -> TypeError: unhashable type: 'dict'
      - run_screen appended ScoredResult objects as sub-period values, which
        screen_stage1b compares against zero -> TypeError

    Both are the same shape as every other defect this project has produced:
    correct components, wrong wiring, no test across the seam.
    """

    def _run_1b(self, pvals, sub_values, fdr_q=0.3):
        from proofhouse.core.screen import run_screen

        entries, adapters, subs = [], {}, {}
        for i, p in enumerate(pvals):
            eid = "H-{:02d}".format(i)
            entries.append(self._entry(eid))
            adapters[eid] = _Adapter("sha256:d{}".format(i))
            subs[eid] = [
                _Adapter("sha256:d{}s{}".format(i, j))
                for j in range(len(sub_values[i]))
            ]

        ledger = self._ledger()
        outer = self
        # Main run per entry, then its sub-period runs, in that order.
        outer._cursor = []
        for i, p in enumerate(pvals):
            outer._cursor.append(("main", p, 0.05))
            for v in sub_values[i]:
                outer._cursor.append(("sub", p, v))

        def score_fn(predictions, outcomes, trial_count):
            from proofhouse.eval.types import ScoredResult, Diagnostics
            _kind, p, value = outer._cursor.pop(0)
            return (
                ScoredResult(statistic="panel_ic", value=value,
                             trial_count=trial_count, p_value=p),
                Diagnostics({}),
            )

        return run_screen(
            entries, adapters, self._blocks(), ledger, score_fn,
            self.manifests, fdr_q=fdr_q, screen_stage="1b",
            sub_period_adapters=subs,
        ), ledger

    def test_1b_runs_at_all(self):
        """Regression: this raised TypeError before the fix."""
        result, _ = self._run_1b([0.001], [[0.04, 0.06, 0.05]])
        self.assertEqual(result["survivors"], ["H-00"])

    def test_1b_drops_sign_inconsistent_entry(self):
        """Consistent sign across sub-periods survives; flipping does not."""
        result, _ = self._run_1b(
            [0.001, 0.002],
            [[0.04, 0.05, 0.06],      # H-00 consistently positive
             [-0.04, -0.05, 0.01]],   # H-01 mostly negative vs +0.05 overall
        )
        self.assertIn("H-00", result["survivors"])
        self.assertNotIn("H-01", result["survivors"])

    def test_1b_logs_subperiod_rows_linked_to_parent(self):
        _, ledger = self._run_1b([0.001], [[0.04, 0.06]])
        rows = ledger.all_rows()
        subs = [r for r in rows if r["kind"] == "subperiod"]
        evals = [r for r in rows if r["kind"] == "eval"]
        self.assertEqual(len(subs), 2)
        self.assertEqual(len(evals), 1)
        for s in subs:
            self.assertEqual(s["parent_run_id"], evals[0]["run_id"])

    def test_1b_subperiods_do_not_inflate_trial_count(self):
        """One entry over three sub-periods is one trial, not four."""
        _, ledger = self._run_1b([0.001], [[0.04, 0.05, 0.06]])
        self.assertEqual(ledger.count_trials("u"), 1)

    def test_1b_screen_row_records_stage(self):
        _, ledger = self._run_1b([0.001], [[0.04, 0.06]])
        row = [r for r in ledger.all_rows() if r["kind"] == "screen"][0]
        self.assertEqual(row["screen_stage"], "1b")


class TestMixedUniverse(ScreenTestBase):
    """Trial counts are per-universe, so a cross-universe batch is not one
    FDR family — and the screen record can only name one universe."""

    def test_mixed_universe_refused(self):
        from proofhouse.core.screen import run_screen

        a = self._entry("H-00")
        b = self._entry("H-01")
        b["universe"] = {"id": "other-universe"}

        with self.assertRaises(ValueError) as ctx:
            run_screen(
                [a, b],
                {"H-00": _Adapter("sha256:d0"), "H-01": _Adapter("sha256:d1")},
                self._blocks(), self._ledger(), self._score_fn,
                self.manifests, fdr_q=0.3,
            )
        msg = str(ctx.exception)
        self.assertIn("universe", msg)
        self.assertIn("other-universe", msg)
