# tests/test_gate_run.py — the gate engine's production invocation path
"""
Phase 2b — end-to-end through run_gate, the call site that makes
evaluate_gate reachable.

The unit tests in test_gate.py and test_amend.py prove the engine and the lock
work in isolation. These prove they are WIRED — which is the defect this
project keeps rediscovering: never a wrong component, always an unconnected
one (failure catalog #26, and "#26 one level up" in the S-2 test).
"""

import os
import tempfile

import pytest

from proofhouse.core.amend import AmendmentLog, RerunRefused
from proofhouse.core.gate import GateExecutionError
from proofhouse.core.gate_run import foreclosure_advisory, run_gate
from proofhouse.core.ledger import Ledger
from proofhouse.eval.types import ScoredResult


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


def _entry(hash_="h0", threshold="0.05", blocks=("B-V-01",)):
    return {
        "id": "H-2026-AC10",
        "hash": hash_,
        "validation_blocks": list(blocks),
        "universe": {"id": "hl-perps-wallets"},
        "pass_licenses": "A 6-week shadow trial.",
        "pass_does_not_establish": "Deployable alpha.",
        "gate": {
            "statistic": "ic",
            "combination": "g1",
            "criteria": {
                "g1": {"comparator": ">=",
                       "threshold": {"literal": threshold}},
            },
        },
    }


def _scored(value):
    return {"ic": ScoredResult(statistic="ic", value=value, trial_count=14)}


class TestTheCardinalSinEndToEnd:
    """Gate, fail, amend, re-run — refused through the real path."""

    def test_full_cycle(self):
        ledger = Ledger(_tmp("ledger.jsonl"))
        amendments = AmendmentLog(_tmp("amendments.jsonl"))
        entry = _entry(threshold="0.05")

        # 1. First gate: IC 0.0389 against >= 0.05 -> FAIL.
        verdict, run_id = run_gate(
            entry, _scored(0.038898), ledger,
            code_hash="c1", data_hash="d1", amendments=[],
        )
        assert verdict.passed is False
        assert ledger.all_rows()[-1]["entry_hash"] == "h0"

        # 2. Amend the threshold down so the same data would now pass. This
        #    is the move the lock exists to catch.
        amended = _entry(hash_="h1", threshold="0.03")
        amendments.file(
            entry["id"], entry, amended,
            rationale="0.05 was too strict.",
            impact="The same window would now pass.",
            ledger_anchor=run_id,
        )

        # 3. Re-run on the same window -> refused.
        with pytest.raises(RerunRefused):
            run_gate(
                amended, _scored(0.038898), ledger,
                code_hash="c1", data_hash="d1",
                amendments=amendments.for_entry(entry["id"]),
            )

        # 4. Fresh window -> allowed, and now passes on the amended threshold.
        fresh = _entry(hash_="h1", threshold="0.03", blocks=("B-V-99",))
        verdict, _ = run_gate(
            fresh, _scored(0.038898), ledger,
            code_hash="c2", data_hash="d2",
            amendments=amendments.for_entry(entry["id"]),
        )
        assert verdict.passed is True

    def test_a_refusal_costs_nothing(self):
        """Phase 1 shipped the mirror-image bug: contamination checked after
        block consumption, so refused runs burned validation blocks. A
        refusal must leave the ledger exactly as it was."""
        ledger = Ledger(_tmp("ledger.jsonl"))
        entry = _entry()

        _, run_id = run_gate(entry, _scored(0.02), ledger,
                             code_hash="c1", data_hash="d1")
        before = len(ledger.all_rows())

        amendment = {
            "amendment_id": "A-0001", "entry_id": entry["id"], "number": 1,
            "rationale": "r", "impact": "i", "diff": [{"field": "x"}],
            "ledger_anchor": run_id, "entry_hash_after": "h1",
        }
        with pytest.raises(RerunRefused):
            run_gate(entry, _scored(0.02), ledger,
                     code_hash="c1", data_hash="d1",
                     amendments=[amendment])

        assert len(ledger.all_rows()) == before

    def test_pipeline_failure_records_no_verdict(self):
        """§2.5: a missing statistic is not a gate outcome. No row is written,
        so nothing can later be read as evidence the gate failed."""
        ledger = Ledger(_tmp("ledger.jsonl"))
        with pytest.raises(GateExecutionError):
            run_gate(_entry(), {}, ledger, code_hash="c", data_hash="d")
        assert ledger.all_rows() == []


class TestLedgerRecording:
    def test_gate_row_reuses_the_evals_hashes_so_it_is_not_a_second_trial(self):
        """The ledger dedups on (code_hash, data_hash, universe). A gate row
        carrying fresh hashes would inflate the multiplicity count, and every
        deflated-Sharpe threshold reads from that count."""
        ledger = Ledger(_tmp("ledger.jsonl"))
        entry = _entry()

        ledger.append_row(
            code_hash="c1", data_hash="d1", universe="hl-perps-wallets",
            kind="eval", blocks_touched=["B-V-01"], stats_scored=["ic"],
            registry_ref=entry["id"], outcome="ic=0.038898",
        )
        before = ledger.count_trials("hl-perps-wallets")

        run_gate(entry, _scored(0.038898), ledger,
                 code_hash="c1", data_hash="d1")

        assert ledger.count_trials("hl-perps-wallets") == before

    def test_gate_row_records_outcome_and_verifies(self):
        path = _tmp("ledger.jsonl")
        ledger = Ledger(path)
        run_gate(_entry(), _scored(0.06), ledger,
                 code_hash="c", data_hash="d")

        reloaded = Ledger(path)
        ok, errors = reloaded.verify()
        assert ok, errors
        row = reloaded.all_rows()[-1]
        assert row["kind"] == "gate"
        assert row["outcome"] == "pass"
        assert row["stats_scored"] == ["ic"]


class TestForeclosureAdvisory:
    """D2: arithmetic, surfaced. Never an automatic stop.

    The useful question is not "is this decided" — after a full evaluation it
    always is — but "how much of this did I not need to spend?" A-12's whole
    point was that W8 could stay unspent and clean.
    """

    def _entry_with(self, combination):
        entry = _entry()
        entry["gate"]["combination"] = combination
        return entry

    def test_reports_which_criteria_could_not_have_mattered(self):
        """g1 fails a conjunction, so g2 and g3 were never load-bearing —
        the A-12 case exactly."""
        advisory = foreclosure_advisory(
            self._entry_with("g1 and g2 and g3"),
            [("g1", False), ("g2", True), ("g3", True)],
        )
        assert advisory == (False, "g1", ["g2", "g3"])

    def test_a_passing_or_forecloses_the_same_way(self):
        advisory = foreclosure_advisory(
            self._entry_with("g1 or g2"),
            [("g1", True), ("g2", False)],
        )
        assert advisory == (True, "g1", ["g2"])

    def test_no_advisory_when_every_criterion_was_load_bearing(self):
        """A conjunction that only resolves on the last criterion has nothing
        to advise about — no block could have been saved."""
        assert foreclosure_advisory(
            self._entry_with("g1 and g2"),
            [("g1", True), ("g2", True)],
        ) is None

    def test_no_advisory_on_a_single_criterion_gate(self):
        """The degenerate case. Saying 'already determined, remaining criteria
        cannot change it' when there ARE no remaining criteria is noise
        dressed as insight."""
        assert foreclosure_advisory(
            self._entry_with("g1"), [("g1", False)]) is None

    def test_advisory_does_not_touch_the_ledger_or_the_entry(self):
        """It reports; it does not act. Acting on foreclosure stays a human
        decision filed as an amendment, exactly as A-12 was."""
        import copy
        entry = self._entry_with("g1 and g2")
        snapshot = copy.deepcopy(entry)
        foreclosure_advisory(entry, [("g1", False), ("g2", True)])
        assert entry == snapshot
