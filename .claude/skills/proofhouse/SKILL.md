---
name: proofhouse
description: How to use proofhouse, a pre-registration harness for experiments, from an agent. Covers when to reach for it, the mental model (immutable hash-chained entries, amend instead of edit, single-use validation blocks, a pure gate), the command set, where each fact actually lives, and the rules that have burned earlier sessions. Load it before registering, amending, filling, gating, screening or reporting anything, and before touching a .proofhouse/ directory.
---

# Using proofhouse

proofhouse makes one fact unfakeable: **which data was examined, by which
hypothesis, and what was committed to before looking.** Everything below
follows from that. It does not find edges and has no opinion on whether an idea
is good. It makes fooling yourself, or the person you work for, expensive and
visible.

If you are an agent running experiments, you are the likeliest source of the
leak this tool exists to stop. You try variants quickly, you lose count, and
every threshold you nudge feels reasonable while you nudge it. The record this
tool keeps is how the person reading your results can tell a lucky result from
a good one.

## When to reach for it

- Something you are about to test might get real money or a real decision
  behind it.
- You have been trying variants and cannot say honestly how many.
- You are about to read data that can only be read once (a validation window).
- Someone will later ask "how many things did you try?".

Not for it: poking at data to get a feel for it. That is `proofhouse explore`,
which logs the run in under five seconds and gets out of the way. Exploration
stays cheap. It only has to be counted, because every threshold the tool
derives depends on the trial count.

## Before doing anything in a project

1. If the project is shared, `git pull` first. The registry is a set of files
   other sessions may have appended to.
2. Run `proofhouse status` and `proofhouse verify`. The standard is verify
   with 0 errors. If an action you are about to take would turn 0 errors into
   more, stop and reconsider the mechanism you are using.
3. Read the entry you are about to work on with `proofhouse report <id>` if
   it has been gated, or by opening its YAML under `.proofhouse/registry/`
   if it has not. Do not work from memory of it.

## The mental model

- **Entries are immutable and hash-chained.** Every file under
  `.proofhouse/registry/` carries `hash` and `prev`. Never edit one in place.
  Doing so orphans every later entry and `verify` will say so. All change
  goes through `proofhouse amend`, which writes a numbered amendment record
  (the diff, a rationale, an impact statement) and registers a successor
  entry with a new id, together or not at all.
- **Supersession is inferred, not stored.** `status` is always `registered`.
  Which entry is live is answered by the `depends_on` lineage: the head of
  the chain is current.
- **A successor of an already-gated entry must not re-declare the blocks its
  ancestor spent.** The collision check exempts blocks spent by a
  descendant, not by an ancestor. Set `validation_blocks: []` on such a
  successor and say why in the rationale, or it sits as a permanent verify
  error.
- **The entry YAML is not the whole truth.** Facts are distributed, and some
  fields go stale by design. A filled `power_check` still reads
  `pending: true` on the entry file. Check the authoritative store:

| Fact | Where it actually lives |
|---|---|
| Gate verdicts | `.proofhouse/manifests/R-*.yaml` and `proofhouse report` |
| Power-check and other fills | `.proofhouse/fills/fills.jsonl` (F-*) |
| Trial counts, per universe | `.proofhouse/ledger/ledger.jsonl` |
| Block segments and consumption | `.proofhouse/blocks/blocks.yaml` |
| Amendment history, with diffs | `.proofhouse/amendments/amendments.jsonl` (A-*) |
| Rejected or closed ideas | `.proofhouse/rejected/rejected.jsonl` (X-*) |
| Screening decisions for a family | `SCR-*` rows in the ledger |
| Transferable lessons | `failure-catalog.md` in the tool repo |

Trial counts are per universe. Never read one universe's count as the
project's.

## Commands

```
proofhouse init                        # create .proofhouse/ in the current directory
proofhouse blocks add <yaml>           # declare a data block (id, start, end, segment, data_complete, ...)
proofhouse blocks list|promote|acknowledge
proofhouse explore -u <universe>       # log an exploration run; counts as a trial
proofhouse register <yaml>             # validate, hash, chain, store a hypothesis
proofhouse fill <id> <field>           # fill a pending field from its pinned rule, once
proofhouse gate <id>                   # render the pre-committed gate; exit 1 on FAIL
proofhouse screen <id> <id> ... --q    # screen a family against recorded stage-1 runs
proofhouse amend <id> <yaml> --rationale ... --impact ...
proofhouse reject --idea ... --reason ...
proofhouse report <id>                 # findings memo for the latest gate
proofhouse status                      # trial counts, blocks, pending fields
proofhouse verify [--catalog <path>]   # integrity check across every log; 0 errors is the bar
```

**There is no `proofhouse eval` command.** An evaluation is a library call,
`proofhouse.eval.runner.run_eval`, because the tool cannot know how to load
your data or score your strategy. You write a short script that builds an
adapter and a score function and hands them to the runner. The runner then
does everything else in a fixed order: verify the artifact pin, check block
isolation, consume the block, run the artifact with parameters from the
entry, score it with the trial count read from the ledger, write a manifest,
and append a chained ledger row carrying the result's digest. The smallest
honest version of that script is `examples/quickstart/run_eval.py`. Copy it
and change the adapter and the score function. Do not skip the runner: a
result that did not pass through it has no manifest and cannot be gated.

## The workflow, in order

1. `init`, then `blocks add` for each data block you will declare.
2. `explore` for every exploratory run. Per campaign is acceptable; per fit is
   stricter. Pick one and say which in the project's notes.
3. Pin the artifact. `signal.implementation` on the entry carries the path
   and the sha256 of the file that will run. The runner refuses any other
   file, or that file edited.
4. `register`. Validators V1 to V17 and checks E1 to E6 run here. A refusal
   is a refusal; do not weaken the entry to get past it without saying so
   in the entry's own prose.
5. `fill` each pending field. Once only. A flag (power below 0.5) is
   recorded, not blocked. It changes how a later failure may be read.
6. Run the eval through `run_eval` at stage 2 on the validation block.
7. `gate`. One decision-grade gate per entry and source run. Replays are
   `--replay` and excluded from the trial count.
8. `report`, then `verify`. Commit `.proofhouse/` to the project's git.
   A registry that lives only on one disk is a local file, not a record.

## Blocks and segments

Data is declared in blocks. Segments say what a block may be used for,
least to most restricted: `E` exploration (unlimited reuse, never
validates), `S1` and `S2` screening (reusable within a cycle), `R` reserve
(claimed unread, promoted on demand), `V` validation (one hypothesis, once),
`L` live (forward-collected, never gates). A block moves up this ladder and
never down. A reserve block that turns out to have been read gets a written
acknowledgment naming the entry that read it; `verify` errors until one
exists, and never clears a future reading on the strength of an old note.

## Rules that bite

Each of these has cost a real session something.

1. **Never re-gate.** A verdict is final. `on_fail` clauses are binding,
   typically "blocks are spent; a revised hypothesis validates on blocks
   postdating its registration." Analysis of spent data after a verdict is
   next-cycle input, labelled as such, never part of the verdict.
2. **Power: know which rule you are under.** The Sharpe rule
   `power(effect_size, blocks, prior_trials)` is bounded by the span alone,
   so no effect size rescues a short window. The events rule
   `power_events(effect_ratio, n_events, n_clusters, icc, prior_trials)` needs
   an explicit `icc` and a prose `clustering_unit`. There is no default icc:
   zero is the assumption that inflates power most, so `fill` refuses
   without one.
3. **Effect ratios come from exploration blocks only**, never from the
   validation blocks the gate will spend. Treat them as ceilings; out of
   sample they decay.
4. **Watch saturation.** Effective sample caps at `n_clusters / icc`. Past
   about 80% of the cap, more events buy nothing; the lever is more
   clusters.
5. **Implement from the registered words, not from memory of them.** The
   registered `signal.transform` text is the spec. Reproducing an already
   logged number through the pinned artifact costs no trial and is the
   right check.
6. **Numbers you report must be re-derivable.** Prefer running the tool's
   own function over trusting prose in an entry. Prose restatements of
   inputs go stale; the record of values does not.
7. **Do not route around a refusal.** Promoting a block to clear an error,
   dropping a hypothesis from a screened family to shrink m, copying a
   result into a manifest by hand: each of these makes `verify` pass while
   making the record false. If the honest path is blocked, the fix is to
   the tool or to the plan, filed as an amendment, not to the record.

## Exploration versus registration

Everything above governs claims being validated or traded. It does not
govern exploratory analysis or building a model, and it should not be read
as if it did. Explore freely on exploration blocks and on blocks already
spent: refit, rethink, change the question, with no pre-set kill thresholds.
Three habits survive into exploration and nothing more:

- leave validation and reserve blocks, and any forward-collected data,
  unread;
- keep a log of which sources, dates and outcomes were touched;
- record exploratory numbers at honest confidence, never as established
  facts from one pass.

The rules return the moment a specific relationship is to be validated or
traded: at that point the shape of the effect gets screened, the entry gets
registered, and one gate decides.

## Screening a family

Several hypotheses on the same data are registered as a family and screened
together on screening blocks, never on validation data. All members declare
the same `fdr_method`; a mixed batch is refused. Run each member's stage-1
eval, then `proofhouse screen` over the batch: it reads the recorded runs,
applies the step-up rule, and writes one `SCR-*` row carrying the batch
size, q, method, threshold and survivors. Surviving a screen is enrichment,
not proof. Each survivor then spends its own validation block.

## Worked examples in this repo

- `examples/quickstart/`: a toy one-day momentum hypothesis on sixty days of
  synthetic returns, registered, filled, evaluated and gated. The recorded
  verdict is FAIL, the power check is flagged (one two-month block cannot
  have power above 0.5 under the Sharpe rule), and `verify` warns that the
  block is a holdout rather than an embargo because the data predated
  registration. All three are the tool working.
- `fixtures/agentcopy_v10_ic_gate.yaml`: a retro-filed real entry with a
  single literal-threshold criterion and a recorded FAIL.
- `fixtures/v11_w6_behavioral_alpha.yaml`: three criteria, per-criterion
  statistics, a derived threshold, and a recorded all-three-FAIL verdict.
- `fixtures/tasteprint_experiment.yaml`: an entry from outside finance, to
  show the core knows nothing about markets.

## What the tool cannot do

It cannot count hypotheses you discarded before scoring them. Noticing a
pattern and quietly moving on leaves no trace, and no software catches
that. It cannot stop you from working outside it. The only defence is that
the honest path is fast, and that the person reading your results can ask
to see the ledger.

## Where to read more

The README covers the gate, amendments and the re-run lock, both power
rules, blocks, screening and verify in detail. `failure-catalog.md` is the
list of ways experiments in this program actually went wrong, numbered and
append-only; `verify` resolves "failure catalog #N" citations against it.
`BACKLOG.md` lists known tool defects and when each gets fixed.
