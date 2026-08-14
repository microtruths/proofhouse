#!/usr/bin/env python3
"""Integration test: retro-file both fixtures end-to-end.

Exit criteria §8:
  1. AgentCopy v10 retro-files through register → hash → chain-verify → status
  2. Tasteprint retro-files with no schema hacks (domain-agnosticism test)
"""

import os
import sys
import tempfile
import shutil

import yaml

# Add the project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.schema.validate import ValidationError


def main():
    # Work in a temp directory
    tmpdir = tempfile.mkdtemp(prefix="proofhouse_retro_")
    print("Working in: {}".format(tmpdir))

    reg_dir = os.path.join(tmpdir, "registry")
    ledger_path = os.path.join(tmpdir, "ledger.jsonl")

    # Create a registry
    registry = Registry(reg_dir)

    # Load and register AgentCopy v10 fixture
    fixtures_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures")

    print("\n--- Retro-filing AgentCopy v10 IC Gate ---")
    with open(os.path.join(fixtures_dir, "agentcopy_v10_ic_gate.yaml")) as f:
        ac_entry = yaml.safe_load(f)

    result_ac = registry.register(ac_entry)
    print("  Registered: {}".format(result_ac["id"]))
    print("  Hash: {}".format(result_ac["hash"]))
    print("  Status: {}".format(result_ac["status"]))
    print("  Mechanism: {}".format(result_ac["mechanism"]["class"]))
    assert "prev" not in result_ac, "First entry should be genesis (no prev)"
    print("  ✓ Genesis record (no prev)")

    # Check pending fields
    pc = result_ac.get("power_check", {})
    val = pc.get("value", {})
    assert val.get("pending") is True
    assert val.get("fill_rule") is not None
    print("  ✓ power_check is pending with fill_rule pinned")

    # Retro-file Tasteprint
    print("\n--- Retro-filing Tasteprint Experiment ---")
    with open(os.path.join(fixtures_dir, "tasteprint_experiment.yaml")) as f:
        tp_entry = yaml.safe_load(f)

    result_tp = registry.register(tp_entry)
    print("  Registered: {}".format(result_tp["id"]))
    print("  Hash: {}".format(result_tp["hash"]))
    print("  Status: {}".format(result_tp["status"]))
    print("  Mechanism: {}".format(result_tp["mechanism"]["class"]))
    assert result_tp["prev"] == result_ac["hash"], "Second entry should chain to first"
    print("  ✓ Chained to AgentCopy (prev = AC hash)")

    # Verify the chain
    print("\n--- Chain Verification ---")
    ok, errors = registry.verify()
    assert ok, "Chain verification failed: {}".format(errors)
    print("  ✓ Chain verified ({} entries, 0 errors)".format(len(registry.list_entries())))

    # Reload from disk and re-verify
    print("\n--- Reload from Disk ---")
    registry2 = Registry(reg_dir)
    entries = registry2.list_entries()
    assert len(entries) == 2
    print("  ✓ {} entries loaded from disk".format(len(entries)))

    ok2, errors2 = registry2.verify()
    assert ok2, "Chain verification after reload failed: {}".format(errors2)
    print("  ✓ Chain verified after reload")

    # Summary
    print("\n=== Retro-filing Complete ===")
    print("Both fixtures registered, hashed, chained, and verified.")
    print("  - AgentCopy v10 (failed IC gate): {}".format(result_ac["id"]))
    print("  - Tasteprint (non-crypto): {}".format(result_tp["id"]))
    print("  - No schema hacks needed for Tasteprint (domain-agnosticism ✓)")

    # Cleanup
    shutil.rmtree(tmpdir)
    print("\nAll exit criteria for retro-filing met.")


if __name__ == "__main__":
    main()
