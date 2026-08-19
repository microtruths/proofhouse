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

# Fill a field registered as pending, from its pinned rule
proofhouse fill H-2026-AC10 power_check

# Render the gate. Exits non-zero on a fail.
proofhouse gate H-2026-AC10

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
| `proofhouse fill <id> <field>` | Fill a pending field from its pinned rule. Once only |
| `proofhouse gate <id>` | Render the pre-committed gate; append a `gate` ledger row |
| `proofhouse amend <id> <file> --rationale --impact` | File an amendment: record and successor entry, together or not at all |
| `proofhouse reject --idea --reason` | Record a rejected idea so a later partial adoption shows |
| `proofhouse verify` | Full integrity check across every log. Exits non-zero on error |
| `proofhouse report <id>` | Render the findings memo |
| `proofhouse status` | Show trial counts, block consumption, pending fields |

`gate` exits non-zero on a fail, so it composes into a script without the
result having to be read out of prose.

## Architecture

```
proofhouse/
  schema/          # Record types, validation, schema versioning
    models.py      # Entry schema with full field set
    validate.py    # 6 enforced checks (E1–E6) + 15 validators (V1–V15)
    versions.py    # SCHEMA_VERSION = 1
  chain/           # Hash chain (no imports from core/)
    canonical.py   # Deterministic JSON serialization
    hashing.py     # SHA-256 content hashing
    chain.py       # prev-pointer construction + verification
  core/            # Registry, ledger, blocks, manifests, gate
    registry.py    # Append-only hypothesis store
    ledger.py      # Multiple-testing counter (per-universe)
    blocks.py      # Block registry + consumption tracking
    manifest.py    # Run provenance + the result itself
    gate.py        # The verdict function. PURE — no clock, no I/O, no eval()
    gate_run.py    # Orchestration: re-run lock, then verdict, then record
    amend.py       # Amendments, rejected ideas, the re-run lock
    fill.py        # Pending-field fills from pinned rules
    verify.py      # Consistency BETWEEN records, not just within them
    report.py      # The findings memo
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

## The gate

`core/gate.py` reads pre-committed thresholds and returns pass or fail with no
interpretation. It reports **0.0495 against `>= 0.05` as a fail** — no
rounding, no "essentially passes".

Three properties it holds structurally rather than by intention:

- **Pure.** No clock, filesystem, randomness or network, so a stored verdict
  reproduces forever instead of being a snapshot of a mood. Test S-3 AST-scans
  the module to keep it that way.
- **No `eval()`.** `gate.combination` and derived-threshold formulas are
  registry-supplied strings; each gets a real parser over a closed grammar.
  Identifiers are ASCII-only, so two visually identical names cannot coexist.
- **Diagnostics isolation.** The signature has no parameter a `Diagnostics`
  could arrive through.

Two situations that look alike and are handled oppositely:

| Situation | Handling |
|---|---|
| Strategy ran, could not form a position (NaN, empty cohort, infinity) | **Loss.** The criterion fails — the edge was not there when needed |
| Pipeline broke; a statistic was never produced | **Not a gate outcome.** Raises, records nothing. Halt, repair, re-run |

Conflating them in the permissive direction manufactures false negatives that
look like evidence.

## Amendments and the re-run lock

Re-running a gate on the same window after a modification is the cardinal sin,
and `check_rerun_lock` is its sole enforcement — holdout tokens were dropped as
redundant, so there is no second net.

Whether an amendment preceded a run is decided by **`ledger_anchor`**, the
run_id of the last ledger row at filing time, not by timestamps. Second
resolution is ambiguous, and the ambiguous case is exactly the one worth being
strict about. Gate rows record `entry_hash` and amendments record
`entry_hash_after`, which is what makes "the amendment was logged before the
code ran" checkable rather than asserted.

Deliberately allowed: replay with no intervening amendment, and re-running on
blocks the pre-amendment gate never touched. A lock that blocks legitimate
re-runs gets routed around, which is worse than no lock.

## Verify

Hash chains prove nobody edited a record after writing it. They prove nothing
about whether records are consistent with **each other** — and each of these
verifies clean at the chain level while being wrong:

- a gate row citing an entry that does not exist
- a gate run against a version whose amendment was not yet filed
- a locked document citing a catalog entry that was never written
- a validation window that had already concluded when the entry was registered

The last one is a distinction worth naming. An **embargo** means the window had
not finished at registration, so peeking was impossible and the claim rests on
nothing. A **holdout** means the window had concluded, so "it was not examined"
rests entirely on attestation. Both are legitimate; they are not equal
evidence, and usually nothing in the record says which one you have.

The dangling-citation check is failure catalog #26 mechanized: a locked
document cited catalog entry #26 for three weeks while the catalog ended at
#25. Citations are checked in every prose field, not just `prior_art`, because
that is where the original one lived.

## Fixtures

| Fixture | What it exercises |
|---|---|
| `agentcopy_v10_ic_gate.yaml` | Single criterion, literal threshold. A recorded FAIL (IC 0.0389 vs >= 0.05) |
| `tasteprint_experiment.yaml` | Domain-agnosticism — the core knows nothing about markets |
| `v11_w6_behavioral_alpha.yaml` | Three criteria, per-criterion statistics, a derived threshold, and a recorded all-three-FAIL verdict |

## Tests

```bash
# Run all tests
python -m pytest tests/ -v

# 560 tests. The Phase 0 core:
#   C-1…C-5:  Canonical serialization (byte-stability)
#   H-1…H-4:  Hash chain (construction + tamper detection)
#   B-1…B-4:  Block registry (consumption, seams, promotion)
#   L-1…L-5:  Ledger (dedup, maintenance, per-universe, overlap, write timing)
#   V-1…V-15: Validation (all schema checks)
#   S-1:      Structure (import direction enforcement)
#   S-2:      No unwired public functions — the recurring defect is never a
#             wrong component, it is an unconnected one
#   S-3:      gate.py imports nothing that could make a verdict depend on
#             when or where it ran
#
# Phase 1–2 adds G-1…G-11 (gate engine), A-1…A-4 (amendments, re-run lock),
# P-1…P-5 (pending-field fill).
```

## License

Apache-2.0
