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

from proofhouse.chain.canonical import decimal_str
from proofhouse.chain.hashing import content_hash


# Valid execution modes
VALID_EXECUTION_MODES = frozenset({"self-reported", "tool-executed"})

# Excluded from the manifest digest, and only this.
#
#   run_id — assigned by the ledger append that CARRIES the digest, so it
#       cannot be inside the payload the digest is computed over. It is not
#       unpinned by the exclusion: the ledger row's own run_id and the
#       manifest's filename both name it, and the row's hash chain covers
#       both.
_DIGEST_EXCLUDED = frozenset({"run_id"})


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


def _hashable(value):
    """A copy of `value` with every float rendered as a canonical decimal.

    WHY THIS EXISTS AT ALL. Canonical rule 3 forbids floats in hashed
    records, so `canonical_bytes` raises on one. A manifest is not written
    under that rule end to end: `ScoredResult.to_dict` canonicalizes the
    fields that are numeric BY CONTRACT and deliberately leaves `metadata`
    alone, because canonicalizing an open dict is irreversible on read —
    there is no way to tell a canonicalized 0.015 from a value that was
    genuinely the string "0.015". `wall_clock_seconds` is a float for the
    same reason: nothing compares it to anything.

    So the manifest cannot be hashed as it sits, and the choice is between
    no digest at all over those fields and a digest over a float-normalized
    view of them. This is the second: for HASHING ONLY, floats are rendered
    with the same `decimal_str` rule the stored fields already use, which
    keeps the digest reproducible by any independent verifier following the
    written rule.

    The residual is stated rather than hidden: inside an uncanonicalized
    dict, the float 0.015 and the string "0.015" produce the same digest,
    so the digest does not distinguish them. That is acceptable exactly
    where it applies — metadata is non-decisional, and any value a gate
    compares against a threshold now lives in `secondary_statistics`, which
    is canonicalized at rest. It would NOT be acceptable for a gating field,
    which is why none of them are hashed through this path.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, float):
        return decimal_str(value)
    if isinstance(value, dict):
        return {k: _hashable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_hashable(v) for v in value]
    return value


def result_digest(result):
    """Content hash of a manifest's `result` block — the numbers themselves.

    Kept separate from the whole-manifest digest because it is the one a
    verdict is rendered against. A gate row cites this digest, so "the
    verdict was computed from these exact numbers" is checkable without
    trusting that the rest of the manifest (host, wall clock, package
    versions) never changed.

    Args:
        result: The dict from ScoredResult.to_dict.

    Returns:
        str: 'sha256:<hex>', or None when there is no result to hash —
        absent is reported as absent, never as the hash of an empty dict.
    """
    if not result:
        return None
    return content_hash(_hashable(result))


def manifest_digest(manifest):
    """Content hash of the whole manifest, run_id excluded.

    §5.3 said "a result without a manifest is not a result" and the manifest
    was then written as plain YAML — unhashed, unchained, referenced by
    nothing. Editing it changed what `report` printed while every hash chain
    still verified clean. This digest is what puts the file inside the chain:
    the eval ledger row stores it, the row is hashed and chained, so altering
    the manifest afterwards contradicts a record that cannot be altered
    without breaking every row after it.

    Args:
        manifest: The manifest dict.

    Returns:
        str: 'sha256:<hex>'.
    """
    payload = {k: v for k, v in (manifest or {}).items()
               if k not in _DIGEST_EXCLUDED}
    return content_hash(_hashable(payload))


def latest_manifest_for(manifests_dir, entry_id):
    """Most recent manifest recording a result for this entry.

    Manifests are named by run_id, which is monotonic, so the highest run_id
    is the latest run. Only manifests carrying a `result` are eligible — one
    without a result predates Phase 2b and cannot be gated on.

    THIS IS THE UNBOUND LOOKUP. It answers "which manifest looks newest",
    which is what `report` used to render verdicts from — and why editing a
    manifest changed the printed verdict. Bound paths resolve a specific
    run_id from a ledger row instead; this remains only for the legacy path,
    which must disclose that it took it.
    """
    import yaml

    if not manifests_dir or not os.path.isdir(manifests_dir):
        return None
    best = None
    for fname in sorted(os.listdir(manifests_dir)):
        if not fname.endswith(".yaml"):
            continue
        with open(os.path.join(manifests_dir, fname)) as fh:
            manifest = yaml.safe_load(fh) or {}
        if manifest.get("registry_ref") != entry_id:
            continue
        if not manifest.get("result"):
            continue
        if best is None or manifest.get("run_id", "") > best.get("run_id", ""):
            best = manifest
    return best


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
