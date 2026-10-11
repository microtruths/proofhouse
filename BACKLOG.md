# Tool backlog

Durable record of known tool defects and deferred fixes, each with its
revive condition. Items here were previously scattered across
retrospectives and gitignored plan docs; this file is the authoritative
list. Fixing an item mid-program follows the usual rule: tool changes
that could alter a registered rule's output are made between trials,
never silently mid-sweep.

| # | Item | Impact | Revive condition | Source record |
|---|---|---|---|---|
| D-5 | `proofhouse explore` writes repeated `-b`/`-s` flag values as ONE comma-joined string where earlier ledger rows carry proper lists (e.g. R-000015's `blocks_touched`). Content complete, rows chained — recorded rows stand as-is; fix forward only. Workaround: pass a single flag value. | Cosmetic/parsing (a consumer that assumes list-of-ids must split on commas) | Next time the `explore` CLI is touched for any reason | Eruptions sweep retrospective, 2026-08-20, §3 (private research record) |
| D-7 | There is no `proofhouse eval` command. Recording an eval run means writing a script around `run_eval` (`examples/quickstart/run_eval.py` is the template). The quick start shows the script honestly rather than hiding the gap. | Every new user writes the same twenty lines; the README cannot be CLI-only | When a second adapter exists, or when the first outside user asks for it | This file, 2026-10-11 (public-release plan) |
| D-8 | `verify` looks for `failure-catalog.md` in the project root and otherwise reports every catalog citation as UNCHECKED. A pip-installed user has no catalog unless they pass `--catalog <path to the clone>`. | A stranger reads UNCHECKED as breakage | When the first UNCHECKED report is mistaken for a failure; fix is to ship the catalog as package data and read it by default | same |
| D-9 | `proofhouse init` does not write `AGENTS.md` or the agent skill into the research project, so an agent working in a project that uses the tool has to be pointed at the clone. | Adoption friction, not integrity | After the first outside adoption | same |
| D-10 | `amend` accepts a successor whose `validation_blocks` re-declares a block its parent already spent. `verify` then reports a permanent error that no later successor can clear, because the record is append-only. The natural move (copy the parent's file, change one field) produces exactly this. | A permanent verify error from following the obvious path; the correct warning sits in the skill, not in the tool | Before the next outside user files an amendment; fix is a refusal or a loud warning in `amend` naming the spent block | Stranger-agent test, 2026-10-11 |
| D-11 | `init` runs `git init` whenever the current directory has no `.git`, checking only that directory. Run in a subfolder of an existing repository it creates a nested repository. | Confusing, and the nested repo can hide the registry from the project's own git | When `init` is next touched; check for an enclosing repository first | same |
| D-12 | A second `run_eval` of the same entry on its already-spent V block is recorded, and a plain `gate` on that run records a second decision-grade verdict without `--replay`. Harmless while nothing changed, but the score function lives in the user's eval script and is not pinned, so this is a route to a different recorded number under the same statistic name with no amendment on file. | Open question for the maintainer: either pin the score function or refuse a second eval by the same entry on a V block it already spent | Maintainer's call before the first outside user gates anything real | same |
| D-13 | Wording: `status` prints `status=available` for a spent block (it means `data_complete`, not unspent); `run_eval`'s spent-block refusal is a raw exception citing "(A7)", a plan label the docs do not use (the docs call the rule E4). | Reads as breakage or as a reference to a missing document | Next time either message is touched | same |
| B1 | `verify` does not flag an unpinned entry (readiness, not integrity). Three live entries (PFR1–PFR3) are unpinned; their blocks are spent by PFR4, so under the proposed severity rule they render as warnings and `verify` stays at 0 errors. | A future gating registration could reach its gate unpinned without warning | Before registering the next entry that will actually gate | Bind-eval-to-verdict plan, 2026-08-19, B-table (gitignored plan doc, not in the repo) |
| B3 | Threshold changes are captured in amendment diffs (e.g. A-0001's `gate.criteria.g1.threshold.literal`) but not labelled/disclosed as revisions. | Disclosure gap, not a lost record | When the next entry's thresholds are edited pre-gate | same |
| B4 | `power_check` is not inherited across an amendment; the successor's `pending: true` field is a known stale-field trap (see the agent skill's note that the entry YAML is not the whole truth). PFR4 was corrected manually via F-0002 with byte-identical inputs. | Reporting convenience; stale `pending` fields mislead readers | When a successor is next registered with an unchanged power rule | same |
| B2 | No bounded context-read into a registered block (the refusal it works around is correct and stays). Optional workflow design, not a defect. | — | Only if a bounded lookback is actually needed | same |

Filed 2026-08-21. D-7 to D-13 filed 2026-10-11.

## Closed

| # | Item | How it was closed |
|---|---|---|
| D-6 | The screening engine had no command. `run_screen`, both step-up procedures and `ledger.append_screen` all existed and were tested, but nothing outside the tests called them, so screening a batch meant writing a script by hand — the same "correct code, no caller" shape S-2 exists to catch, one level above where S-2 looks. | Closed 2026-09-14. `proofhouse screen` added. It screens the stage-1 runs the ledger already holds, the way `gate` reads the run it renders a verdict against, and records the batch, m, q, method and threshold as a `screen` row. The batch rules now live in one place (`check_screen_batch`), shared by the command and by `run_screen`. |
