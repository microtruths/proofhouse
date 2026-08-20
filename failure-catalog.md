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

| **27** | **Hash chain excluded `prev` from canonical bytes — content integrity without history integrity.** Build plan §3 rule 2 said "hash and prev are excluded." The parenthetical justifies excluding `hash` (a record cannot hash its own hash) and extended it to `prev` without justification. `prev` is known before the hash is computed, so it can and must be covered. Because it wasn't, insertion and deletion attacks passed `verify_chain` cleanly — rewriting a record's `prev` didn't change its hash. Tests H-1–H-4 tested the *implementation* ("prev points to the right hash") rather than the *property* ("insertion and deletion are detected"). | Phase 0 build plan §3 (spec error), implemented faithfully | Independent code review + reproduction (insertion/deletion attacks verified programmatically) | **Test the property, not the implementation.** A hash chain that excludes `prev` links records by pointer but does not commit to its own history. Pairs with #26: that was two implementations of one spec; this is one faithful implementation of a wrong spec. Fix: exclude only `hash`. Add insertion/deletion attack tests (H-5, H-6). |

> **Entry #27 provenance.** Found by independent code review 2026-08-14 during Phase 0
> completion audit. The spec was wrong and the implementation was spec-faithful. Fix deployed
> same day while only 2 registry entries + 1 ledger row existed (cheapest moment).

| **28** | **The seam gate was unreachable on the path that mattered, and the registry never read its own consumption record.** Two defects in `blocks.py`, found together. (a) `consume_block` ran E4's single-use check *before* the seam check, and E4 returns early when the same entry re-consumes — so `consume as R → promote to V → consume again` skipped the seam gate entirely, defeating #12 by statement ordering. (b) `promote_block` never consulted `consumed_by`, so one entry could train on a reserve block, promote it to V, and score itself on the same window; the finished record was byte-for-byte identical to a block held clean and validated once. Separately, the live registry carried B-2026-03 labelled **R — untouched** while v11's W6 overlapped it and the cohort pipeline trained through it. Every hash chain verified clean throughout, and `verify` had no block checks at all. | Phase 0 `blocks.py` §4.1, carried through Phases 1–2 | Adversarial probing of the module during the 2026-08-19 ledger reconciliation; the mislabelled block surfaced by hand reconciliation, not by the tool | **Ordering is part of a check's definition.** A guard placed after an early return is not a guard. Pairs with #27 — that was a check testing the wrong thing, this is a correct check on an unreachable line. And: **a record that holds a fact is not the same as a system that reads it.** `consumed_by` had said the block was contaminated all along; three separate readers walked past it. Fix: seam check ahead of E4; promotion to V consults `consumed_by` and requires written acknowledgment to cross; `verify` gains block checks; `blocks acknowledge` attaches an explanation without moving the segment. |

> **Entry #28 provenance.** Found 2026-08-19 by probing `blocks.py` with throwaway scripts
> after Finding 2 of the ledger reconciliation. The finding's own diagnosis — "no way to record
> that a Reserve block turned out contaminated" — was wrong: the record existed. Both real bugs
> were behind it, and neither was what had been reported. **The third audit running, a green
> suite proved nothing and probing found the defects.**

| **29** | **The fix for #28 made the worst available action the cheapest one.** `verify` reports unexplained consumption on a Reserve block as an ERROR and an acknowledged promotion as a WARNING. Because promotion only required an acknowledgment — not that the reading already be explained — the quickest way to make `verify` pass was to promote the contaminated block into **validation**, precisely the act the check exists to prevent. Every individual rule was defensible; the incentive they composed into pointed at the wrong door. Found in the same probing pass, three commits after the fix it followed. | The #28 remediation (2026-08-19), reviewed the same day | Probing the composed behaviour rather than the rules separately — asking "what is the cheapest way to make this check shut up?" | **Audit the gradient, not just the rules.** A control's strength is set by the cost of the cheapest path around it, and that path is a property of the whole system rather than of any one check. Whenever a check has both a hard and a soft outcome, ask which one the lazy route reaches. Fix: explaining is now a prerequisite of promoting, never an alternative to it. |

> **Entry #29 provenance.** Found 2026-08-19 auditing the #28 fix, alongside boolean fill
> inputs being read as numbers (`int(True)` is 1, so `icc: true` silently became perfect
> within-cluster correlation) and `"inf"` being written into a hashed record as a power
> derivation's cap. The audit of a fix found more than the audit that produced it.

| **30** | **The fresh-block rule was enforced against declarations rather than consumption — wrong in both directions at once.** An invalidating amendment must validate on blocks its lineage did not use. The check compared two `validation_blocks` lists, which state *intent* at registration, and never read `consumed_by`, which records what was actually *spent* at gate time. **Too strict:** H-2026-PFR1 was amended the same day it was registered, with all six validation blocks `consumed_by: []` and `manifests/` empty. The amendment was refused, and the only compliant route was to spend four *different* blocks to protect six that were already clean — more data consumed for less isolation. **Too loose:** the loop compared only against immediate parents, so `A → B → C` let C reuse a block A had spent, because C's list overlapped A's and only B was ever examined. One intermediate amendment laundered it. | Phase 0 `registry.py`, carried through Phases 1–2 | Probing the check against the live registry after a research-side spec proposed the fix; the transitive hole was found by asking what the one-level loop could not see | **A check is only as good as the evidence it reads.** `Registry` already held `self._block_registry` — the fact was one attribute away from the code ignoring it, exactly as in #28. Note also the shape of the compensating cost: the fix makes an invalidating amendment on an ungated entry FREE where it used to cost scarce blocks, converting a resource-limited search into an unlimited one. Loosening a constraint on correct grounds can still remove the only thing that was pricing an abuse, and the replacement price has to be named. |

> **Entry #30 provenance.** Fixed 2026-08-19. A pre-existing test named
> `test_a_polarity_change_reusing_parent_blocks_is_refused` documented "a consumed validation
> window" in its docstring and never consumed anything — it had been passing against the
> declaration-only check for the wrong reason, and only failed once the check started reading
> consumption. **A test can encode the bug it was written to prevent.**

| **31** | **A new check reported the CORRECT workflow as broken — and fixing it in one branch left the identical bug in the sibling branch.** `_check_runnable` was added to warn that an entry's validation blocks had been spent by something else. It had no notion of supersession, so the intended path — amend P into S, then gate S — left a permanent error on P: *"P declares B1, which S has already spent."* True, useless, and unclearable, since nothing in the schema retires an entry. Descent was then used as the supersession signal and the **error** branch was fixed. The **warning** branch, four lines below, kept the same blindness: an ordinary PFR1 → PFR2 → PFR3 chain produced six warnings that three entries were contesting blocks none had spent. Found only by running against the live registry, where another session had just built exactly that chain. | The #30 remediation (2026-08-19), reviewed the same day | Probing the intended workflow rather than the failure it was written for; then running the fixed check against real records rather than synthesized ones | **A check that fires on correct usage is worse than no check** — this is the third time this tool has produced an unclearable alarm (cf. #29 and the R-block labelling decision), and the pull toward it is that the check reads as more rigorous. Second, and newer: **when a predicate is wrong, fix every branch that shares it in the same pass.** The error and warning arms of one function were written together, reviewed together, and only one was corrected, because the test that caught the first case did not exercise the second. |

> **Entry #31 provenance.** Found 2026-08-19 auditing #30's fix, hours after it was pushed.
> The warning-branch half was invisible to every synthesized test and obvious on first contact
> with the real registry — the same way #28's cry-wolf bug and the citation resolver's #25 bug
> were both found. **Synthesized fixtures agree with whatever the author was already thinking.**
>
> **A THIRD site of the same predicate**, reported by the research chat the same day and fixed
> alongside: `report.py` counted revisions from the entry's OWN `amendments` list. That list is
> appended only in the invalidating branch, so an entry whose own amendment was ordinary carries
> an empty list while its ancestor carries the invalidation — the disclosure read zero forks for
> a chain that had one. It went silent on H-2026-PFR3, i.e. on precisely the chain where it
> matters most, since PFR2 is the entry that reused blocks under #30's new rule and this
> disclosure is the compensating reader that pays for allowing that. Twice now the fix for
> "walk the lineage" has been applied to the stamper and not to the reader. **Grep for every
> caller of the fact, not every caller of the function.**

| **32** | **Two computed facts with no reader, in the modules S-2 is exempt from — because S-2 guards FUNCTIONS, not FACTS.** (a) `stats.benchmark_sensitivity` computes `sign_flip_detected` and nothing in `core/` reads it; it does not even reach `ScoredResult`, which stores only `{benchmark: alpha}`. A findings memo could print a sweep in which the benchmark **reversed the sign of the result** and say nothing — §2.2.1 measured exactly that, +577 bps under BTC subtraction against −599 under an OI-weighted index on the same data. (b) `costs.check_feasibility` has no caller: an entry registered declaring a 1% rebalance band while its smallest possible trade was 12.5% of a position, with the schema collecting every input the check needed. | Phase 1 `eval/` toolkit modules, carried through Phase 2 | A defect-family sweep after five instances in one day, asking which modules S-2 could not see | **An exemption scoped to functions does not cover the facts they produce.** `costs.py` and `stats.py` are exempt from S-2 as researcher-facing toolkits, and that is correct for `panel_ic` or `deflated_sharpe` — the researcher's `score_fn` calls those. But a *warning* a toolkit produces is not a toolkit function; it is an output that needs a reader regardless of who calls the producer. Fix: derive the sign flip in `report.py` from the alphas already printed rather than plumbing a field — one representation, so the flag cannot disagree with the numbers beside it — and wire feasibility as V-17, refusing only designs infeasible at every declared sleeve size. |

> **Entry #32 provenance.** Found 2026-08-19 by sweeping for more instances of the day's
> recurring families rather than waiting for the next one to surface. Both had been present
> since Phase 1. Note the near-miss in the sweep itself: the first pass chased a function
> called `check_executable`, inferred from a docstring; the function is `check_feasibility`.
> **A name read out of prose is a guess.**

| **33** | **The CLI assumed one statistic per gate, in three copy-pasted places.** `gate` and `report` both built `{scored.statistic: scored}` from the single result a manifest stores. A gate whose criteria name DIFFERENT statistics was therefore unrunnable and unreportable through the supported path — the second criterion resolved to "never produced", which is the pipeline-failure branch, so it raised instead of rendering a verdict. Found by the research chat while integrating H-2026-PFR3, whose gate is `clustered_t_stat >= 2.923 and mean_net_bps_per_event >= 25` — the first real two-statistic gate the tool has seen. | Phase 2c CLI, replicated to Phase 2.5's `report` | Attempting the supported path with a real multi-criterion gate | **An assumption copied to a third site is a design decision nobody made.** The single-statistic shape was never chosen; it was inherited from the first call site, where it happened to be true. Fix: one `statistics_from_result` in `core/`, shared by all three, assembling secondary statistics from the result's own metadata — same run, same content-hashed manifest, so equally trustworthy — and refusing legibly when one is genuinely absent rather than letting the gate report it as a broken pipeline. |

> **Entry #33 provenance.** Fixed 2026-08-19, hours before the gate it blocks was due to run.
> The research chat routed around it by calling `run_gate` directly, which preserves every
> safety property; only the convenience wrapper was bypassed. Worth noting the workaround was
> correct — the defect was in the shortcut, not the machinery.

## Process lessons (meta)

- **Review the diff AND the whole.** Several errors (#17, stale "200 draws" counts, a rejected-suggestion entry contradicting an adopted rule) were merge debris invisible to diff-only review.
- **Every layer paraphrases.** The plan paraphrased Raymond (#23), the agent paraphrased the veto (#18), numpy's defaults "paraphrased" OLS (#22). Countermeasure at every layer: verbatim specs, stated formulas, hand-computed unit tests.
- **Post-failure pressure is the danger zone.** The strongest pull toward protocol violation came immediately after the IC gate failed ("just fix the composite and re-gate"). The correct response was: record the failure, register revised hypotheses for future data (temporal embargo), promote the independent pre-registered hypothesis.
- **Pre-committed decision rules make diagnostics safe.** Checks on already-seen data got 2–3 enumerated outcomes with predetermined branches BEFORE running — turning potential forking paths into mechanical branch selection.
- **Diminishing returns are real.** Rounds 1–3 found structural flaws; round 6 found merge debris; late rounds partially fixed issues earlier rounds introduced (#13→#14). Name it, lock, let data review next.
- **A catalog is itself a locked document.** #26 was numbered and cited before it was written, and the citation dangled for three weeks. Referential integrity is a mechanical check, not a matter of care.
