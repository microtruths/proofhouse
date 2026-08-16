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
