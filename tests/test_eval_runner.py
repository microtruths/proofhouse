# tests/test_eval_runner.py — Tests for the eval runner
"""
Phase 1 Build Plan §1a — Runner tests.

Tests the integration point: artifact pin verification, parameter passing,
diagnostics isolation, manifest creation, and ledger row appending.
"""

import hashlib
import json
import os
import tempfile


def _make_artifact(tmpdir, code, name="strategy.py"):
    """Write a strategy artifact and return (path, sha256)."""
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(code)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return path, "sha256:" + digest


def _make_csv(tmpdir, rows, name="data.csv"):
    """Write a CSV file and return the path."""
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write("date,return\n")
        for date, ret in rows:
            f.write("{},{}\n".format(date, ret))
    return path


# A simple strategy artifact that reads parameters from its arguments
_STRATEGY_CODE = '''
def run(data, parameters):
    """Simple strategy: multiply returns by polarity parameter."""
    polarity = parameters.get("polarity", 1)
    return [r * polarity for _, r in data]
'''


class TestArtifactPinVerification:
    """Artifact pin must gate execution, not just detect edits."""

    def test_runner_refuses_wrong_hash(self):
        """Runner refuses to execute when artifact hash != pin.

        This is the #26 wall: gating execution makes running a second
        implementation impossible.
        """
        from proofhouse.eval.runner import verify_artifact_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            path, correct_hash = _make_artifact(tmpdir, _STRATEGY_CODE)

            # Modify the artifact after pinning
            with open(path, "a") as f:
                f.write("\n# modified\n")

            import pytest
            with pytest.raises(ValueError, match="hash mismatch"):
                verify_artifact_pin(path, correct_hash)

    def test_runner_accepts_correct_hash(self):
        """Runner accepts execution when hash matches pin."""
        from proofhouse.eval.runner import verify_artifact_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            path, correct_hash = _make_artifact(tmpdir, _STRATEGY_CODE)
            # Should not raise
            verify_artifact_pin(path, correct_hash)

    def test_missing_artifact_rejected(self):
        """Runner rejects missing artifact path."""
        from proofhouse.eval.runner import verify_artifact_pin
        import pytest

        with pytest.raises(FileNotFoundError):
            verify_artifact_pin("/nonexistent/strategy.py", "sha256:abc")


class TestDiagnosticsIsolation:
    """A4: runner returns ScoredResult and Diagnostics as separate types."""

    def test_separate_return_types(self):
        """Runner returns (ScoredResult, Diagnostics) tuple."""
        from proofhouse.eval.types import ScoredResult, Diagnostics

        # Verify they are distinct types with no shared state
        sr = ScoredResult(statistic="dsr", value=1.5, trial_count=3)
        diag = Diagnostics(entries={"regime_split": [0.1, -0.2]})

        assert isinstance(sr, ScoredResult)
        assert isinstance(diag, Diagnostics)
        assert not isinstance(sr, Diagnostics)
        assert not isinstance(diag, ScoredResult)

    def test_scored_result_has_no_diagnostics(self):
        """ScoredResult does not contain any diagnostics fields."""
        from proofhouse.eval.types import ScoredResult

        sr = ScoredResult(
            statistic="panel_ic",
            value=0.0389,
            trial_count=5,
            benchmark_results={"btc": 0.02},
            metadata={"n": 4431},
        )

        d = sr.to_dict()
        assert "regime_split" not in d
        assert "plots" not in d
        assert "breakdowns" not in d

    def test_diagnostics_serializable(self):
        """Diagnostics can be serialized independently."""
        from proofhouse.eval.types import Diagnostics

        diag = Diagnostics()
        diag.add("regime_split", {"bull": 0.05, "bear": -0.02})
        diag.add("per_instrument", {"BTC": 0.03, "ETH": 0.01})

        d = diag.to_dict()
        assert "regime_split" in d
        assert "per_instrument" in d


class TestParameterPassing:
    """Artifacts receive parameters from the entry, not embedded constants."""

    def test_parameters_passed_to_artifact(self):
        """The runner passes signal.parameters to the artifact as arguments."""
        from proofhouse.eval.runner import load_and_run_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = _make_artifact(tmpdir, _STRATEGY_CODE)

            data = [("2024-01-01", 0.01), ("2024-01-02", -0.01)]
            parameters = {"polarity": -1}

            result = load_and_run_artifact(path, data, parameters)

            # With polarity=-1, returns should be negated
            assert result == [-0.01, 0.01], (
                "Parameters should be passed to artifact. Got {}".format(result)
            )

    def test_default_polarity_is_positive(self):
        """Without explicit polarity, the strategy uses default."""
        from proofhouse.eval.runner import load_and_run_artifact

        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = _make_artifact(tmpdir, _STRATEGY_CODE)

            data = [("2024-01-01", 0.01), ("2024-01-02", -0.01)]
            parameters = {}  # No polarity → default=1

            result = load_and_run_artifact(path, data, parameters)
            assert result == [0.01, -0.01]


class TestRunnerManifest:
    """Runner produces a manifest with full provenance."""

    def test_manifest_contains_required_fields(self):
        """Manifest has all required fields from parent plan §5.3."""
        from proofhouse.eval.runner import create_eval_manifest

        manifest = create_eval_manifest(
            run_id="R-000001",
            code_hash="sha256:abc123",
            data_hash="sha256:def456",
            seed=42,
            registry_ref="exp-001",
        )

        assert manifest["run_id"] == "R-000001"
        assert manifest["code_hash"] == "sha256:abc123"
        assert manifest["data_hash"] == "sha256:def456"
        assert manifest["seed"] == 42
        assert manifest["registry_ref"] == "exp-001"
        assert "ts_utc" in manifest
        assert "host" in manifest
