# Failure Catalog — proofhouse canonical copy

> **Provenance.** Entries #1–#25 are the AgentCopy Combined-Signal Experiment catalog (Jul 2026),
> copied verbatim. Entry **#26** was filed by reference in `V10_CYCLE_REPORT.md:142` and errata
> A-11 (2026-07-22) but never appended to the original catalog, which had been frozen at #25
> covering "plan versions v1–v10, and amendments A-1–A-3." **Landed here 2026-08-14.**
>
> **This copy is canonical for the proofhouse project.** Numbering is append-only. Other copies
> (`Alamancy/failure_prevention_skills/`, `multi_workstream_ideas/`) are references, not sources.

Every error found across 6 review rounds, 6+ agent reviews, plan versions v1–v10, and amendments A-1–A-3. Each entry: what happened, who made it, how it was caught, the general lesson. **Read this before reviewing any experiment plan or implementing any spec.** These are calibration examples — the abstract checklists in the skills correspond to these concrete instances.

Errors were made by every participant: the human's plan drafts, the primary reviewer (Fable), and executing agents. Treat reviewer output with the same skepticism as author output.

## Design errors (found in plan review)

| # | Failure | Made by | Caught by | Lesson |
|---|---------|---------|-----------|--------|
| 1 | Universe gates ("≥6mo active", "$25K at some point") evaluated on full-period data → survivorship bias in every window | v1 plan | Tracing what data each gate could see at each window's selection date | Per-window universes from pre-cutoff data only. Trace data lineage per window. |
| 2 | Sensitivity window W8's test period duplicated the holdout's → holdout contaminated during development | v1 plan | Listing every window's test period and checking for overlaps with the holdout | Enumerate all test periods; holdout period may appear exactly once. |
| 3 | Paired t-tests across n=5 windows can't reach p<0.10 unless 5/5 — gate unpassable by construction | v1 plan | Computing the minimum achievable p for the test at that n | Check every gate is *passable*; compute the math of the test at the actual N. |
| 4 | Reviewer recommended pooled daily returns as "~300 observations" — overstating effective N (60 correlated days per fixed cohort ≈ 1 unit) | **Fable (reviewer)** | Later agent review round | Effective N = independent units. Reviewers overclaim too; re-derive their statistics. |
| 5 | Baseline S1 included the new HFT filter → S3-beats-S1 unattributable (filter vs signal) | v1 plan | Asking "what would production actually have produced?" | Include a true production-replication baseline; decompose attribution stepwise. |
| 6 | Test data for W5/W6 only existed for a prefiltered wallet subset (prefiltered with look-ahead); selected wallets could lack test data | v2 plan | Cross-checking each window's test period against the data inventory table | For each window, verify test-period coverage for every wallet each strategy could select. |
| 7 | Ingest whitelist covered shared universe but not S0's different pool → coverage gap reintroduced for the baseline only | v3 plan | Re-checking the fix for issue #6 against ALL strategies' pools | A fix scoped to the main case often misses the differently-pooled baseline. |
| 8 | Holdout had no pre-registered decision rule — what "failing W6" meant was undefined | v2 plan | Simulating the decision tree: "winner passes dev, loses on W6 — then what?" | Walk every branch of the decision tree to a defined action. |
| 9 | Three combined strategies vs one p<0.10 gate ≈ 27% false-pass | v2 plan | Counting shots on goal | One primary hypothesis; fixed-sequence for the rest. |
| 10 | IC gate run on W3 whose data then fed development stats → selection bias in W3 | v2 plan | Asking what the experiment's existence was conditioned on | Gate windows are excluded from primary statistics; residual leakage named. |
| 11 | Rolling-60-day β inside a ~60-day training window → NaN for nearly all events | v1–v5 plans | Agent review checking estimator feasibility at actual window lengths | Check every estimator's data requirements against the actual window sizes. |
| 12 | W5 test period spliced two data sources at Apr 1 with zero overlap → pipeline differences would appear as a phantom regime break | v7 plan | Mapping data-source boundaries against window boundaries | Splices need overlap + reconciliation; never let a seam sit unvalidated inside a test period. |
| 13 | Pooled per-coin β on direction-adjusted returns would cancel toward zero across mixed long/short wallets ("cancellation paradox") | Fable's v8 suggestion | Agent review | Pooling direction-adjusted quantities across entities with opposing signs destroys the estimate. |
| 14 | Per-wallet β fix (#13) created sparse (wallet,coin) cells: 2-point OLS fits exactly, residuals collapse | v9 plan (fixing #13) | Reviewing what the new fix does at its edge cases | Every fix creates new edge cases; review the fix, not just the fixed. |
| 15 | Kill-switch "gate fail → stop everything" written when the plan had one hypothesis (v2); still applied after an independent hypothesis (S6) was added (v7) | v2→v10 drift | Asking whether the consequence still made sense for each current hypothesis | Re-derive consequence rules whenever the hypothesis set changes. |

## Randomization-test errors

| # | Failure | Made by | Caught by | Lesson |
|---|---------|---------|-----------|--------|
| 16 | S6 null flipped only the raw directional return; the −β·btc term was identical in realized and every null draw → **β correction cancelled out of the p-value entirely**; beta-riders would classify as skilled in trending windows | **Fable (amendment spec)**, carried into v7 | Expanding the null statistic algebraically and checking which terms cancel | Expand permutation statistics by hand; anything constant across draws is not being tested. |
| 17 | Stale justification: "β held fixed because it's universe-pooled, one wallet's flips can't move it" survived after β became per-wallet; the replacement justification ("β stable under flips") was also wrong (sign flips change direction-adjusted β *systematically*) | v8→v9 drift | Fresh internal-consistency pass | The correct rationale was structural (null operates on fixed realized residuals — sign-exchangeability; β never re-estimated *by construction*). When a design changes, hunt down every justification written for the old design. |

## Execution errors (agent-implemented checks)

| # | Failure | Made by | Caught by | Lesson |
|---|---------|---------|-----------|--------|
| 18 | Safety-veto check implemented as `max(0.02×notional, $2000)`; the spec was `50% × max(cum_gains, $2000)` (floor $1,000, cumulative-gains-based) — agent paraphrased the rule from memory | Executing agent (A-2 Check 2) | Reviewer reading the agent's code against the spec text | **Paste specs verbatim into implementations.** Plausible ≠ specified. |
| 19 | "H-B2 would catch the survivors" argued from their 0% *OOS* win rate — but H-B2 screens on *training* traits | Executing agent (A-2) | Asking what data the filter actually sees at selection time | Filters are evaluated on the data they'd see, never on outcomes. |
| 20 | Corrected veto scan (A-3) still ran on episodes through Dec — including OOS. Selection-time answer (through Oct 31): 1/10, not 2/10 | Executing agent (A-3) | Checking the scan's time scope against the decision question | Even errata have scope; selection-time vs full-period must be explicit, both reported. |
| 21 | A-1 verification reported "no-intercept β (cov/var formula)" — self-contradictory (cov/var IS the with-intercept slope) | Executing agent | Reviewer noticing the summary contradicted its own parenthetical | If a one-line classification is internally inconsistent, the verification failed. |
| 22 | Revised A-1 claimed "hybrid convention, mean residual ≠ α̂" — false under matched ddof (mean(y−βx) = ȳ−βx̄ = α̂ exactly). The REAL deviation: `np.cov` ddof=1 vs `np.var` ddof=0 → β inflated ×n/(n−1) ≈ 14% at the 8-event sparse floor | Executing agent (Opus) | Reviewer re-deriving the algebra, then checking numpy defaults | Verify conventions by derivation + library-default audit, not by naming. Sparse cells amplify default mismatches. |
| 23 | Borrowed method's trait polarity (payoff ratio inverted = good) had *negative* IC on this data — actively destroying the composite | Raymond's framework, imported by plan | Phase 0.5 replication gate + trait decomposition | Cheap replication gates on borrowed methods before building on them; decompose composites to traits. |
| 24 | Headline +11.1 bps top-vs-bottom differential was a mean under skew=−66, std=154×mean; median was +2.53 bps | Phase 0.5 report | Demanding the median alongside any mean under extreme skew | Means under extreme skew are not numbers; the signal was a bad-wallet detector, not a good-wallet picker. |
| 25 | Wallet-level "return" of −1,026,952 bps (−102×) — denominator was per-episode notional, not capital | Phase 0.5 metric | Impossible-magnitude check | Any impossible magnitude means the denominator isn't what the name implies; document it. |
| **26** | **Two implementations of one registered construct coexisted in the pipeline** — `replicate_behavioral_ic.py` (Raymond's registered variant) and `compute_behavioral_features.py` (as-built), with **opposite polarities on payoff_ratio and disposition_ratio**. The Phase 0.5 IC gate tested one; the S2 cohorts were actually built by the other. The composite that failed the gate was never the composite that produced the evidence. | v10 pipeline (implementation drift, undetected across every review round) | §1.2 composite verification — line-by-line code audit against the locked spec, 2026-07-22, during v11 lock preparation | **A registered definition must name the single implementing artifact.** Any second implementation of the same construct is a divergence risk by existence. Critically: the wrong code *survived every review because it read as sensible* — it encoded the orthodox skilled-trader profile rather than the registered contrarian one. Human review cannot catch this class; only a mechanical spec↔code diff can. |

> **Entry #26 provenance.** Filed as `V10_CYCLE_REPORT.md:142` and v10 errata A-11 (2026-07-22).
> Cited from `V11_PRE_REGISTRATION_LOCKED.md` §1.2 while the catalog still ended at #25 —
> a dangling reference from a locked document, itself an instance of the registry-rot failure
> mode. Landed 2026-08-14. See open-decisions register D5.

## Process lessons (meta)

- **Review the diff AND the whole.** Several errors (#17, stale "200 draws" counts, a rejected-suggestion entry contradicting an adopted rule) were merge debris invisible to diff-only review.
- **Every layer paraphrases.** The plan paraphrased Raymond (#23), the agent paraphrased the veto (#18), numpy's defaults "paraphrased" OLS (#22). Countermeasure at every layer: verbatim specs, stated formulas, hand-computed unit tests.
- **Post-failure pressure is the danger zone.** The strongest pull toward protocol violation came immediately after the IC gate failed ("just fix the composite and re-gate"). The correct response was: record the failure, register revised hypotheses for future data (temporal embargo), promote the independent pre-registered hypothesis.
- **Pre-committed decision rules make diagnostics safe.** Checks on already-seen data got 2–3 enumerated outcomes with predetermined branches BEFORE running — turning potential forking paths into mechanical branch selection.
- **Diminishing returns are real.** Rounds 1–3 found structural flaws; round 6 found merge debris; late rounds partially fixed issues earlier rounds introduced (#13→#14). Name it, lock, let data review next.
- **A catalog is itself a locked document.** #26 was numbered and cited before it was written, and the citation dangled for three weeks. Referential integrity is a mechanical check, not a matter of care.
