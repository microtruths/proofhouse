# tests/test_artifact_pin.py — Artifact pinning tests
"""
Phase 1 Build Plan §1b — Artifact pin tests.

Exit criterion: Runner refuses execution when hash ≠ pin.
"""

import hashlib
import os
import tempfile


def _write_artifact(tmpdir, code, name="strategy.py"):
    """Write a strategy artifact and return (path, sha256)."""
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(code)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return path, "sha256:" + digest


_SAMPLE_CODE = '''
def run(data, parameters):
    polarity = parameters.get("polarity", 1)
    return [r * polarity for _, r in data]
'''


class TestArtifactPin:
    """Artifact pinning and execution gating."""

    def test_pin_creation(self):
        """create_implementation_pin returns path and hash."""
        from proofhouse.core.artifact_pin import create_implementation_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            path, expected_hash = _write_artifact(tmpdir, _SAMPLE_CODE)
            pin = create_implementation_pin(path)

            assert pin["path"] == path
            assert pin["sha256"] == expected_hash

    def test_verify_correct_hash(self):
        """verify_pin passes when hash matches."""
        from proofhouse.core.artifact_pin import verify_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            path, correct_hash = _write_artifact(tmpdir, _SAMPLE_CODE)
            # Should not raise
            verify_pin(path, correct_hash)

    def test_verify_wrong_hash_refused(self):
        """verify_pin refuses execution when hash != pin.

        This is the exit criterion: modify the artifact file,
        runner raises before any code runs.
        """
        import pytest
        from proofhouse.core.artifact_pin import verify_pin

        with tempfile.TemporaryDirectory() as tmpdir:
            path, correct_hash = _write_artifact(tmpdir, _SAMPLE_CODE)

            # Modify the artifact (simulates using a different file)
            with open(path, "a") as f:
                f.write("\n# unauthorized modification\n")

            with pytest.raises(ValueError, match="hash mismatch"):
                verify_pin(path, correct_hash)

    def test_missing_artifact_refused(self):
        """verify_pin refuses when artifact file doesn't exist."""
        import pytest
        from proofhouse.core.artifact_pin import verify_pin

        with pytest.raises(FileNotFoundError):
            verify_pin("/nonexistent/strategy.py", "sha256:abc")

    def test_polarity_flip_detected_via_hash(self):
        """A polarity flip changes the hash, which verify_pin catches.

        This is the actual #26 failure mode: the polarity was wrong
        because a different file was used. With hash pinning, any
        modification is caught.
        """
        import pytest
        from proofhouse.core.artifact_pin import (
            create_implementation_pin, verify_pin,
        )

        original_code = '''
def run(data, parameters):
    polarity = parameters.get("polarity", 1)
    return [r * polarity for _, r in data]
'''
        flipped_code = '''
def run(data, parameters):
    polarity = parameters.get("polarity", -1)
    return [r * polarity for _, r in data]
'''

        with tempfile.TemporaryDirectory() as tmpdir:
            # Pin the original
            path, _ = _write_artifact(tmpdir, original_code)
            pin = create_implementation_pin(path)

            # Replace with the flipped version
            with open(path, "w") as f:
                f.write(flipped_code)

            # verify_pin catches the change
            with pytest.raises(ValueError, match="hash mismatch"):
                verify_pin(pin["path"], pin["sha256"])

    def test_parameters_from_entry_not_constants(self):
        """Artifacts receive parameters from the entry.

        With parameters passed as arguments, there are no embedded
        constants to drift. Polarity is in signal.parameters.
        """
        from proofhouse.eval.runner import _load_and_run_artifact as load_and_run_artifact

        code = '''
def run(data, parameters):
    polarity = parameters["polarity"]
    return [r * polarity for _, r in data]
'''
        with tempfile.TemporaryDirectory() as tmpdir:
            path, _ = _write_artifact(tmpdir, code)

            data = [("2024-01-01", 0.05)]

            # Polarity comes from the entry, not the code
            result_pos = load_and_run_artifact(path, data, {"polarity": 1})
            result_neg = load_and_run_artifact(path, data, {"polarity": -1})

            assert result_pos == [0.05]
            assert result_neg == [-0.05]
