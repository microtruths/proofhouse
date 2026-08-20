# tests/test_integration.py — End-to-end integration test
"""
Phase 1c — Integration test.

Tests the orchestrator, not manual assembly. If this test still needs
manual step-sequencing, the orchestrator isn't done.

The previous version (pre-orchestrator) proved the wiring by calling
12 helpers in numbered order. That test passed because the test did
the assembly — which is precisely what proved the assembly wasn't in
the system (#26 one level up).

Now: call run_eval() once. If any safety check is missing, the test
will either fail or (worse) pass without enforcing the check.
"""

import os
import tempfile

import pytest


# A toy strategy artifact for integration testing
_TOY_STRATEGY = '''
def run(data, parameters):
    """Toy strategy: return predictions as polarity * index."""
    polarity = parameters.get("polarity", 1)
    n = len(data)
    predictions = [polarity * (i + 1) / n for i in range(n)]
    outcomes = [r for _, r in data]
    return predictions, outcomes
'''


def _make_score_fn():
    """Create a score_fn for testing.

    The score_fn receives (predictions, outcomes, trial_count) and
    returns (ScoredResult, Diagnostics). It uses trial_count from the
    orchestrator — the coupling that parent §4 requires.
    """
    from proofhouse.eval.stats import panel_ic
    from proofhouse.eval.types import ScoredResult, Diagnostics

    def score_fn(predictions, outcomes, trial_count):
        ic = panel_ic(predictions, outcomes)
        scored = ScoredResult(
            statistic="panel_ic",
            value=ic,
            trial_count=trial_count,
            metadata={"n": len(predictions)},
        )
        diag = Diagnostics()
        diag.add("per_prediction", predictions)
        return scored, diag

    return score_fn


def _make_entry(pin, tmpdir):
    """Build a complete entry for integration testing."""
    return {
        "id": "int-test-001",
        "schema_version": 1,
        "name": "Integration test entry",
        "created_utc": "2026-08-16T00:00:00Z",
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
            "claim": "Toy strategy for integration test",
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
        "fdr_method": "by",
        "fdr_method_justification": "Hypotheses share the hl-perps universe and are therefore dependent.",
        "pass_licenses": "Deploy to half-sleeve for 30 days.",
        "pass_does_not_establish": "Persistent alpha or robustness.",
        "live_gate": {"min_duration_weeks": 4},
        "validation_blocks": ["test-2024-Q1"],
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": "1.5", "blocks": 1},
            "value": {"pending": True, "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }


def _setup_infrastructure(tmpdir):
    """Create all infrastructure for a test run.

    Returns (entry, adapter, block_registry, ledger, registry, manifests_dir).
    """
    from proofhouse.core.artifact_pin import create_implementation_pin
    from proofhouse.core.blocks import BlockRegistry
    from proofhouse.core.ledger import Ledger
    from proofhouse.core.registry import Registry
    from proofhouse.adapters.csv_adapter import CSVAdapter

    # Block registry with an E block
    br = BlockRegistry()
    br.add_block({
        "id": "test-2024-Q1",
        "start": "2024-01-01",
        "end": "2024-02-28",
        "segment": "E",
        "data_complete": True,
        "completed_at": "2024-03-15",
        "sources": ["test-source"],
        "seams": [],
    })

    # CSV data
    csv_path = os.path.join(tmpdir, "returns.csv")
    with open(csv_path, "w") as f:
        f.write("date,return\n")
        for i in range(20):
            date = "2024-01-{:02d}".format(i + 1)
            ret = 0.01 * (i % 5 - 2)
            f.write("{},{}\n".format(date, ret))

    # Artifact
    artifact_path = os.path.join(tmpdir, "toy_strategy.py")
    with open(artifact_path, "w") as f:
        f.write(_TOY_STRATEGY)
    pin = create_implementation_pin(artifact_path)

    adapter = CSVAdapter(csv_path)
    ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))
    registry = Registry(os.path.join(tmpdir, "registry"))
    manifests_dir = os.path.join(tmpdir, "manifests")

    entry = _make_entry(pin, tmpdir)
    registry.register(entry)

    return entry, adapter, br, ledger, registry, manifests_dir


class TestRunEval:
    """Test the orchestrator, not manual assembly."""

    def test_run_eval_full_pipeline(self):
        """run_eval performs every step and returns a complete EvalResult.

        This is the test that proves the orchestrator works. If any step
        is missing from run_eval(), this test should catch it.
        """
        from proofhouse.eval.runner import run_eval
        from proofhouse.eval.types import ScoredResult, Diagnostics

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, adapter, br, ledger, registry, manifests_dir = \
                _setup_infrastructure(tmpdir)

            result = run_eval(
                entry=entry,
                adapter=adapter,
                block_registry=br,
                ledger=ledger,
                stage=1,
                score_fn=_make_score_fn(),
                manifests_dir=manifests_dir,
                seed=42,
            )

            # --- Verify the orchestrator's guarantees ---

            # 1. ScoredResult is the right type
            assert isinstance(result.scored, ScoredResult)
            assert result.scored.statistic == "panel_ic"
            assert result.scored.trial_count >= 1

            # 2. Diagnostics is the right type and separate
            assert isinstance(result.diagnostics, Diagnostics)
            assert not isinstance(result.diagnostics, ScoredResult)
            assert "per_prediction" in result.diagnostics.entries

            # 3. Manifest has full provenance
            m = result.manifest
            assert m["code_hash"] == entry["signal"]["implementation"]["sha256"]
            assert m["data_hash"] == adapter.data_hash
            assert m["registry_ref"] == "int-test-001"
            assert m["seed"] == 42
            assert m["wall_clock_seconds"] is not None
            assert m["run_id"] is not None

            # 4. Manifest was persisted to disk (§5.3)
            manifest_path = os.path.join(
                manifests_dir, "{}.yaml".format(result.run_id)
            )
            assert os.path.exists(manifest_path), \
                "Manifest not persisted to disk: {}".format(manifest_path)

            # Verify the persisted manifest is readable and correct
            import yaml
            with open(manifest_path) as f:
                loaded = yaml.safe_load(f)
            assert loaded["run_id"] == result.run_id
            assert loaded["code_hash"] == m["code_hash"]
            assert loaded["registry_ref"] == "int-test-001"

            # 5. Ledger row was appended with stage and segment
            assert ledger.count_trials("test-universe") == 1
            rows = ledger.all_rows()
            assert len(rows) == 1
            assert rows[0]["stage"] == 1
            assert rows[0]["segment"] == "E"
            assert rows[0]["registry_ref"] == "int-test-001"

            # 6. Ledger chain is valid
            ok, errors = ledger.verify()
            assert ok, "Ledger chain invalid: {}".format(errors)

            # 7. Block was consumed
            block = br.get_block("test-2024-Q1")
            assert len(block.get("consumed_by", [])) == 1
            assert block["consumed_by"][0]["entry_id"] == "int-test-001"

    def test_modified_artifact_blocks_pipeline(self):
        """Modifying the artifact after pinning blocks the entire pipeline.

        This is the #26 test: if the artifact hash doesn't match, nothing
        runs. The orchestrator enforces this — no manual step to forget.
        """
        import pytest
        from proofhouse.eval.runner import run_eval

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, adapter, br, ledger, registry, manifests_dir = \
                _setup_infrastructure(tmpdir)

            # Modify the artifact after pinning
            artifact_path = entry["signal"]["implementation"]["path"]
            with open(artifact_path, "a") as f:
                f.write("\n# unauthorized modification\n")

            with pytest.raises(ValueError, match="hash mismatch"):
                run_eval(
                    entry=entry,
                    adapter=adapter,
                    block_registry=br,
                    ledger=ledger,
                    stage=1,
                    score_fn=_make_score_fn(),
                    manifests_dir=manifests_dir,
                )

            # Verify nothing happened: no ledger rows, no consumption
            assert ledger.count_trials("test-universe") == 0
            block = br.get_block("test-2024-Q1")
            assert len(block.get("consumed_by", [])) == 0

    def test_v_block_in_stage1_blocks_pipeline(self):
        """V-block touch in stage-1 blocks the pipeline via the orchestrator.

        The orchestrator enforces this — no manual call to forget.
        """
        import pytest
        from proofhouse.core.artifact_pin import create_implementation_pin
        from proofhouse.core.blocks import BlockRegistry
        from proofhouse.core.ledger import Ledger
        from proofhouse.adapters.csv_adapter import CSVAdapter
        from proofhouse.eval.runner import run_eval

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a V-block
            br = BlockRegistry()
            br.add_block({
                "id": "v-block",
                "start": "2024-07-01",
                "end": "2024-08-31",
                "segment": "V",
                "data_complete": True,
                "completed_at": "2024-09-15",
                "sources": ["test"],
                "seams": [],
            })

            # Artifact and data
            artifact_path = os.path.join(tmpdir, "toy_strategy.py")
            with open(artifact_path, "w") as f:
                f.write(_TOY_STRATEGY)
            pin = create_implementation_pin(artifact_path)

            csv_path = os.path.join(tmpdir, "returns.csv")
            with open(csv_path, "w") as f:
                f.write("date,return\n2024-01-01,0.01\n2024-01-02,-0.01\n")
            adapter = CSVAdapter(csv_path)
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))
            manifests_dir = os.path.join(tmpdir, "manifests")

            entry = _make_entry(pin, tmpdir)
            entry["validation_blocks"] = ["v-block"]

            with pytest.raises(ValueError, match="cannot be touched at stage 1"):
                run_eval(
                    entry=entry,
                    adapter=adapter,
                    block_registry=br,
                    ledger=ledger,
                    stage=1,
                    score_fn=_make_score_fn(),
                    manifests_dir=manifests_dir,
                )

            # Nothing executed: no ledger rows, no consumption
            assert ledger.count_trials("test-universe") == 0

    def test_stage2_uses_stage2_trial_count(self):
        """Stage-2 run uses count_trials_stage2, not count_trials.

        This is the §2.3.1 separation: screening on S1/S2 doesn't burn
        V's multiplicity budget. Without this, the staged funnel delivers
        zero statistical benefit.
        """
        from proofhouse.eval.runner import run_eval

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, adapter, br, ledger, registry, manifests_dir = \
                _setup_infrastructure(tmpdir)

            # Pre-populate with stage-1 rows (screening runs)
            ledger.append_row(
                code_hash="sha256:s1a", data_hash="sha256:d1",
                universe="test-universe", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )
            ledger.append_row(
                code_hash="sha256:s1b", data_hash="sha256:d2",
                universe="test-universe", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )

            # Run as stage-2 validation
            result = run_eval(
                entry=entry,
                adapter=adapter,
                block_registry=br,
                ledger=ledger,
                stage=2,
                score_fn=_make_score_fn(),
                manifests_dir=manifests_dir,
            )

            # Total trials = 3 (2 stage-1 + 1 stage-2 from run_eval)
            assert ledger.count_trials("test-universe") == 3

            # Stage-2 count excludes stage-1 rows → only the new run
            # But the orchestrator reads BEFORE appending, so it saw 0 stage-2 rows.
            # max(1, 0) = 1
            assert result.scored.trial_count == 1

            # Verify it's different from the full count
            # Full count at scoring time was 2 (the 2 stage-1 rows)
            # If the orchestrator had used count_trials(), it would be 2.
            assert result.scored.trial_count != ledger.count_trials("test-universe") - 1

    def test_score_fn_receives_trial_count(self):
        """The score_fn receives trial_count as an argument from the orchestrator.

        This test verifies the coupling that parent §4 requires:
        'read trial count FROM the ledger, never a parameter.'
        The orchestrator reads the count and passes it to score_fn.
        """
        from proofhouse.eval.runner import run_eval
        from proofhouse.eval.types import ScoredResult, Diagnostics

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, adapter, br, ledger, registry, manifests_dir = \
                _setup_infrastructure(tmpdir)

            # Pre-populate ledger
            ledger.append_row(
                code_hash="sha256:pre", data_hash="sha256:pre",
                universe="test-universe", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
            )

            received_counts = []

            def capturing_score_fn(predictions, outcomes, trial_count):
                received_counts.append(trial_count)
                scored = ScoredResult(
                    statistic="panel_ic", value=0.05,
                    trial_count=trial_count,
                )
                return scored, Diagnostics()

            run_eval(
                entry=entry,
                adapter=adapter,
                block_registry=br,
                ledger=ledger,
                stage=1,
                score_fn=capturing_score_fn,
                manifests_dir=manifests_dir,
            )

            # score_fn was called exactly once
            assert len(received_counts) == 1
            # It received the ledger's trial count (1 pre-existing row)
            assert received_counts[0] == 1

    def test_wrong_score_fn_return_type_rejected(self):
        """score_fn must return (ScoredResult, Diagnostics)."""
        import pytest
        from proofhouse.eval.runner import run_eval

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, adapter, br, ledger, registry, manifests_dir = \
                _setup_infrastructure(tmpdir)

            def bad_score_fn(predictions, outcomes, trial_count):
                return {"value": 0.05}, {"diag": "oops"}

            with pytest.raises(TypeError, match="score_fn must return"):
                run_eval(
                    entry=entry,
                    adapter=adapter,
                    block_registry=br,
                    ledger=ledger,
                    stage=1,
                    score_fn=bad_score_fn,
                    manifests_dir=manifests_dir,
                )

    def test_contaminated_entry_does_not_burn_blocks(self):
        """A contaminated entry is refused WITHOUT consuming its V-blocks.

        This tests the step ordering: contamination check (step 3) runs
        BEFORE consumption (step 4). consume_block has no inverse, so
        spending blocks on a refused run is an unrecoverable loss.
        """
        import pytest
        from proofhouse.core.artifact_pin import create_implementation_pin
        from proofhouse.core.blocks import BlockRegistry
        from proofhouse.core.ledger import Ledger
        from proofhouse.adapters.csv_adapter import CSVAdapter
        from proofhouse.eval.runner import run_eval

        with tempfile.TemporaryDirectory() as tmpdir:
            br = BlockRegistry()
            br.add_block({
                "id": "v-block",
                "start": "2024-07-01",
                "end": "2024-08-31",
                "segment": "V",
                "data_complete": True,
                "completed_at": "2024-09-15",
                "sources": ["test"],
                "seams": [],
            })

            artifact_path = os.path.join(tmpdir, "toy_strategy.py")
            with open(artifact_path, "w") as f:
                f.write(_TOY_STRATEGY)
            pin = create_implementation_pin(artifact_path)

            csv_path = os.path.join(tmpdir, "returns.csv")
            with open(csv_path, "w") as f:
                f.write("date,return\n2024-01-01,0.01\n2024-01-02,-0.01\n")
            adapter = CSVAdapter(csv_path)
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))
            manifests_dir = os.path.join(tmpdir, "manifests")

            # Contaminate: a stage-1 row that touched V
            ledger.append_row(
                code_hash="sha256:bad", data_hash="sha256:bad",
                universe="test-universe", kind="explore",
                blocks_touched=["v-block"],
                stats_scored=["ic"],
                registry_ref="int-test-001",
                stage=1, segment="V",
            )

            entry = _make_entry(pin, tmpdir)
            entry["validation_blocks"] = ["v-block"]

            with pytest.raises(ValueError, match="contaminated"):
                run_eval(
                    entry=entry,
                    adapter=adapter,
                    block_registry=br,
                    ledger=ledger,
                    stage=2,
                    score_fn=_make_score_fn(),
                    manifests_dir=manifests_dir,
                )

            # V-block was NOT consumed — it's still available
            block = br.get_block("v-block")
            assert len(block.get("consumed_by", [])) == 0, \
                "Contaminated run should not consume blocks"


class TestTheResultIsBoundToTheVerdict:
    """The invariant, end to end: *this exact registered entry, evaluated in
    this exact run, produced these exact numbers, which produced this exact
    verdict.*

    Every link used to be loose. run_gate took caller-supplied results with
    no source check; save_manifest wrote plain YAML that nothing referenced
    the digest of; the gate row recorded pass/fail and nothing else; report
    ignored the gate row and re-derived from the newest manifest; verify
    never opened a manifest at all. So editing R-000012.yaml changed what
    `proofhouse report` printed and every hash chain still verified clean.

    This is the test that says otherwise, through the orchestrators rather
    than by assembling the steps here — the #26-one-level-up lesson.
    """

    def _evaluated(self, tmpdir):
        """run_eval through the orchestrator. Returns everything downstream
        needs."""
        from proofhouse.eval.runner import run_eval

        entry, adapter, br, ledger, registry, manifests_dir = \
            _setup_infrastructure(tmpdir)
        result = run_eval(
            entry=entry, adapter=adapter, block_registry=br, ledger=ledger,
            stage=1, score_fn=_make_score_fn(), manifests_dir=manifests_dir,
            seed=42,
        )
        # The REGISTERED entry, with its hash — what the CLI gates against.
        # Gating the in-memory dict instead leaves entry_hash off the row,
        # and check_amendment_ordering reads exactly that field.
        return (registry.get_entry(entry["id"]), br, ledger, registry,
                manifests_dir, result)

    def test_the_eval_row_carries_the_result_into_the_chain(self):
        from proofhouse.core.manifest import manifest_digest, result_digest

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)

            row = ledger.all_rows()[-1]
            assert row["result_hash"] == result_digest(
                result.manifest["result"])
            assert row["manifest_hash"] == manifest_digest(result.manifest)
            ok, errors = ledger.verify()
            assert ok, errors

    def test_the_outcome_string_is_exact_not_rounded(self):
        """The live eval row says `clustered_t_stat=1.861711` for a value of
        1.8617105276654804 — six digits of a number whose comparison against
        2.923 was the verdict. A rounded restatement of a hashed number can
        only ever disagree with it."""
        from proofhouse.chain.canonical import decimal_str

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)

            row = ledger.all_rows()[-1]
            assert row["outcome"] == "panel_ic={}".format(
                decimal_str(result.scored.value))
            assert row["outcome"].endswith(result.manifest["result"]["value"])

    def test_gate_and_report_agree_through_the_record(self):
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import bind_verdict, render_text, \
            build_report

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)

            verdict, gate_run_id = run_gate(
                entry, result.run_id, ledger, manifests_dir, block_registry=br)

            bound, results, provenance = bind_verdict(
                entry, ledger, manifests_dir, block_registry=br)

            assert provenance["status"] == "bound"
            assert provenance["gate_run_id"] == gate_run_id
            assert provenance["source_eval_run_id"] == result.run_id
            assert bound.to_dict() == verdict.to_dict()
            text = render_text(build_report(
                entry, bound, scored_results=results, provenance=provenance))
            assert "BOUND" in text

    def test_verify_is_clean_on_a_bound_record(self):
        """THE CRY-WOLF DIRECTION, checked first. A check that fires on a
        correct record is one people learn to skip — how the first cut of the
        block check and the citation resolver both failed.

        Scoped to the manifests check. This fixture is deliberately minimal
        and declares an EXPLORATION block as its validation block, which V-8
        errors on — a property of the fixture that predates this check and is
        not what is under test here.
        """
        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.verify import verify_all

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)
            run_gate(entry, result.run_id, ledger, manifests_dir,
                     block_registry=br)

            ok, findings = verify_all(
                registry=registry, ledger=ledger, block_registry=br,
                manifests_dir=manifests_dir)

            manifests = [f for f in findings if f.check == "manifests"]
            assert manifests == [], [str(f) for f in manifests]
            # Nothing UNVERIFIABLE either: a record written by the current
            # tool is checkable, and saying "cannot check" about it would be
            # the same cry-wolf failure one class over.
            assert [f for f in findings if f.severity == "unverifiable"] == []

    def test_THE_TAMPER_TEST(self):
        """Edit the manifest's result: verify FAILS and report REFUSES.

        §6: "the tamper test, which is the point of the whole change". Before
        it, both passed clean on exactly this edit.
        """
        import yaml

        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import ReportRefused, bind_verdict
        from proofhouse.core.verify import verify_all

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)
            run_gate(entry, result.run_id, ledger, manifests_dir,
                     block_registry=br)

            # The edit. The gate is panel_ic > 0.05 and this fixture clears
            # it, so the tamper moves the recorded number to one that would
            # not have — the mirror of the fail-into-pass case, caught by the
            # same digest, because the check is on the record agreeing with
            # itself and not on which way the verdict went.
            path = os.path.join(manifests_dir,
                                "{}.yaml".format(result.run_id))
            with open(path) as fh:
                manifest = yaml.safe_load(fh)
            assert float(manifest["result"]["value"]) > 0.05
            manifest["result"]["value"] = "0.01"
            with open(path, "w") as fh:
                yaml.dump(manifest, fh, default_flow_style=False,
                          sort_keys=True)

            # 1. verify FAILS, and says the file is what moved.
            ok, findings = verify_all(
                registry=registry, ledger=ledger, block_registry=br,
                manifests_dir=manifests_dir)
            errors = [f for f in findings if f.severity == "error"]
            assert ok is False
            assert any("the file is what moved" in f.message for f in errors)
            # And the verdict is caught independently of the digest: the
            # recorded criterion no longer matches the numbers on disk.
            assert any("no longer the comparison the record describes"
                       in f.message for f in errors)

            # 2. report REFUSES, and names the side that moved.
            with pytest.raises(ReportRefused) as exc:
                bind_verdict(entry, ledger, manifests_dir, block_registry=br)
            assert "THE SIDE THAT MOVED IS THE MANIFEST" in str(exc.value)

    def test_a_gate_engine_change_is_refused_and_named(self):
        """D-3's other direction, and the one that needs a stated procedure:
        recompute-and-refuse fires on any deliberate gate-engine change. The
        recorded row is edited here in place of shipping a different engine —
        what the check compares is the record against the recomputation, and
        this is the cheapest way to make them disagree with the manifest
        intact."""
        import json

        from proofhouse.core.gate_run import run_gate
        from proofhouse.core.report import ReportRefused, bind_verdict

        with tempfile.TemporaryDirectory() as tmpdir:
            entry, br, ledger, registry, manifests_dir, result = \
                self._evaluated(tmpdir)
            run_gate(entry, result.run_id, ledger, manifests_dir,
                     block_registry=br)

            # Rewrite the recorded verdict as though a different engine had
            # rendered it. The ledger chain now breaks too — which is the
            # point: this is not a thing that can be done quietly.
            path = os.path.join(tmpdir, "ledger.jsonl")
            lines = open(path).read().splitlines()
            row = json.loads(lines[-1])
            was = row["criteria"][0]["passed"]
            row["criteria"][0]["passed"] = not was
            row["outcome"] = "fail" if was else "pass"
            lines[-1] = json.dumps(row, sort_keys=True,
                                   separators=(",", ":"))
            with open(path, "w") as fh:
                fh.write("\n".join(lines) + "\n")

            from proofhouse.core.ledger import Ledger
            reloaded = Ledger(path)
            with pytest.raises(ReportRefused) as exc:
                bind_verdict(entry, reloaded, manifests_dir,
                             block_registry=br)
            assert "THE SIDE THAT MOVED IS THE GATE ENGINE" in str(exc.value)
            assert "criterion g1 passed: recorded {!r}, now {!r}".format(
                not was, was) in str(exc.value)
