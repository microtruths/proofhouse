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
        assert advisory == (False, ["g2", "g3"])

    def test_a_passing_or_forecloses_the_same_way(self):
        advisory = foreclosure_advisory(
            self._entry_with("g1 or g2"),
            [("g1", True), ("g2", False)],
        )
        assert advisory == (True, ["g2"])

    def test_advisory_is_independent_of_criterion_ordering(self):
        """The defect this replaced: evaluate_gate iterates criteria
        ALPHABETICALLY, so a prefix-based advisory was reporting alphabetical
        position as though it were the order blocks were spent. The answer
        must not move when the names do."""
        entry = self._entry_with("gamma and alpha and beta")
        first = foreclosure_advisory(
            entry, [("gamma", False), ("alpha", True), ("beta", True)])
        second = foreclosure_advisory(
            entry, [("alpha", True), ("beta", True), ("gamma", False)])
        assert first == second == (False, ["alpha", "beta"])

    def test_no_advisory_when_every_criterion_was_load_bearing(self):
        """A conjunction where flipping any single criterion would change the
        verdict has nothing to advise about — no block could have been
        saved."""
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

    def test_two_failing_criteria_are_each_non_decisive(self):
        """Both fail a conjunction, so neither one alone decided it — either
        could flip and the verdict would hold. Both blocks were wasted."""
        assert foreclosure_advisory(
            self._entry_with("g1 and g2"),
            [("g1", False), ("g2", False)],
        ) == (False, ["g1", "g2"])

    def test_advisory_does_not_touch_the_ledger_or_the_entry(self):
        """It reports; it does not act. Acting on foreclosure stays a human
        decision filed as an amendment, exactly as A-12 was."""
        import copy
        entry = self._entry_with("g1 and g2")
        snapshot = copy.deepcopy(entry)
        foreclosure_advisory(entry, [("g1", False), ("g2", True)])
        assert entry == snapshot


class TestAMultiCriterionGateIsRunnableThroughTheCLIPath:
    """A manifest stores ONE result, and every CLI path built
    `{scored.statistic: scored}` from it.

    A gate whose criteria name DIFFERENT statistics was therefore unrunnable
    and unreportable through the supported path: the second criterion came
    back "never produced", which is the pipeline-failure branch, so it raised
    rather than rendering a verdict. Three call sites, one assumption, and
    the first real two-statistic gate hit it — H-2026-PFR3's gate is
    `clustered_t_stat >= 2.923 and mean_net_bps_per_event >= 25`.
    """

    def _entry(self):
        return {
            "id": "H-TWO",
            "gate": {
                "statistic": "clustered_t_stat",
                "combination": "g1 and g2",
                "criteria": {
                    "g1": {"comparator": ">=",
                           "threshold": {"literal": "2.923"}},
                    "g2": {"statistic": "mean_net_bps_per_event",
                           "comparator": ">=",
                           "threshold": {"literal": "25"}},
                },
            },
        }

    def test_required_statistics_covers_defaults_and_overrides(self):
        from proofhouse.core.gate_run import required_statistics

        assert required_statistics(self._entry()) == {
            "clustered_t_stat", "mean_net_bps_per_event"}

    def test_a_secondary_statistic_is_taken_from_metadata(self):
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5,
                              metadata={"mean_net_bps_per_event": 189.0146})

        results = statistics_from_result(self._entry(), scored)

        assert set(results) == {"clustered_t_stat", "mean_net_bps_per_event"}
        assert results["mean_net_bps_per_event"].value == 189.0146
        assert results["clustered_t_stat"] is scored

    def test_the_companion_claims_neither_p_value_nor_sweep(self):
        """Metadata is as trustworthy as the headline value — same run, same
        hashed manifest. What it is not is the PRIMARY measurement, and the
        p-value and benchmark sweep belong to the statistic actually scored.
        """
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5, p_value=0.001,
                              benchmark_results={"btc": 1.0, "eq": 2.0},
                              metadata={"mean_net_bps_per_event": 189.0146})

        companion = statistics_from_result(
            self._entry(), scored)["mean_net_bps_per_event"]

        assert companion.p_value is None
        assert companion.benchmark_results == {}
        assert companion.trial_count == 5

    def test_a_genuinely_absent_statistic_refuses_legibly(self):
        """Refusing here, where the cause is legible, beats letting
        evaluate_gate report it as a broken pipeline."""
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5, metadata={})

        with pytest.raises(KeyError) as exc:
            statistics_from_result(self._entry(), scored)

        assert "mean_net_bps_per_event" in str(exc.value)
        assert "metadata" in str(exc.value)

    def test_a_single_criterion_gate_is_unaffected(self):
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        entry = {"id": "H-ONE", "gate": {
            "statistic": "ic", "combination": "g1",
            "criteria": {"g1": {"comparator": ">=",
                                "threshold": {"literal": "0.05"}}}}}
        scored = ScoredResult("ic", 0.0389, 14)

        assert statistics_from_result(entry, scored) == {"ic": scored}

    def test_the_verdict_actually_renders_end_to_end(self):
        """The property, not the plumbing: both criteria decide."""
        from proofhouse.core.gate import evaluate_gate
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5,
                              metadata={"mean_net_bps_per_event": 189.0146})
        entry = self._entry()

        verdict = evaluate_gate(entry, statistics_from_result(entry, scored))

        assert verdict.passed is True
        assert {c.name for c in verdict.criteria} == {"g1", "g2"}
