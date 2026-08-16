# proofhouse.core.artifact_pin — named-artifact pinning and execution gating
"""
Phase 1 Build Plan §1b — Artifact Pinning.

Per failure catalog #26: "a registered definition must name the single
implementing artifact."

In #26, the registered artifact was never modified — replicate_behavioral_ic.py
was correct; a DIFFERENT file built the cohorts. Hash-pinning alone would
report it clean.

Two design choices make this structural rather than merely detectable:

1. Bind execution to the pin. The runner refuses to execute any artifact
   whose hash ≠ the registered pin. Recording catches edits; gating
   execution makes running a second implementation impossible.

2. Artifacts read parameters from the entry, not from embedded constants.
   If the artifact takes parameters as an argument sourced from the registry,
   there is no constant to extract and nothing to diff. Polarity drift
   becomes unrepresentable.
"""

import hashlib
import os


def compute_artifact_hash(artifact_path):
    """Compute the SHA-256 hash of an artifact file.

    Args:
        artifact_path: Path to the artifact file.

    Returns:
        str: 'sha256:<hex>'

    Raises:
        FileNotFoundError: If the artifact does not exist.
    """
    if not os.path.exists(artifact_path):
        raise FileNotFoundError(
            "Artifact not found: {}".format(artifact_path)
        )

    with open(artifact_path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return "sha256:" + digest


def create_implementation_pin(artifact_path):
    """Create a signal.implementation pin for a registry entry.

    Returns a dict suitable for storing in signal.implementation:
        {"path": "<artifact_path>", "sha256": "sha256:<hex>"}

    Args:
        artifact_path: Path to the artifact file.

    Returns:
        Dict with 'path' and 'sha256' keys.
    """
    return {
        "path": artifact_path,
        "sha256": compute_artifact_hash(artifact_path),
    }


def verify_pin(artifact_path, expected_hash):
    """Verify an artifact's hash matches the registered pin.

    This gates execution, not just detection. If the hash doesn't match,
    execution is refused. This is a wall, not a tripwire.

    Args:
        artifact_path: Path to the artifact file.
        expected_hash: Expected hash in 'sha256:<hex>' format.

    Raises:
        FileNotFoundError: If artifact does not exist.
        ValueError: If hash does not match.
    """
    actual_hash = compute_artifact_hash(artifact_path)

    if actual_hash != expected_hash:
        raise ValueError(
            "Artifact hash mismatch — execution refused.\n"
            "  Path:     {}\n"
            "  Expected: {}\n"
            "  Actual:   {}\n"
            "The registered artifact has been modified or a different file "
            "is being used.".format(artifact_path, expected_hash, actual_hash)
        )
