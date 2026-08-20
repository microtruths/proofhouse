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

class TestTheManifestIsHashed:
    """§5.3 said "a result without a manifest is not a result" and then wrote
    the manifest as plain YAML: unhashed, unchained, referenced by nothing.
    Editing R-000012.yaml changed what `proofhouse report` printed and every
    hash chain still verified clean. These digests are what put the file
    inside the chain — the eval ledger row stores them.
    """

    def _manifest(self):
        return {
            "run_id": "R-000012",
            "ts_utc": "2026-08-20T00:44:24Z",
            "code_hash": "sha256:abc",
            "data_hash": "sha256:def",
            "config_hash": None,
            "seed": None,
            "package_versions": {},
            "registry_ref": "H-2026-PFR4",
            "execution": "tool-executed",
            "host": "MacBook-Pro-9.local",
            "wall_clock_seconds": 3.362,
            "result": {
                "statistic": "clustered_t_stat",
                "value": "1.8617105276654804",
                "trial_count": 1,
                "p_value": None,
                "benchmark_results": {},
                "secondary_statistics": {
                    "mean_net_bps_per_event": "46.420037737456951"},
                "metadata": {"n_events": 943, "round_trip_cost_bps": 18.64},
            },
        }

    def test_a_digest_is_reproducible_from_the_file_on_disk(self):
        """The whole mechanism: the digest recorded before the file was
        written must recompute from the file after a YAML round-trip. If it
        does not, every gate is refused for the wrong reason."""
        from proofhouse.core.manifest import manifest_digest, result_digest

        manifest = self._manifest()
        expected_result = result_digest(manifest["result"])
        expected_manifest = manifest_digest(manifest)

        tmpdir = tempfile.mkdtemp()
        save_manifest(manifest, tmpdir)
        with open(os.path.join(tmpdir, "R-000012.yaml")) as fh:
            reloaded = yaml.safe_load(fh)

        assert result_digest(reloaded["result"]) == expected_result
        assert manifest_digest(reloaded) == expected_manifest

    def test_editing_the_result_changes_the_digest(self):
        from proofhouse.core.manifest import manifest_digest, result_digest

        before = self._manifest()
        after = self._manifest()
        after["result"]["value"] = "2.9617105276654804"

        assert result_digest(before["result"]) != result_digest(after["result"])
        assert manifest_digest(before) != manifest_digest(after)

    def test_editing_metadata_changes_the_manifest_digest(self):
        """metadata is not canonicalized AT REST — it cannot be, reversibly.
        It is still hashed, through a float-normalized view, so an edit to it
        is detected even though no verdict turns on it."""
        from proofhouse.core.manifest import manifest_digest

        before = self._manifest()
        after = self._manifest()
        after["result"]["metadata"]["n_events"] = 944

        assert manifest_digest(before) != manifest_digest(after)

    def test_provenance_is_covered_too(self):
        """A result is a measurement OF something. Swap the data hash and the
        numbers describe a different window."""
        from proofhouse.core.manifest import manifest_digest, result_digest

        before = self._manifest()
        after = self._manifest()
        after["data_hash"] = "sha256:another-window"

        assert result_digest(before["result"]) == result_digest(after["result"])
        assert manifest_digest(before) != manifest_digest(after)

    def test_run_id_is_excluded_and_why(self):
        """run_id is assigned by the ledger append that CARRIES the digest,
        so it cannot be inside the payload the digest covers. It is not left
        unpinned: the row's own run_id and the manifest's filename both name
        it, and the row is chained."""
        from proofhouse.core.manifest import manifest_digest

        before = self._manifest()
        after = dict(self._manifest(), run_id=None)
        assert manifest_digest(before) == manifest_digest(after)

    def test_an_absent_result_hashes_to_nothing_not_to_empty(self):
        """A manifest with no result predates Phase 2b. Reporting None says
        so; hashing {} would give it a digest that looks like a record."""
        from proofhouse.core.manifest import result_digest

        assert result_digest(None) is None
        assert result_digest({}) is None
