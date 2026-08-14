# tests/test_manifest.py — tests for run manifests
"""
Medium 5: manifest.py was untested.
"A result without a manifest is not a result." (§5.3)
"""

import os
import tempfile

import pytest
import yaml

from proofhouse.core.manifest import create_manifest, save_manifest, VALID_EXECUTION_MODES


class TestManifest:
    """Tests for run manifest creation and persistence."""

    def test_create_manifest_required_fields(self):
        """create_manifest returns all required fields."""
        manifest = create_manifest(
            run_id="R-000001",
            code_hash="sha256:abc",
            data_hash="sha256:def",
        )
        assert manifest["run_id"] == "R-000001"
        assert manifest["code_hash"] == "sha256:abc"
        assert manifest["data_hash"] == "sha256:def"
        assert manifest["execution"] == "self-reported"
        assert "ts_utc" in manifest
        assert "host" in manifest
        assert manifest["wall_clock_seconds"] is None  # filled by caller

    def test_create_manifest_optional_fields(self):
        """Optional fields are stored when provided."""
        manifest = create_manifest(
            run_id="R-000002",
            code_hash="sha256:abc",
            data_hash="sha256:def",
            config_hash="sha256:cfg",
            seed=42,
            package_versions={"proofhouse": "0.1.0"},
            registry_ref="H-2026-001",
            execution="tool-executed",
        )
        assert manifest["config_hash"] == "sha256:cfg"
        assert manifest["seed"] == 42
        assert manifest["package_versions"] == {"proofhouse": "0.1.0"}
        assert manifest["registry_ref"] == "H-2026-001"
        assert manifest["execution"] == "tool-executed"

    def test_invalid_execution_mode_rejected(self):
        """Invalid execution mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid execution mode"):
            create_manifest(
                run_id="R-000003",
                code_hash="sha256:abc",
                data_hash="sha256:def",
                execution="auto-magic",
            )

    def test_save_manifest_round_trip(self):
        """Manifest saves to disk as YAML and is reloadable."""
        manifest = create_manifest(
            run_id="R-000004",
            code_hash="sha256:abc",
            data_hash="sha256:def",
        )

        tmpdir = tempfile.mkdtemp()
        save_manifest(manifest, tmpdir)

        path = os.path.join(tmpdir, "R-000004.yaml")
        assert os.path.exists(path)

        with open(path) as f:
            loaded = yaml.safe_load(f)

        assert loaded["run_id"] == "R-000004"
        assert loaded["code_hash"] == "sha256:abc"

    def test_valid_execution_modes(self):
        """Both valid execution modes are accepted."""
        for mode in VALID_EXECUTION_MODES:
            manifest = create_manifest(
                run_id="R-test",
                code_hash="sha256:abc",
                data_hash="sha256:def",
                execution=mode,
            )
            assert manifest["execution"] == mode
