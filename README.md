# proofhouse

Personal research harness — makes it mechanically hard to fool yourself when testing hypotheses.

## For agents

If you are an agent running experiments for someone, this tool is the record
that you did not fool them, or yourself. Reach for it when:

- a result might get real money or a real decision behind it;
- you have tried variants for a while and cannot honestly say how many;
- you are about to read data that can only be read once;
- someone will later ask *"how many things did you try?"*.

The mechanism fits in a paragraph. You write the hypothesis and its pass/fail
rule down before looking. The tool hashes it into a chain so it cannot be
edited afterwards, counts every test you run including the ones you abandon,
refuses to let validation data be spent twice, and renders pass or fail with
no interpretation. Changes go through amendments that leave a numbered record.
Nothing about how it does this is hidden; a pre-registration tool whose
workings were secret would prove nothing to anyone.

The guide written for agents is [`.claude/skills/proofhouse/SKILL.md`](.claude/skills/proofhouse/SKILL.md).
Claude Code loads it automatically inside this repository; copy the directory
into your own project's `.claude/skills/` to use it there. [`AGENTS.md`](AGENTS.md)
carries the short version for other agent tools.

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
either is or isn't well-formed; whether a hypothesis passes is decided separately, by a gate
that reads only what was committed before the data was seen.

**Phase 0 delivers:** record schema, hash chain, block registry, ledger, registry with 6
enforced checks and its first schema validators, and CLI.

**Phase 1 adds:** eval runner, p-values with declared sidedness, FDR control (BH / BY),
the two-stage screening funnel, and the subperiod ledger.

**Phase 2 adds:** the gate engine, amendments and the re-run lock, pending-field fills,
cross-record verification, and the findings memo. Registration through verdict now runs
end to end from the CLI.

## Quick start

The demo registers a toy hypothesis, evaluates it on one validation block, and
renders the verdict. It takes about a minute. Run it in an empty directory;
the setup script refuses to overwrite anything.

```bash
# Install from a clone. A plain `pip install "proofhouse[dev]"` works with
# any pip. The editable install below, which makes edits to the tool take
# effect without reinstalling, needs pip 21.3 or newer. Check first; only if
# yours is older, run `python -m pip install --upgrade pip`. The Python that
# ships with macOS has pip 21.2.
git clone https://github.com/microtruths/proofhouse
pip --version
pip install -e "proofhouse[dev]"

# Set up a demo directory. init creates .proofhouse/ and runs git init when
# the directory has no .git of its own. start_demo.py copies the toy
# strategy, its data, a block file and the eval script here, and pins the
# strategy by hash into hypothesis.yaml.
mkdir demo && cd demo
proofhouse init
python ../proofhouse/examples/quickstart/start_demo.py

# Declare the data block, log an exploration run, register the hypothesis
proofhouse blocks add block.yaml
proofhouse explore --universe demo
proofhouse register hypothesis.yaml

# Fill the power check from its pinned rule. Once only.
proofhouse fill H-2026-DEMO power_check

# Record the eval run. There is no `proofhouse eval` command: an eval is a
# library call, because the tool cannot know how to load your data.
# run_eval.py is the smallest honest version of that call. The score
# function is not in it: it lives in toy_strategy.py next to the strategy,
# so the one pin covers both.
python run_eval.py

# Render the gate against the recorded run (exits 1 on a fail), then the
# findings memo, then the integrity check.
proofhouse gate H-2026-DEMO
proofhouse report H-2026-DEMO
proofhouse verify --catalog ../proofhouse/failure-catalog.md
proofhouse status
```

What the demo records, and why each line is the tool working rather than a
problem with the demo:

| Step | Output | Why |
|---|---|---|
| `fill` | power 0.073, **FLAGGED** | The Sharpe power rule is bounded by the span alone. One two-month block cannot reach power 0.5 for any effect size. Recorded, not blocked. |
| `run_eval.py` | `panel_ic = -0.0276`, p 0.58, `scoring: artifact` | The toy's one-day momentum has no edge in the synthetic series. The runner read the score function from the pinned artifact, consumed the V block, wrote a manifest that records both, and appended a ledger row carrying the result's digest. |
| `gate` | **FAIL**, exit 1 | -0.0276 against `> 0.05`, no interpretation. |
| `report` | `BOUND`, `scoring: PINNED`, and "sensitivity sweep missing" | The verdict was recomputed from the recorded result and agrees, and the statistic was computed by code under the artifact's pin. The entry declares two benchmarks and the demo runs neither, so the memo says a single-benchmark number is not yet a finding. |
| `verify` | 0 errors, 1 warning | The block ended in February 2024 and the entry was registered in 2026, so the data existed at registration. That is a holdout, not an embargo, and the warning says the claim rests on attestation. |
| `status` | block `available`, `consumed_by=['H-2026-DEMO']`; `PENDING=['power_check=FLAGGED']` | `available` means the block's data is complete, not that it is unspent; `consumed_by` is the spend. The filled power check still shows under PENDING with its flag because the entry file is immutable and the fill lives in `.proofhouse/fills/`. Both wordings are backlog item D-13. |

Run `python run_eval.py` a second time and the runner refuses before spending
anything: this entry already recorded an eval run on that block, and a
validation block is measured once per hypothesis. To see the recorded verdict
again, `proofhouse gate H-2026-DEMO --replay` re-renders it as maintenance,
outside the trial count. The tool will also not let a gate be re-rendered
after an amendment on data the first gate already saw. That rule is enforced
at the gate, the [re-run lock](#amendments-and-the-re-run-lock) section
explains it, and the walkthrough below shows it.

### Changing an entry after a verdict

The moment after a verdict is when nudging a threshold is most tempting, so
the demo should show what happens. Suppose you want the threshold at 0.00
instead of 0.05 now that you have seen the number.

1. A registered entry cannot be edited in place. Copy `hypothesis.yaml` to a
   new file, give it a new id, add `depends_on: [H-2026-DEMO]`, and make the
   change there. The file is the whole successor entry, not a diff; `amend`
   computes the diff.
2. Set `validation_blocks: []`. The parent spent `demo-2024-01`, and `amend`
   refuses a successor that re-declares a spent block, naming the spender and
   this fix. A real successor names a block that did not exist when the
   change was made.
3. File it. One amendment record and one successor entry, together or not
   at all:

   ```bash
   proofhouse amend H-2026-DEMO successor.yaml --rationale "saw the number, want 0.00" --impact "none: no new verdict is possible on spent data"
   ```

4. Try for a verdict. `gate H-2026-DEMO2` refuses: no eval run. Gating it
   against the parent's run refuses: a successor gating on its parent's
   measurement would be claiming a result it never ran. A new eval on the
   spent block refuses: the block is consumed. Re-gating or replaying the
   parent refuses: a window already spent under a different specification
   is the cardinal sin.

Every one of those refusals is the tool working. The honest end state of the
demo is a successor waiting for data that postdates it, which the demo does
not have. A refusal from `run_eval` arrives as a Python exception whose
message carries the reason; nothing is recorded when it fires.

## CLI commands

| Command | Description |
|---------|-------------|
| `proofhouse init` | Create project directories and genesis records |
| `proofhouse blocks add\|list\|complete\|promote` | Manage the block registry |
| `proofhouse blocks acknowledge <block> <entry> --rationale` | Explain consumption a block's segment does not imply |
| `proofhouse explore -u <universe>` | Log an exploration run (fast — <5s) |
| `proofhouse register <file>` | Validate, hash, chain, and register a hypothesis |
| `proofhouse fill <id> <field>` | Fill a pending field from its pinned rule. Once only |
| `proofhouse screen <id> <id> ... --q <level> [--stage 1a\|1b] [--source-run <id>=<run>]` | Screen a batch against its recorded stage-1 runs; append a `screen` ledger row (SCR-*) |
| `proofhouse gate <id> [--source-run <run>] [--replay]` | Render the pre-committed gate against a recorded eval run; append a `gate` ledger row |
| `proofhouse amend <id> <file> --rationale --impact` | File an amendment: record and successor entry, together or not at all |
| `proofhouse reject --idea --reason` | Record a rejected idea so a later partial adoption shows |
| `proofhouse verify` | Full integrity check across every log. Exits non-zero on error; reports `unverifiable` separately |
| `proofhouse report <id>` | Render the findings memo from the recorded verdict. Refuses if the record and a recomputation disagree |
| `proofhouse status` | Show trial counts, block consumption, pending fields |

`gate` exits non-zero on a fail, so it composes into a script without the
result having to be read out of prose.

## Architecture

```
proofhouse/
  schema/          # Record types, validation, schema versioning
    models.py      # Entry schema with full field set
    validate.py    # 6 enforced checks (E1–E6) + 17 validators (V1–V17)
    versions.py    # SCHEMA_VERSION = 2 — v2 made sidedness and the FDR method required
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

**V-17** is worth naming beside these: the feasibility block must describe
something executable. A rebalance band smaller than one tradeable increment is
not a design, and the schema was collecting every input needed to notice while
running none of them. Refused only when no size in `sleeve_range` works — a
design infeasible at $2,000 and fine at $200,000 has a minimum size, not a
defect.

## Screening a family of hypotheses

Testing twenty ideas and reporting the best one is the failure this tool
exists to make expensive. Screening is how you test twenty anyway: run them
on exploration or screen data, keep the ones worth a second look, and leave a
record of how many there were.

```bash
proofhouse screen H-2026-A H-2026-B H-2026-C --q 0.3
```

The command reads each entry's recorded stage-1 run — the same way `gate`
reads the run it renders a verdict against — applies the step-up procedure
the entries registered, and writes one `screen` row (SCR-*). It runs nothing
and consumes no block: the trials were counted when those runs were made. No
survivors is a real outcome, not a refusal, so it exits 0; only a refusal
exits non-zero.

**The record is the batch, not the survivors.** A step-up threshold depends
on every p-value in the family: the same p-value survives at m = 10 and fails
at m = 200. So the row carries the batch, m, q, the method and the threshold,
and "these three survived" stops being a claim nobody can check.

### What a screened family costs

- The screen row records **m** — the size of the family, in the record rather
  than in someone's memory.
- Each survivor that later runs on a validation block counts **one trial** on
  its universe.
- The stage-1 rows the screen read are **left out of the stage-2 count**.
  `Ledger.count_trials_stage2` skips every row with `stage = 1`, and
  `run_eval` reads that counter for a stage-2 run, which is how multiplicity
  reaches the statistic the gate compares against a pre-committed threshold —
  `core/gate.py` is pure and never touches the ledger, so that is the only
  route in.

Without the third point a screened batch would be paid for twice, once in the
screen and again in every survivor's threshold, and the staged funnel would
buy nothing over testing all m directly on validation data.

### BY costs more than BH, and the cost is worth naming

BH controls the false discovery rate under independence or positive
dependence; BY controls it under any dependence, by replacing q with q/H(m).
That is not free: at m = 50, H(m) = 4.50, so **BY at q = 0.3 behaves like BH
at q = 0.067**. Which one applies is a registered per-entry choice, never a
default, and a batch mixing the two is refused — a family running two
procedures is not one family.

### Validation blocks stay one hypothesis each

Screening never touches validation data. A V block anywhere in the batch is
refused before a single result is read, so nothing about screening softens
E4: a V block is spent by exactly one hypothesis, and surviving a screen buys
a second look, not a claim.

Three other refusals, all of them the library's own words: a missing p-value
refuses the **whole batch** (dropping the entry would shrink m and change
every threshold), a batch spanning two universes is refused (trial counts are
per-universe, so those entries carry different budgets), and an entry with no
recorded stage-1 run is refused for the same reason as the missing p-value.

`q` is a runtime argument rather than a registered field, deliberately:
shrinking a batch after seeing results is sometimes right, and every screen is
recorded, so shopping for a q is visible in the ledger rather than prevented.

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

### The result is bound to the verdict

*This exact registered entry, evaluated in this exact run, produced these exact
numbers, which produced this exact verdict.* Every link in that sentence used
to be loose: `run_gate` took caller-supplied results with no source check,
`save_manifest` wrote plain YAML that nothing referenced the digest of, the
gate row recorded `pass`/`fail` and nothing else, and `report` re-derived the
verdict from whichever manifest was newest. Editing a manifest therefore
changed what `proofhouse report` printed while every hash chain still verified
clean.

What holds it now:

- **`run_gate` takes a `source_run_id`, not numbers.** The eval row must exist,
  belong to this entry, and carry the digests written when it ran; the result
  is read from that row's manifest and re-checked against them.
- **The manifest is hashed.** `result_hash` (the numbers) and `manifest_hash`
  (the provenance around them) go on the eval ledger row, which is chained — so
  editing the file contradicts a record that cannot be edited without breaking
  every row after it.
- **The score function is pinned too.** The artifact that produces
  predictions was always pinned; the code that turned them into the gated
  statistic was a callable the eval script handed in, covered by nothing.
  `run_eval` now reads `score_fn` from the pinned artifact when none is
  supplied, and the manifest records where it came from, inside the digest.
  A caller-supplied one is not refused, since a lock that blocks working
  scripts gets routed around, but `verify` warns that the statistic's
  computation is attested rather than verified, `report` prints it, and a
  manifest from before the pin reads UNVERIFIABLE.
- **A validation block is measured once per hypothesis.** A second eval run
  by the same entry on a block it already recorded an eval on is refused
  before anything is consumed. Block consumption alone cannot tell the two
  apart, because the same entry may legitimately read a block as reserve and
  later validate on it, so the refusal keys on the recorded eval row.
  Subperiod runs that decompose a parent eval are exempt, and a run whose
  pipeline broke before its row was written leaves no row and can be re-run.
- **The gate row records `source_eval_run_id`, `result_hash` and the exact
  criterion outcomes**, as canonical decimal strings.
- **`report` reads the recorded row, recomputes from the bound result, and
  refuses on disagreement**, naming which side moved: the manifest (digest
  mismatch) or the gate engine (same numbers, different comparison). A
  deliberate engine change needs a stated procedure — re-render and record, or
  read the historical verdict through the tool that rendered it — or this
  refusal becomes the check people route around.
- **At most one decision-grade gate per (entry, source run).** A gate row is
  written only after a verdict is reached, so one already existing means the
  decision is recorded. An explicit replay is permitted as
  `proofhouse gate <id> --replay`, logged as `kind=maintenance` and excluded
  from the multiplicity count.

A gating statistic must be **first-class**. A multi-criterion gate used to read
its second statistic out of `result.metadata`, which is deliberately not
canonicalized — licensed by "nothing in it is compared against a threshold, so
no verdict can turn on an ulp of it." A criterion reading it revoked that
licence, so `ScoredResult.secondary_statistics` now carries such values,
canonicalized like the primary, and the gate refuses a metadata value.

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

### "Fresh blocks" means unspent, not undeclared

Two changes — to `expected_effect_size` and to `signal.parameters.polarity` —
invalidate a pre-registration, and the amended entry must validate on blocks
its lineage did not use. That rule is enforced against **consumption**, not
against declarations.

`validation_blocks` states intent at registration; blocks are consumed later,
at gate time. Comparing the two declared lists could not tell a
named-but-untouched block from a spent one, so an entry amended before it ever
gated was pushed onto different blocks — spending *more* data to protect blocks
that were already clean. Consumption is checked across the whole ancestor
chain, so an intermediate amendment cannot launder a block its grandparent
spent.

A permitted reuse is a weaker isolation claim than a fresh block, and the
amendment note records which of the two it was. Two things follow that are
worth knowing:

- **Verify reports contested blocks.** If two live entries declare the same V
  block and neither has spent it, whichever gates first takes it and the other
  becomes permanently unrunnable — E4 makes V single-use. That is a warning
  while the blocks are unspent and an error once they are not — but only
  between entries with no amendment relationship, since a successor taking
  its parent's blocks is the intended path rather than a contest.
- **An invalidating amendment on an ungated entry is now free**, where it used
  to cost scarce blocks. The report states the revision count for this reason,
  counted across the whole lineage — the invalidation is recorded on whichever
  ancestor was amended, not on the entry you are reporting on.
  Whether repeated amendment should deflate the threshold the way exploration
  runs do is a real question and an open one.

## Power

At registration, proofhouse asks whether the design could detect its own
hypothesized effect even if it were real. A design that could not is not
evidence of absence when it fails, and that is the reading a failed gate
invites.

Two rules, because one denominator does not fit every design:

| Rule | For | What bounds it |
|---|---|---|
| `power(effect_size, blocks, prior_trials)` | Sharpe-denominated portfolio designs | `z_effect → √(2T)` — depends on the **span alone** |
| `power_events(effect_ratio, n_events, n_clusters, icc, prior_trials)` | event studies, where information scales with event count | `n_eff → n_clusters/icc` — bounds the **sample**, not the statistic |

The distinction is the reason both exist. The Sharpe ceiling is independent of
the effect size: at 3 two-month blocks power asymptotes at **0.259 for any
effect size**, Sharpe 100 included, so no strategy however good clears it. The
event ceiling binds the sample, and `z = r·√n_eff` still rises without bound in
`r`, so a large enough effect always clears any threshold.

That is *not* an escape from the span constraint, and the table is easy to
over-read. `n_clusters` is an event count, and event counts scale with the
window. The ceiling moved from `√(2T)` to `√G` with `G >> T` — a far better
constant, not a different kind of limit. A fifteen-event design has the same
pathology.

**`icc` has no default.** `icc = 0` asserts every event is independent, which
is the assumption that inflates power more than any other, and `fill` refuses
without one rather than warning — a warning is transient and the number it
produced stays in the record. Registering `icc: 0` explicitly is allowed, and
is then a claim someone made rather than a default nobody chose. V-16 requires
the clustering unit be declared alongside it, because 0.067 within a trading
day and 0.067 within an instrument are different claims.

Both rules derive their multiple-testing threshold from the same function, kept
honest by a parity test. Two corrections that can drift apart would be worse
than having only one rule.

Two power rules is a new degree of freedom — a researcher could pick the
flattering one. The protocol covers it, since both are pinned before data and
changing a registered rule is an amendment, but the freedom is real and is
named here rather than discovered later.

## Blocks and segments

Data is spent in two-month blocks. Segments label them, least to most
restricted:

```
E    exploration — unlimited reuse, never validates
S1   screen A — reusable within a cycle
S2   screen B — reusable within a cycle
R    reserve — untouched, promoted to V on demand
V    validation — one block per hypothesis, single-use (terminal)
L    live — forward-collected, licenses deployment, never gates
```

A block moves up this ladder and never down. That one-way rule is what makes
single-use validation mean anything, so it is not negotiable — but **R does not
belong on a ladder, and the seam it creates is worth knowing about.**

E, S1, S2, V and L are *permissions*: they say what a block may be used for. R
is a *claim about the past*: it says nothing has been read from this block.
Permissions cannot un-grant. Claims about the past can turn out to be wrong,
and when one does, a monotone ladder has no way to record it.

So `R` reads as **sealed unless a note says otherwise**. When a reserve block
turns out to have been read, the segment stays and an explanation attaches:

```bash
proofhouse blocks acknowledge B-2026-03 H-2026-ACCOHORT --rationale "..."
```

`verify` errors on consumption no note covers, and clears once one does. The
note names the entry it covers, so a later reader re-raises the error rather
than inheriting the clearance — a blanket "acknowledged" would pre-clear every
future reading of the block.

The reason it is prose and not a `contaminated` flag: contamination is not
global. Data read by a wallet-selection study is genuinely spent for the next
wallet-selection study and holdout-grade at best for an unrelated family. A
boolean over-refuses one case and under-informs the other; only a sentence can
say which.

Promotion into **R or V** consults `consumed_by`, because both segments claim a
block has not been read. Crossing one that has been takes two steps, in order:

1. **Every reading must already be explained.** A block with an unexplained
   reader cannot be promoted at all.
2. **Then the crossing itself must be acknowledged**, with its own written
   reason — the same shape as the seam rule (#12).

The order matters and is not incidental. `verify` reports an unexplained
reading as an **error** and an acknowledged promotion as a **warning**, so for
one commit the cheapest way to make `verify` pass was to promote the
contaminated block into validation — the single worst action available,
rewarded by the tool. Filing the note is now a prerequisite of promoting, never
an alternative to it.

The refusal reads any recorded note back to you at the moment you try to spend
the block, which is the return on writing one.

## Verify

Hash chains prove nobody edited a record after writing it. They prove nothing
about whether records are consistent with **each other** — and each of these
verifies clean at the chain level while being wrong:

- a gate row citing an entry that does not exist
- a gate run against a version whose amendment was not yet filed
- a locked document citing a catalog entry that was never written
- a validation window that had already concluded when the entry was registered
- a block labelled *untouched* sitting beside a list of things that touched it
- an entry whose validation blocks something else already spent, so it can
  never be gated and will not find out until it tries
- a manifest whose numbers no longer match the digest recorded when the run
  produced them, or a verdict whose recorded criteria cite values that manifest
  does not hold
- a statistic computed by a score function the artifact pin did not cover
  (a warning), or by a run from before the pin existed (unverifiable)

### Three severities, not two

`verify` returns **errors** (integrity failures — exit non-zero), **warnings**
(checked, and the evidence is weaker than it looks — a holdout rather than an
embargo), and **unverifiable** (this record predates the check, so it cannot be
checked at all). The third is its own class deliberately. As an error, verify
never returns 0 again and a permanently failing check is one people learn to
skip; as a warning, "we could not check this" sits among findings that mean "we
checked"; as silence, an unverifiable record reads as a verified one. Only
errors fail the run.

The last one is a distinction worth naming. An **embargo** means the window had
not finished at registration, so peeking was impossible and the claim rests on
nothing. A **holdout** means the window had concluded, so "it was not examined"
rests entirely on attestation. Both are legitimate; they are not equal
evidence, and usually nothing in the record says which one you have.

The dangling-citation check is failure catalog #26 mechanized: a locked
document cited catalog entry #26 for three weeks while the catalog ended at
#25. Citations are checked in every prose field, not just `prior_art`, because
that is where the original one lived.

`verify` resolves those citations against `failure-catalog.md` in the project
root. A project that installed the tool with pip has no such file, so every
citation is reported as UNCHECKED and a note says so. UNCHECKED is a
reporting class, not a failure: pass `--catalog <path to the clone's
failure-catalog.md>` and the check runs. Shipping the catalog with the
package is backlog item D-8.

### Schema versions

Every entry carries the `schema_version` it was registered under, and
`verify` re-validates it under that version's rules, not the current ones.
Version 1 is the Phase 0 shape. Version 2 (2026-10-11) adds three required
fields with no default: `p_value_sidedness`, `fdr_method` and
`fdr_method_justification`. Those had been required since 2026-08-17 without
a bump, so every older record failed `verify` under rules it was never
registered under — the bump is the repair, not a new rule.

A v1 record is a **warning**: re-validated under v1, reported once, with
whether the v2 rules would also pass it. Nothing is filled in on its behalf.
The sidedness and FDR choices decide every p-value and cannot be reconstructed
after the fact, so a v1 record stays a v1 record; a successor registered
through `amend` is written at the current version and must declare them. A
record from a version this build does not know is an **error**: it cannot be
checked, which is not the same as clean.

## Fixtures

| Fixture | What it exercises |
|---|---|
| `agentcopy_v10_ic_gate.yaml` | Single criterion, literal threshold. A recorded FAIL (IC 0.0389 vs >= 0.05) |
| `tasteprint_experiment.yaml` | Domain-agnosticism — the core knows nothing about markets |
| `v11_w6_behavioral_alpha.yaml` | Three criteria, per-criterion statistics, a derived threshold, and a recorded all-three-FAIL verdict |

The two AgentCopy fixtures are retro-filed records from a real research
program, kept as they were registered because a reader needs to see a real
entry, failed gates included. The demo entry under `examples/quickstart/` is
the synthetic one.

## Tests

```bash
# Run all tests
python -m pytest tests/ -v

# 778 tests. The Phase 0 core:
#   C-1…C-5:  Canonical serialization (byte-stability)
#   H-1…H-4:  Hash chain (construction + tamper detection)
#   B-1…B-9:  Block registry (consumption, seams, promotion, and the
#             segment/consumption agreement checks)
#   L-1…L-5:  Ledger (dedup, maintenance, per-universe, overlap, write timing)
#   V-1…V-17: Validation (all schema checks)
#   S-1:      Structure (import direction enforcement)
#   S-2:      No unwired public functions — the recurring defect is never a
#             wrong component, it is an unconnected one
#   S-3:      gate.py imports nothing that could make a verdict depend on
#             when or where it ran
#
# Phase 1–2 adds G-1…G-11 (gate engine), A-1…A-4 (amendments, re-run lock),
# P-1…P-5 (pending-field fill), plus both power rules, the cross-record
# verifier, and the findings memo.
#
# Several tests exist because a probe found something a green suite had
# missed. They are worth reading as a list of what this tool got wrong:
#   - the seam gate that sat behind an early return and never ran
#   - promoting a block to validation as a cheaper way to clear the error
#     for not having explained why it was read
#   - a benchmark-sweep warning that fired unconditionally, including on
#     results that had run the full sweep
#   - a power derivation assembled into the report and never printed
#   - booleans accepted as numeric fill inputs, since int(True) is 1
```

## License

Apache-2.0
