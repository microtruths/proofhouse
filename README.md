# proofhouse

Personal research harness — makes it mechanically hard to fool yourself while looking for edge.

## What this is

A research substrate that enforces declaration-time constraints on hypotheses. Registration either is or isn't well-formed; whether a hypothesis passes is a separate concern (Phase 2).

**Phase 0 delivers:** record schema, hash chain, block registry, ledger, registry with 6 enforced checks + 10 schema validators, and CLI.

## Quick start

```bash
# Install
pip install -e ".[dev]"

# Initialize a project
proofhouse init

# Log an exploration run (<5 seconds)
proofhouse explore --universe hl-perps-top20

# Register a hypothesis
proofhouse register fixtures/agentcopy_v10_ic_gate.yaml

# Check status
proofhouse status
```

## CLI commands

| Command | Description |
|---------|-------------|
| `proofhouse init` | Create project directories and genesis records |
| `proofhouse blocks add\|list\|complete\|promote` | Manage the block registry |
| `proofhouse explore -u <universe>` | Log an exploration run (fast — <5s) |
| `proofhouse register <file>` | Validate, hash, chain, and register a hypothesis |
| `proofhouse status` | Show trial counts, block consumption, pending fields |

## Architecture

```
proofhouse/
  schema/          # Record types, validation, schema versioning
    models.py      # Entry schema with full field set
    validate.py    # 6 enforced checks (E1–E6) + 10 validators (V1–V10)
    versions.py    # SCHEMA_VERSION = 1
  chain/           # Hash chain (no imports from core/)
    canonical.py   # Deterministic JSON serialization
    hashing.py     # SHA-256 content hashing
    chain.py       # prev-pointer construction + verification
  core/            # Registry, ledger, blocks, manifests
    registry.py    # Append-only hypothesis store
    ledger.py      # Multiple-testing counter (per-universe)
    blocks.py      # Block registry + consumption tracking
    manifest.py    # Run provenance
  cli/
    main.py        # Click-based CLI
```

**Import direction:** `schema/` and `chain/` never import from `core/`. This is enforced by test S-1 (AST scan), keeping a future verifier package split cheap.

## Enforced checks

| # | Check | Rule |
|---|-------|------|
| E1 | Gate combination | Required, no default |
| E2 | Threshold anti-circularity | Derived source_blocks may not have segment V |
| E3 | Diagnostics isolation | Gate and Diagnostics structurally separate |
| E4 | Block consumption | V-blocks single-use per hypothesis |
| E5 | Pass licensing | pass_licenses + pass_does_not_establish required, ≤200 chars |
| E6 | Block availability | data_complete required; seams + V requires acknowledgment |

## Tests

```bash
# Run all tests
python -m pytest tests/ -v

# 76 tests covering:
#   C-1…C-5:  Canonical serialization (byte-stability)
#   H-1…H-4:  Hash chain (construction + tamper detection)
#   B-1…B-4:  Block registry (consumption, seams, promotion)
#   L-1…L-5:  Ledger (dedup, maintenance, per-universe, overlap, write timing)
#   V-1…V-10: Validation (all schema checks)
#   S-1:      Structure (import direction enforcement)
```

## License

Apache-2.0
