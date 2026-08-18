# proofhouse

Personal research harness — makes it mechanically hard to fool yourself while looking for edge.

## What it's for

When you test a lot of ideas against the same data, the best-looking one is usually
lucky rather than good. You cannot tell from the inside, because every step that
inflates a result feels reasonable while you're doing it. One more variant. A threshold
nudged slightly. A bad month that "doesn't really count."

proofhouse makes you write down what you're testing and what would count as success
*before* you look. It counts every test you run, including the ones you abandon. It
refuses to let you test on data you've already spent. Then it tells you pass or fail
against what you wrote, with no room left to reinterpret.

It will not find you an edge and has no opinion on whether your idea is good. It only
makes fooling yourself expensive and visible.

The difference it buys: *"I have a strategy that made 40% in backtest"* is a claim.
*"I have a strategy that made 40%, here are the 47 other things I tried, and here's the
standard I committed to before I looked"* is evidence.

### When to reach for it

- You're about to test something you might put real money behind
- You've been trying variants a while and can't honestly say how many
- You're about to spend data you only get to spend once
- You'll need to answer *"how many things did you try?"* later — to an allocator, a
  partner, or yourself in six months

Not for it: poking at data to get a feel for it. That's what `explore` is — it logs the
run in under five seconds and gets out of the way. Exploration is fine and should stay
cheap. It just has to be *counted*, because exploration nobody counts is how the trial
number quietly goes wrong, and every threshold here is derived from the trial number.

### What it can't do

It cannot count hypotheses you discarded *before* scoring them — noticing a pattern and
quietly moving on leaves no trace, and no software catches that. It cannot stop you from
working outside it; the defence is only that the honest path is fast. And it will
sometimes tell you a result is weaker than you thought. That isn't a malfunction.

## What this is

A research substrate that enforces declaration-time constraints on hypotheses. Registration
either is or isn't well-formed; whether a hypothesis passes is a separate concern (Phase 2).

**Phase 0 delivers:** record schema, hash chain, block registry, ledger, registry with 6
enforced checks + 10 schema validators, and CLI.

**Phase 1 adds:** eval runner, p-values with declared sidedness, FDR control (BH / BY),
the two-stage screening funnel, and the subperiod ledger.

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

# 291 tests. The Phase 0 core:
#   C-1…C-5:  Canonical serialization (byte-stability)
#   H-1…H-4:  Hash chain (construction + tamper detection)
#   B-1…B-4:  Block registry (consumption, seams, promotion)
#   L-1…L-5:  Ledger (dedup, maintenance, per-universe, overlap, write timing)
#   V-1…V-10: Validation (all schema checks)
#   S-1:      Structure (import direction enforcement)
```

## License

Apache-2.0
