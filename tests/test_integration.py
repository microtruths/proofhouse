# tests/test_integration.py — End-to-end integration test
"""
Phase 1c — Integration test.

Exercises the full pipeline: register entry → pin artifact → load data →
run eval → score result → check power → append ledger row → verify chain.

This is the "smoke test that every piece connects" — not a unit test of
individual functions, but a proof that the wiring is correct.
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


class TestEndToEndPipeline:
    """Full pipeline integration test."""

    def test_full_pipeline(self):
        """Register → pin → load → eval → score → ledger → verify."""
        from proofhouse.core.artifact_pin import create_implementation_pin, verify_pin
        from proofhouse.core.blocks import BlockRegistry
        from proofhouse.core.ledger import Ledger
        from proofhouse.core.registry import Registry
        from proofhouse.eval.runner import load_and_run_artifact, create_eval_manifest
        from proofhouse.eval.stats import panel_ic, deflated_sharpe, benchmark_sensitivity
        from proofhouse.eval.power import compute_power_check
        from proofhouse.eval.types import ScoredResult, Diagnostics
        from proofhouse.adapters.csv_adapter import CSVAdapter

        with tempfile.TemporaryDirectory() as tmpdir:
            # === Setup infrastructure ===

            # 1. Create block registry with an E block
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

            # 2. Write a CSV data file
            csv_path = os.path.join(tmpdir, "returns.csv")
            with open(csv_path, "w") as f:
                f.write("date,return\n")
                for i in range(20):
                    date = "2024-01-{:02d}".format(i + 1)
                    ret = 0.01 * (i % 5 - 2)  # -0.02, -0.01, 0, 0.01, 0.02
                    f.write("{},{}\n".format(date, ret))

            # 3. Write and pin the artifact
            artifact_path = os.path.join(tmpdir, "toy_strategy.py")
            with open(artifact_path, "w") as f:
                f.write(_TOY_STRATEGY)
            pin = create_implementation_pin(artifact_path)

            # 4. Create the data adapter and hash
            adapter = CSVAdapter(csv_path)

            # 5. Create ledger
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            # 6. Create registry
            registry = Registry(os.path.join(tmpdir, "registry"))
            entry = {
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
            registry.register(entry)

            # === Execute pipeline ===

            # 7. Verify artifact pin (must pass before execution)
            verify_pin(pin["path"], pin["sha256"])

            # 8. Enforce block isolation (stage 1 with E block = OK)
            br.enforce_block_isolation(["test-2024-Q1"], stage=1)

            # 9. Load data
            data = adapter.load_returns()

            # 10. Run the artifact
            predictions, outcomes = load_and_run_artifact(
                artifact_path, data, entry["signal"]["parameters"]
            )

            # 11. Score the result
            ic = panel_ic(predictions, outcomes)
            returns = [r for _, r in data]

            # 12. Create ScoredResult (separate from Diagnostics)
            scored = ScoredResult(
                statistic="panel_ic",
                value=ic,
                trial_count=ledger.count_trials("test-universe"),
                metadata={"n": len(data)},
            )

            # Create Diagnostics (separate type)
            diag = Diagnostics()
            diag.add("per_date_ic", list(zip(
                [d for d, _ in data], predictions
            )))

            # 13. Power check
            power = compute_power_check(
                expected_sharpe=1.5,
                span_years=0.15,  # ~2 months
                trial_count=max(1, ledger.count_trials("test-universe")),
            )

            # 14. Create manifest
            manifest = create_eval_manifest(
                run_id="R-000001",
                code_hash=pin["sha256"],
                data_hash=adapter.data_hash,
                seed=42,
                registry_ref="int-test-001",
            )

            # 15. Append ledger row
            ledger.append_row(
                code_hash=pin["sha256"],
                data_hash=adapter.data_hash,
                universe="test-universe",
                kind="eval",
                blocks_touched=["test-2024-Q1"],
                stats_scored=["panel_ic"],
                registry_ref="int-test-001",
                outcome="ic={:.4f}".format(ic),
                stage=1,
                segment="E",
            )

            # === Verify everything ===

            # 16. Ledger chain is valid
            ok, errors = ledger.verify()
            assert ok, "Ledger chain invalid: {}".format(errors)

            # 17. Trial count incremented
            assert ledger.count_trials("test-universe") == 1

            # 18. ScoredResult is the right type
            assert isinstance(scored, ScoredResult)
            assert not isinstance(scored, Diagnostics)

            # 19. Diagnostics is the right type
            assert isinstance(diag, Diagnostics)
            assert not isinstance(diag, ScoredResult)

            # 20. Manifest has provenance
            assert manifest["code_hash"] == pin["sha256"]
            assert manifest["data_hash"] == adapter.data_hash
            assert manifest["registry_ref"] == "int-test-001"

            # 21. Power check produced a result
            assert "power" in power
            assert "flagged" in power

    def test_artifact_modification_blocks_pipeline(self):
        """Modifying the artifact after pinning blocks the entire pipeline."""
        import pytest
        from proofhouse.core.artifact_pin import create_implementation_pin, verify_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            # Pin the artifact
            artifact_path = os.path.join(tmpdir, "strategy.py")
            with open(artifact_path, "w") as f:
                f.write(_TOY_STRATEGY)
            pin = create_implementation_pin(artifact_path)

            # Modify the artifact (simulates using wrong implementation)
            with open(artifact_path, "a") as f:
                f.write("\n# oops, wrong version\n")

            # Pipeline cannot proceed
            with pytest.raises(ValueError, match="hash mismatch"):
                verify_pin(pin["path"], pin["sha256"])

    def test_v_block_in_stage1_blocks_pipeline(self):
        """V-block touch in stage-1 blocks the pipeline before eval."""
        import pytest
        from proofhouse.core.blocks import BlockRegistry

        br = BlockRegistry()
        br.add_block({
            "id": "validation-block",
            "start": "2024-07-01",
            "end": "2024-08-31",
            "segment": "V",
            "data_complete": True,
            "completed_at": "2024-09-15",
            "sources": ["test"],
            "seams": [],
        })

        with pytest.raises(ValueError, match="cannot be touched at stage 1"):
            br.enforce_block_isolation(["validation-block"], stage=1)

    def test_ledger_stage2_excludes_stage1(self):
        """Stage-2 count excludes stage-1 rows from the full pipeline."""
        from proofhouse.core.ledger import Ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            ledger = Ledger(os.path.join(tmpdir, "ledger.jsonl"))

            # Stage-1 exploration
            ledger.append_row(
                code_hash="sha256:s1a", data_hash="sha256:d1",
                universe="u", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )
            ledger.append_row(
                code_hash="sha256:s1b", data_hash="sha256:d2",
                universe="u", kind="explore",
                blocks_touched=["e-block"],
                stats_scored=["ic"],
                stage=1, segment="E",
            )

            # Stage-2 validation
            ledger.append_row(
                code_hash="sha256:s2", data_hash="sha256:d3",
                universe="u", kind="gate",
                blocks_touched=["v-block"],
                stats_scored=["ic"],
                stage=2, segment="V",
            )

            # Total = 3, Stage-2 correction count = 1
            assert ledger.count_trials("u") == 3
            assert ledger.count_trials_stage2("u") == 1
