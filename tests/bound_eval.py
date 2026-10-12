# tests/bound_eval.py — fabricate an eval run that is BOUND to its result
"""
Shared fixture builder for the result→verdict binding.

Three test modules (gate_run, verify, report) need a ledger row and a
manifest that agree with each other the way `run_eval` writes them. This
builds one, THROUGH THE REAL WRITERS — `result_digest`, `manifest_digest` and
`save_manifest` — rather than restating what they produce. A test fixture
that computes digests its own way can drift from the code under test and
then agree with itself forever, which is the shape of a check that cannot
fail.

`run_eval` itself is not usable here: it needs an artifact file on disk, a
data adapter, a block registry and a pinned sha256, none of which any of
these tests are about.
"""

import os
import tempfile

from proofhouse.core.ledger import Ledger
from proofhouse.core.manifest import (manifest_digest, result_digest,
                                      save_manifest)


def project():
    """A throwaway project: (Ledger, manifests_dir)."""
    root = tempfile.mkdtemp()
    return (Ledger(os.path.join(root, "ledger.jsonl")),
            os.path.join(root, "manifests"))


def reload(ledger):
    """The same ledger read back off disk, to verify the chain survived."""
    return Ledger(ledger._path)


def record_eval(ledger, manifests_dir, entry, scored,
                code_hash="c1", data_hash="d1", kind="eval",
                registry_ref=None, digests=True, scoring="pinned"):
    """Record an eval run the way run_eval does, and return its run_id.

    Args:
        digests: False writes a LEGACY row — one from before the manifest
            digest existed. Not a synthetic case: R-000012 is exactly this,
            and 'unverifiable' has to be demonstrable against it.
        scoring: "pinned" writes the scoring block run_eval writes when the
            score function came from the pinned artifact; "caller" the
            attested-only form; None omits the block, which is what every
            manifest written before the score-function pin looks like.
    """
    scoring_block = None
    if scoring == "pinned":
        scoring_block = {"source": "artifact", "code_hash": code_hash}
    elif scoring == "caller":
        scoring_block = {"source": "caller", "code_hash": None}
    manifest = {
        "run_id": None,
        "ts_utc": "2026-08-19T12:00:00Z",
        "code_hash": code_hash,
        "data_hash": data_hash,
        "config_hash": None,
        "seed": None,
        "package_versions": {},
        "registry_ref": registry_ref or entry.get("id"),
        "execution": "tool-executed",
        "host": "test",
        "wall_clock_seconds": 1.5,
        "result": scored.to_dict(),
    }
    if scoring_block is not None:
        manifest["scoring"] = scoring_block

    ledger.append_row(
        code_hash=code_hash,
        data_hash=data_hash,
        universe=(entry.get("universe") or {}).get("id"),
        kind=kind,
        blocks_touched=list(entry.get("validation_blocks") or []),
        stats_scored=sorted({scored.statistic}
                            | set(scored.secondary_statistics)),
        registry_ref=registry_ref or entry.get("id"),
        outcome="{}={}".format(scored.statistic, scored.value),
        result_hash=result_digest(manifest["result"]) if digests else None,
        manifest_hash=manifest_digest(manifest) if digests else None,
    )

    run_id = ledger.all_rows()[-1]["run_id"]
    manifest["run_id"] = run_id
    save_manifest(manifest, manifests_dir)
    return run_id


def tamper(manifests_dir, run_id, **fields):
    """Edit a saved manifest's `result` in place, as an attacker would.

    The whole change is measured against this: before it, editing a
    manifest changed what `report` printed and every hash chain still
    verified clean.
    """
    import yaml

    path = os.path.join(manifests_dir, "{}.yaml".format(run_id))
    with open(path) as fh:
        manifest = yaml.safe_load(fh)
    manifest["result"].update(fields)
    with open(path, "w") as fh:
        yaml.dump(manifest, fh, default_flow_style=False, sort_keys=True)
    return path
