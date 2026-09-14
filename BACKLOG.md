# Tool backlog

Durable record of known tool defects and deferred fixes, each with its
revive condition. Items here were previously scattered across
retrospectives and gitignored plan docs; this file is the authoritative
list. Fixing an item mid-program follows the usual rule: tool changes
that could alter a registered rule's output are made between trials,
never silently mid-sweep.

| # | Item | Impact | Revive condition | Source record |
|---|---|---|---|---|
| D-5 | `proofhouse explore` writes repeated `-b`/`-s` flag values as ONE comma-joined string where earlier ledger rows carry proper lists (e.g. R-000015's `blocks_touched`). Content complete, rows chained — recorded rows stand as-is; fix forward only. Workaround: pass a single flag value. | Cosmetic/parsing (a consumer that assumes list-of-ids must split on commas) | Next time the `explore` CLI is touched for any reason | `proofhouse-data/docs/2026-08-20-retrospective-eruptions-sweep.md` §3 (tools) |
| B1 | `verify` does not flag an unpinned entry (readiness, not integrity). Three live entries (PFR1–PFR3) are unpinned; their blocks are spent by PFR4, so under the proposed severity rule they render as warnings and `verify` stays at 0 errors. | A future gating registration could reach its gate unpinned without warning | Before registering the next entry that will actually gate | `docs/plans/2026-08-19-bind-eval-to-verdict.md` (B-table) |
| B3 | Threshold changes are captured in amendment diffs (e.g. A-0001's `gate.criteria.g1.threshold.literal`) but not labelled/disclosed as revisions. | Disclosure gap, not a lost record | When the next entry's thresholds are edited pre-gate | same |
| B4 | `power_check` is not inherited across an amendment; the successor's `pending: true` field is a known stale-field trap (see the navigation skill's warning). PFR4 was corrected manually via F-0002 with byte-identical inputs. | Reporting convenience; stale `pending` fields mislead readers | When a successor is next registered with an unchanged power rule | same |
| B2 | No bounded context-read into a registered block (the refusal it works around is correct and stays). Optional workflow design, not a defect. | — | Only if a bounded lookback is actually needed | same |

Filed 2026-08-21.

## Closed

| # | Item | How it was closed |
|---|---|---|
| D-6 | The screening engine had no command. `run_screen`, both step-up procedures and `ledger.append_screen` all existed and were tested, but nothing outside the tests called them, so screening a batch meant writing a script by hand — the same "correct code, no caller" shape S-2 exists to catch, one level above where S-2 looks. | Closed 2026-09-14. `proofhouse screen` added. It screens the stage-1 runs the ledger already holds, the way `gate` reads the run it renders a verdict against, and records the batch, m, q, method and threshold as a `screen` row. The batch rules now live in one place (`check_screen_batch`), shared by the command and by `run_screen`. |
