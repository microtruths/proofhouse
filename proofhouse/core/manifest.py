# proofhouse.core.manifest — run manifests
"""
Run manifests for proofhouse.

Phase 0 Build Plan §4.4:
    "code hash, data hash, config hash, seed, package versions,
     registry ref, ledger row id, wall-clock, host,
     execution: self-reported | tool-executed."

    "A result without a manifest is not a result."
"""

import json
import os
import platform
import time
from datetime import datetime, timezone


# Valid execution modes
VALID_EXECUTION_MODES = frozenset({"self-reported", "tool-executed"})


def create_manifest(
    run_id,
    code_hash,
    data_hash,
    config_hash=None,
    seed=None,
    package_versions=None,
    registry_ref=None,
    execution="self-reported",
):
    """Create a run manifest capturing full provenance.

    Args:
        run_id: The ledger run_id this manifest corresponds to.
        code_hash: SHA-256 of the code that ran.
        data_hash: SHA-256 of the data snapshot.
        config_hash: Optional SHA-256 of the config.
        seed: Optional random seed.
        package_versions: Optional dict of {package: version}.
        registry_ref: Optional entry ID this run is linked to.
        execution: 'self-reported' or 'tool-executed'.

    Returns:
        Dict representing the manifest.
    """
    if execution not in VALID_EXECUTION_MODES:
        raise ValueError(
            "Invalid execution mode '{}'. Must be one of: {}".format(
                execution, ", ".join(sorted(VALID_EXECUTION_MODES))
            )
        )

    return {
        "run_id": run_id,
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_hash": code_hash,
        "data_hash": data_hash,
        "config_hash": config_hash,
        "seed": seed,
        "package_versions": package_versions or {},
        "registry_ref": registry_ref,
        "execution": execution,
        "host": platform.node(),
        "wall_clock_seconds": None,  # filled by caller
    }


def save_manifest(manifest, manifests_dir):
    """Save a manifest to disk as YAML.

    Args:
        manifest: Dict from create_manifest.
        manifests_dir: Path to the manifests/ directory.
    """
    import yaml

    os.makedirs(manifests_dir, exist_ok=True)
    fname = "{}.yaml".format(manifest["run_id"])
    path = os.path.join(manifests_dir, fname)
    with open(path, "w") as f:
        yaml.dump(manifest, f, default_flow_style=False, sort_keys=True)
