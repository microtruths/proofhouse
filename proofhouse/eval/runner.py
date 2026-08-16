# proofhouse.eval.runner — eval runner
"""
Phase 1 Build Plan §1a — Eval Runner.

The integration point: takes data + code + entry → scored result
(separate from diagnostics) with a manifest.

Key behaviors:
1. Verifies artifact pin before execution (wall, not tripwire).
2. Passes signal.parameters from entry to artifact as arguments.
3. Returns (ScoredResult, Diagnostics) as separate types.
4. Creates a manifest with full provenance.
5. Appends a ledger row with stage and segment fields.

Parent plan §5.3:
    "A result without a manifest is not a result."
"""

import importlib.util
import platform
from datetime import datetime, timezone


def verify_artifact_pin(artifact_path, expected_hash):
    """Verify the artifact file hash matches the registered pin.

    This gates execution, not just detection. If the hash doesn't match,
    the runner refuses to proceed. This is the #26 wall: recording the
    pin catches edits; gating execution on the pin makes running a second
    implementation impossible.

    Delegates to proofhouse.core.artifact_pin.verify_pin — that module
    is the canonical implementation. This function exists so callers can
    import from runner without knowing about the pin module.

    Args:
        artifact_path: Path to the artifact file.
        expected_hash: Expected hash in 'sha256:<hex>' format.

    Raises:
        FileNotFoundError: If the artifact file does not exist.
        ValueError: If the hash does not match.
    """
    from proofhouse.core.artifact_pin import verify_pin
    verify_pin(artifact_path, expected_hash)


def load_and_run_artifact(artifact_path, data, parameters):
    """Load a strategy artifact and execute it with parameters from the entry.

    The artifact must define a `run(data, parameters)` function. Parameters
    come from `signal.parameters` in the registry entry — the artifact reads
    them as arguments, not as embedded constants. This makes polarity drift
    unrepresentable.

    Args:
        artifact_path: Path to the Python artifact.
        data: Data to pass to the strategy (list of (date, value) tuples).
        parameters: Dict of parameters from signal.parameters.

    Returns:
        The return value of the artifact's run() function.
    """
    spec = importlib.util.spec_from_file_location("strategy", artifact_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    if not hasattr(module, "run"):
        raise ValueError(
            "Artifact {} does not define a run(data, parameters) function".format(
                artifact_path
            )
        )

    return module.run(data, parameters)


def create_eval_manifest(run_id, code_hash, data_hash, seed=None,
                         config_hash=None, package_versions=None,
                         registry_ref=None, execution="tool-executed"):
    """Create a manifest for an eval run.

    Parent plan §5.3:
        "code hash, data hash, config hash, seed, package versions,
         registry ref, ledger row id, wall-clock, host,
         execution: self-reported | tool-executed."

    Args:
        run_id: Ledger run_id.
        code_hash: SHA-256 of the artifact.
        data_hash: SHA-256 of the data snapshot.
        seed: Random seed used.
        config_hash: Optional config hash.
        package_versions: Optional dict of package versions.
        registry_ref: Entry ID.
        execution: 'self-reported' or 'tool-executed'.

    Returns:
        Dict representing the manifest.
    """
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
        "wall_clock_seconds": None,  # filled by caller after run completes
    }
