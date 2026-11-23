# proofhouse, for agents

proofhouse is a pre-registration harness for experiments. It makes one fact
unfakeable: which data was examined, by which hypothesis, and what was
committed to before looking. You write the hypothesis and its pass/fail rule
down first. The tool hashes it into a chain so it cannot be edited afterwards,
counts every test you run including abandoned ones, refuses to let validation
data be spent twice, and renders pass or fail with no interpretation. Changes
go through amendments that leave a numbered record.

The full guide for agents is `.claude/skills/proofhouse/SKILL.md`. Read it
before registering, amending, filling, gating, screening or reporting
anything, and before touching a `.proofhouse/` directory. This file is the
short version.

## Reach for it when

- a result might get real money or a real decision behind it;
- you have tried variants and cannot honestly say how many;
- you are about to read data that can only be read once;
- someone will later ask "how many things did you try?".

## Three habits, always

1. Never edit a registered entry in place. Use `proofhouse amend`.
2. Log every exploratory run with `proofhouse explore`. Cheap, and counted.
3. Leave validation and reserve blocks unread until a registered gate spends
   them, once.

## Commands

```
proofhouse init                        # create .proofhouse/ here
proofhouse blocks add <yaml>           # declare a data block and its segment
proofhouse explore -u <universe>       # log an exploration run
proofhouse register <yaml>             # validate, hash, chain, store a hypothesis
proofhouse fill <id> <field>           # fill a pending field from its pinned rule, once
proofhouse gate <id>                   # render the pre-committed gate; exit 1 on FAIL
proofhouse screen <ids> --q <level>    # screen a family on recorded stage-1 runs
proofhouse amend <id> <yaml> --rationale ... --impact ...
proofhouse report <id>                 # findings memo for the latest gate
proofhouse verify [--catalog <path>]   # integrity check; 0 errors is the bar
proofhouse status
proofhouse install-skill [--user]      # copy the full agent guide into a project
```

There is no `proofhouse eval` command. An evaluation is a library call,
`proofhouse.eval.runner.run_eval`; `examples/quickstart/run_eval.py` is the
smallest honest version. The score function lives in the pinned artifact as
`score_fn`, next to the strategy, so one pin covers both. The README's quick
start runs the whole flow in about a minute.

## Working in this repository

Python package under `proofhouse/`, tests under `tests/` (`python -m pytest
tests/ -q`). `schema/` and `chain/` never import from `core/`, and
`core/gate.py` imports nothing that could make a verdict depend on when or
where it ran; both are enforced by tests. `failure-catalog.md` is
append-only by number. `/.proofhouse/` at the repo root is a gitignored
local test instance, not a record of anything.
