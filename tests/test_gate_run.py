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
from proofhouse.core.gate_run import (ProvenanceRefused, foreclosure_advisory,
                                      run_gate)
from proofhouse.eval.types import ScoredResult
from tests.bound_eval import project, record_eval, reload, tamper


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
    return ScoredResult(statistic="ic", value=value, trial_count=14)


class TestTheCardinalSinEndToEnd:
    """Gate, fail, amend, re-run — refused through the real path."""

    def test_full_cycle(self):
        ledger, manifests = project()
        amendments = AmendmentLog(_tmp("amendments.jsonl"))
        entry = _entry(threshold="0.05")

        # 1. First gate: IC 0.0389 against >= 0.05 -> FAIL.
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        verdict, run_id = run_gate(
            entry, source, ledger, manifests, amendments=[])
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

        # 3. Re-run on the same window -> refused. The lock is checked before
        #    the source is even resolved, which is why the same source_run_id
        #    is what gets refused rather than a missing-run complaint.
        with pytest.raises(RerunRefused):
            run_gate(amended, source, ledger, manifests,
                     amendments=amendments.for_entry(entry["id"]))

        # 4. Fresh window -> allowed, and now passes on the amended threshold.
        fresh = _entry(hash_="h1", threshold="0.03", blocks=("B-V-99",))
        fresh_source = record_eval(ledger, manifests, fresh, _scored(0.038898),
                                   code_hash="c2", data_hash="d2")
        verdict, _ = run_gate(
            fresh, fresh_source, ledger, manifests,
            amendments=amendments.for_entry(entry["id"]))
        assert verdict.passed is True

    def test_a_refusal_costs_nothing(self):
        """Phase 1 shipped the mirror-image bug: contamination checked after
        block consumption, so refused runs burned validation blocks. A
        refusal must leave the ledger exactly as it was."""
        ledger, manifests = project()
        entry = _entry()

        source = record_eval(ledger, manifests, entry, _scored(0.02))
        _, run_id = run_gate(entry, source, ledger, manifests)
        before = len(ledger.all_rows())

        amendment = {
            "amendment_id": "A-0001", "entry_id": entry["id"], "number": 1,
            "rationale": "r", "impact": "i", "diff": [{"field": "x"}],
            "ledger_anchor": run_id, "entry_hash_after": "h1",
        }
        with pytest.raises(RerunRefused):
            run_gate(entry, source, ledger, manifests,
                     amendments=[amendment])

        assert len(ledger.all_rows()) == before

    def test_pipeline_failure_records_no_verdict(self):
        """§2.5: a missing statistic is not a gate outcome. No row is written,
        so nothing can later be read as evidence the gate failed.

        The refusal now arrives from statistics_from_result rather than from
        evaluate_gate: run_gate assembles the statistics the gate names, so a
        result that cannot supply one is refused where the cause is legible,
        before any comparison. evaluate_gate's pipeline-failure branch is
        therefore unreachable THROUGH run_gate — it is still the backstop for
        a direct call, and test_gate.py holds it there.
        """
        ledger, manifests = project()
        entry = _entry()
        wrong = ScoredResult(statistic="something_else", value=0.4,
                             trial_count=14)
        source = record_eval(ledger, manifests, entry, wrong)

        with pytest.raises(KeyError) as exc:
            run_gate(entry, source, ledger, manifests)

        assert "ic" in str(exc.value)
        assert [r for r in ledger.all_rows() if r["kind"] == "gate"] == []


class TestLedgerRecording:
    def test_gate_row_reuses_the_evals_hashes_so_it_is_not_a_second_trial(self):
        """The ledger dedups on (code_hash, data_hash, universe). A gate row
        carrying fresh hashes would inflate the multiplicity count, and every
        deflated-Sharpe threshold reads from that count.

        The hashes are no longer the caller's to supply — run_gate copies
        them off the eval row it was pointed at, so they cannot disagree with
        the run that earned them."""
        ledger, manifests = project()
        entry = _entry()

        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        before = ledger.count_trials("hl-perps-wallets")

        run_gate(entry, source, ledger, manifests)

        assert ledger.count_trials("hl-perps-wallets") == before
        row = ledger.all_rows()[-1]
        eval_row = ledger.all_rows()[0]
        assert (row["code_hash"], row["data_hash"]) == (
            eval_row["code_hash"], eval_row["data_hash"])

    def test_gate_row_records_outcome_and_verifies(self):
        ledger, manifests = project()
        source = record_eval(ledger, manifests, _entry(), _scored(0.06))
        run_gate(_entry(), source, ledger, manifests)

        reloaded = reload(ledger)
        ok, errors = reloaded.verify()
        assert ok, errors
        row = reloaded.all_rows()[-1]
        assert row["kind"] == "gate"
        assert row["outcome"] == "pass"
        assert row["stats_scored"] == ["ic"]

    def test_the_row_records_which_run_and_which_numbers(self):
        """Point 4. The gate row recorded pass/fail and nothing else, so the
        verdict sat in the chain with no link to the measurement and no
        record of the values compared. CriterionOutcome.to_dict has said
        "Serialize for the gate ledger row" since 2b with no caller."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        eval_row = ledger.all_rows()[-1]

        run_gate(entry, source, ledger, manifests)
        row = ledger.all_rows()[-1]

        assert row["source_eval_run_id"] == source
        assert row["result_hash"] == eval_row["result_hash"]
        assert [c["name"] for c in row["criteria"]] == ["g1"]
        # The exact value compared, as a canonical decimal string — not a
        # rounded restatement of it.
        assert row["criteria"][0]["actual"] == "0.038898000000000002"
        # The registered literal is the string "0.05"; the recorded threshold
        # is decimal_str of the double it parses to. Pinned to what the
        # function RETURNS, not to what the entry reads — an approximation
        # whose output sits in records is not something to "fix" here.
        assert row["criteria"][0]["threshold"] == "0.050000000000000003"
        assert row["criteria"][0]["passed"] is False

    def test_the_recorded_criteria_survive_the_chain(self):
        """A field added to the row after hashing would leave the stored hash
        disagreeing with its contents and break the chain on the next
        verify. That is how entry_hash was nearly got wrong."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        run_gate(entry, source, ledger, manifests)

        ok, errors = reload(ledger).verify()
        assert ok, errors


class TestTheResultMustComeFromARecordedRun:
    """The invariant: *this exact registered entry, evaluated in this exact
    run, produced these exact numbers, which produced this exact verdict.*

    run_gate used to take `scored_results`, `code_hash` and `data_hash` with
    no source check at all, so a caller could present a valid pair of hashes
    alongside altered numbers and the verdict would be recorded as though it
    came from the run.
    """

    def test_a_run_that_does_not_exist_is_refused(self):
        ledger, manifests = project()
        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(_entry(), "R-000999", ledger, manifests)
        assert "R-000999" in str(exc.value)
        assert ledger.all_rows() == []

    def test_a_run_belonging_to_another_entry_is_refused(self):
        """The v1 successor path. A successor gating on its parent's eval
        would render a verdict on data it never spent anything to see."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898),
                             registry_ref="H-2026-SOMEONE-ELSE")

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)

        assert "H-2026-SOMEONE-ELSE" in str(exc.value)
        assert [r for r in ledger.all_rows() if r["kind"] == "gate"] == []

    def test_a_gate_row_is_not_a_source(self):
        """A gate reads the measurement, not another verdict."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        run_gate(entry, source, ledger, manifests)
        gate_run_id = ledger.all_rows()[-1]["run_id"]

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(_entry(blocks=("B-V-77",)), gate_run_id, ledger,
                     manifests, kind="maintenance")
        assert "not an eval run" in str(exc.value)

    def test_a_legacy_row_cannot_be_gated_on(self):
        """R-000012 carries no digest, so its numbers are not in the chain.
        `verify` reports such a row and `report` renders it with the gap
        stated; what nothing does is render a NEW verdict against it."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898),
                             digests=False)

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)
        assert "result_hash" in str(exc.value)

    def test_a_missing_manifest_is_refused(self):
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        os.remove(os.path.join(manifests, "{}.yaml".format(source)))

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)
        assert "not a result" in str(exc.value)

    def test_THE_TAMPER_TEST(self):
        """Edit the manifest's result; the gate refuses.

        This is the demonstration the whole change is measured against.
        Before it, `save_manifest` wrote plain YAML that nothing referenced
        the digest of, so editing R-000012.yaml changed what the tool printed
        and every hash chain still verified clean.
        """
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))

        # 0.038898 fails >= 0.05. Make it pass.
        tamper(manifests, source, value="0.06")

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)

        assert "THE FILE MOVED" in str(exc.value)
        assert [r for r in ledger.all_rows() if r["kind"] == "gate"] == []

    def test_tampering_with_provenance_is_caught_separately(self):
        """The result block still matches, so what changed is what the
        numbers are a measurement OF."""
        import yaml
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))

        path = os.path.join(manifests, "{}.yaml".format(source))
        with open(path) as fh:
            manifest = yaml.safe_load(fh)
        manifest["data_hash"] = "sha256:a-different-window-entirely"
        with open(path, "w") as fh:
            yaml.dump(manifest, fh, default_flow_style=False, sort_keys=True)

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)
        assert "manifest_hash" in str(exc.value)


class TestOneDecisionPerMeasurement:
    """Point 7, the demoted v1 B5 finding in its correct form.

    run_gate appends its row only after evaluate_gate returns, so a pipeline
    failure never writes one and ANY existing gate row means a verdict was
    recorded. A second decision-grade row for the same measurement is
    therefore ledger duplication: two rows, one decision, and nothing in the
    record saying which is the decision. Not the cardinal sin — that is the
    re-run lock's job, and it fires on a re-run under a CHANGED
    specification.
    """

    def test_a_second_decision_grade_gate_is_refused(self):
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        run_gate(entry, source, ledger, manifests)
        before = len(ledger.all_rows())

        with pytest.raises(ProvenanceRefused) as exc:
            run_gate(entry, source, ledger, manifests)

        assert "already carries a decision-grade verdict" in str(exc.value)
        assert len(ledger.all_rows()) == before

    def test_an_explicit_replay_is_permitted_and_does_not_count(self):
        """`maintenance` is already in _EXCLUDED_KINDS. Logged, never
        unlogged, and excluded from the multiplicity count — the same shape
        as `subperiod`."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        run_gate(entry, source, ledger, manifests)
        trials = ledger.count_trials("hl-perps-wallets")

        verdict, replay_id = run_gate(entry, source, ledger, manifests,
                                      kind="maintenance")

        assert verdict.passed is False
        row = ledger.all_rows()[-1]
        assert row["run_id"] == replay_id
        assert row["kind"] == "maintenance"
        assert row["source_eval_run_id"] == source
        assert ledger.count_trials("hl-perps-wallets") == trials

    def test_a_replay_does_not_become_the_decision(self):
        """A replay may follow a replay; what it may not do is stand in for
        a decision-grade row that was never written."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        run_gate(entry, source, ledger, manifests, kind="maintenance")
        run_gate(entry, source, ledger, manifests, kind="maintenance")

        kinds = [r["kind"] for r in ledger.all_rows()]
        assert kinds == ["eval", "maintenance", "maintenance"]
        # The decision is still available — no decision row exists yet.
        verdict, _ = run_gate(entry, source, ledger, manifests)
        assert verdict.passed is False

    def test_a_legacy_gate_row_still_blocks_a_duplicate(self):
        """A row from before source_eval_run_id existed cannot say which run
        it gated. The only evidence available is the ledger's own dedup key,
        and matching on it can only cause a refusal — which is answered by
        asking for the replay explicitly."""
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        # A legacy gate row: same entry, same (code_hash, data_hash), no
        # source_eval_run_id. R-000013 in the live registry is this row.
        ledger.append_row(
            code_hash="c1", data_hash="d1", universe="hl-perps-wallets",
            kind="gate", blocks_touched=["B-V-01"], stats_scored=["ic"],
            registry_ref=entry["id"], outcome="fail",
        )

        with pytest.raises(ProvenanceRefused):
            run_gate(entry, source, ledger, manifests)

        verdict, _ = run_gate(entry, source, ledger, manifests,
                              kind="maintenance")
        assert verdict.passed is False

    def test_an_unknown_kind_is_refused(self):
        ledger, manifests = project()
        entry = _entry()
        source = record_eval(ledger, manifests, entry, _scored(0.038898))
        with pytest.raises(ValueError):
            run_gate(entry, source, ledger, manifests, kind="explore")


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

    def test_a_secondary_statistic_comes_from_a_first_class_field(self):
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult(
            "clustered_t_stat", 5.8985, 5,
            secondary_statistics={"mean_net_bps_per_event": 189.0146})

        results = statistics_from_result(self._entry(), scored)

        assert set(results) == {"clustered_t_stat", "mean_net_bps_per_event"}
        assert results["mean_net_bps_per_event"].value == 189.0146
        assert results["clustered_t_stat"] is scored

    def test_a_metadata_statistic_is_refused(self):
        """§2.1(a). It used to be read straight out of `metadata`, on the
        argument that metadata sits in the same recorded manifest and is
        therefore equally trustworthy. The trust was never the problem — the
        ENCODING was: to_dict deliberately does not canonicalize metadata,
        licensed by "nothing in it is compared against a threshold, so no
        verdict can turn on an ulp of it." A gate criterion reading it
        revoked that licence.
        """
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5,
                              metadata={"mean_net_bps_per_event": 189.0146})

        with pytest.raises(ProvenanceRefused) as exc:
            statistics_from_result(self._entry(), scored)

        assert "mean_net_bps_per_event" in str(exc.value)
        assert "secondary_statistics" in str(exc.value)

    def test_a_gate_refuses_a_metadata_statistic_end_to_end(self):
        """The refusal has to hold on the path that RECORDS something, not
        only in the assembler."""
        ledger, manifests = project()
        entry = dict(self._entry(), universe={"id": "hl-perps"},
                     validation_blocks=[])
        scored = ScoredResult("clustered_t_stat", 5.8985, 5,
                              metadata={"mean_net_bps_per_event": 189.0146})
        source = record_eval(ledger, manifests, entry, scored)

        with pytest.raises(ProvenanceRefused):
            run_gate(entry, source, ledger, manifests)

        assert [r for r in ledger.all_rows() if r["kind"] == "gate"] == []

    def test_the_legacy_report_path_may_still_read_metadata(self):
        """R-000012's secondary gating value lives in metadata and always
        will — the manifest is immutable. The report path renders it and
        states that it is unverifiable; what it may not do is record a new
        verdict from it, which is why the permission is a parameter on the
        assembler rather than the default."""
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult("clustered_t_stat", 5.8985, 5,
                              metadata={"mean_net_bps_per_event": 189.0146})

        results = statistics_from_result(self._entry(), scored,
                                         allow_metadata=True)

        assert results["mean_net_bps_per_event"].value == 189.0146

    def test_the_companion_claims_neither_p_value_nor_sweep(self):
        """A secondary statistic is a measurement from the same run, not a
        second primary — the p-value and the benchmark sweep belong to the
        statistic that was actually scored.
        """
        from proofhouse.core.gate_run import statistics_from_result
        from proofhouse.eval.types import ScoredResult

        scored = ScoredResult(
            "clustered_t_stat", 5.8985, 5, p_value=0.001,
            benchmark_results={"btc": 1.0, "eq": 2.0},
            secondary_statistics={"mean_net_bps_per_event": 189.0146})

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
        assert "secondary_statistics" in str(exc.value)

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

        scored = ScoredResult(
            "clustered_t_stat", 5.8985, 5,
            secondary_statistics={"mean_net_bps_per_event": 189.0146})
        entry = self._entry()

        verdict = evaluate_gate(entry, statistics_from_result(entry, scored))

        assert verdict.passed is True
        assert {c.name for c in verdict.criteria} == {"g1", "g2"}
