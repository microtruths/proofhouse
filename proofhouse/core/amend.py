# proofhouse.core.amend — amendments, rejected ideas, and the re-run lock
"""
Phase 2b — the amendment protocol and the lock that makes it binding.

WHY THE LOCK CARRIES MORE WEIGHT THAN THE PLAN ORIGINALLY GAVE IT
------------------------------------------------------------------
The parent plan specified two mechanisms against re-testing spent data: a
one-time holdout token consumed on read, and this lock. Phase 2 §3.3 dropped
the token as redundant — block single-use plus this lock cover the rule, and
two mechanisms enforcing one rule is how they drift apart.

That decision makes `check_rerun_lock` the SOLE enforcement of what the
original brief calls the cardinal sin: *"Blocks re-running a gate on the same
window after a modification."* There is no second net. Everything in this
module is written on that assumption.

ORDERING IS ANCHORED, NOT TIMESTAMPED
--------------------------------------
Whether an amendment came before or after a gate run decides whether a re-run
is legitimate. Timestamps are second-resolution, so two events in the same
second are ambiguous — and the ambiguous case is precisely the one someone
would exploit. Every amendment therefore records `ledger_anchor`: the run_id
of the last ledger row at filing time. That yields a total order over the two
logs that does not depend on clock resolution, and an auditor can check it by
reading integers rather than trusting a wall clock.

WHAT "LOGGED BEFORE THE CODE RUNS" MEANS MECHANICALLY
------------------------------------------------------
§3.1 requires an amendment to be logged before the corresponding code runs.
That is unfalsifiable unless runs record WHICH VERSION of the entry they used,
so gate rows carry `entry_hash` and amendments carry `entry_hash_after`.
`check_amendment_ordering` then reconstructs which version each run should
have been using and flags any run that used a version whose amendment had not
yet been filed. Without those two fields the requirement is a comment.
"""

import json
import os
from datetime import datetime, timezone

from proofhouse.chain.canonical import decimal_str
from proofhouse.chain.chain import verify_chain
from proofhouse.chain.hashing import content_hash


def _run_number(run_id):
    """Extract the ordinal from a run_id like 'R-000042' -> 42.

    Every log in proofhouse numbers from a single shared counter, so this is a
    total order across kinds (R-, SCR-, OVL-).
    """
    if not run_id:
        return 0
    try:
        return int(str(run_id).rsplit("-", 1)[-1])
    except ValueError:
        raise ValueError("Unparseable run_id: {!r}".format(run_id))


def _normalize(value):
    """Make a diff value safe for a hashed record.

    Canonical rule 3 forbids floats in hashed records so an independent
    verifier reproduces identical bytes. A threshold recorded as 0.05 and
    re-read as 0.05000000000000000277 would break the chain for a reader who
    round-trips through a different JSON library.
    """
    if isinstance(value, float):
        return decimal_str(value)
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    return value


def diff_entries(before, after, _prefix=""):
    """Field-level diff between two entry versions.

    Returns:
        List of {'field': dotted.path, 'before': ..., 'after': ...}, sorted by
        field. Dict values are walked; lists are compared whole, since a
        reordered list is a change worth seeing in full rather than as a set
        of positional edits.
    """
    changes = []
    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}

    # Chain bookkeeping is not a semantic change — every amendment rewrites
    # the hash by construction, so including it would make every diff noisy
    # and bury the field that actually moved.
    ignored = {"hash", "prev", "status", "amendments"}

    for key in sorted(set(before) | set(after)):
        if not _prefix and key in ignored:
            continue
        path = "{}.{}".format(_prefix, key) if _prefix else key
        old = before.get(key)
        new = after.get(key)
        if isinstance(old, dict) or isinstance(new, dict):
            changes.extend(diff_entries(old, new, path))
            continue
        if old != new:
            changes.append({
                "field": path,
                "before": _normalize(old),
                "after": _normalize(new),
            })
    return changes


class _ChainedLog:
    """Append-only, content-hashed, prev-chained JSONL.

    Same construction as Ledger: `prev` is inside the hashed payload, so both
    insertion and deletion are detectable. Failure catalog #27 is the record
    of what happens when it is not — a chain that excluded `prev` verified
    clean after a row was removed.
    """

    _prefix = "X"

    def __init__(self, path):
        self._path = path
        self._rows = []
        self._next = 1
        self._last_hash = None

        if os.path.exists(path):
            with open(path) as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self._rows.append(row)
                    self._last_hash = row.get("hash")
                    self._next += 1

    def _write_and_chain(self, row):
        if self._last_hash is not None:
            row["prev"] = self._last_hash
        row["hash"] = content_hash(row)
        self._last_hash = row["hash"]
        self._rows.append(row)

        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self._path, "a") as fh:
            # json.dumps, not canonical_bytes: the latter strips `hash`, which
            # is right for hashing and wrong for storage. Reload must see it.
            fh.write(json.dumps(
                row, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False) + "\n")
        return row

    def all_rows(self):
        return [dict(r) for r in self._rows]

    def verify(self):
        """Verify hash-chain integrity. Returns (ok, errors)."""
        return verify_chain(self._rows)


class AmendmentLog(_ChainedLog):
    """Numbered, dated amendments with rationale, impact, and a field diff.

    The shape to support is what v11 actually needed — A-1 through A-12,
    including A-12's verdict-preserving schedule change, which touched no
    hypothesis, threshold or comparator and was *still* filed as a numbered
    amendment. That discipline is the point: deviating from a locked
    procedure, even provably harmlessly, goes through the process rather than
    around it.
    """

    _prefix = "A"

    def file(self, entry_id, before, after, rationale, impact,
             ledger_anchor=None, invalidating=False):
        """File an amendment. Numbered per entry, in filing order.

        Args:
            entry_id: The entry being amended.
            before: The entry dict as registered/last amended.
            after: The entry dict as it will now stand.
            rationale: WHY. Required, non-empty.
            impact: What this changes about the result's interpretation.
                Required, non-empty.
            ledger_anchor: run_id of the last ledger row at filing time. This
                is what orders the amendment against gate runs; passing None
                means "filed before any run existed".
            invalidating: True when the change invalidates the
                pre-registration (polarity, expected_effect_size), which
                additionally requires fresh blocks — enforced in
                Registry.register and again in run_eval.

        Returns:
            The amendment record.

        Raises:
            ValueError: On empty rationale/impact, or an empty diff.
        """
        if not rationale or not str(rationale).strip():
            raise ValueError(
                "An amendment requires a rationale. The record exists to make "
                "the reason auditable later; an unexplained amendment is the "
                "registry rot the protocol is meant to prevent."
            )
        if not impact or not str(impact).strip():
            raise ValueError(
                "An amendment requires an impact assessment — what this "
                "changes about how the result may be read. A-12 filed one "
                "even for a change that provably could not move the verdict."
            )

        changes = diff_entries(before, after)
        if not changes:
            raise ValueError(
                "Amendment for {} has an empty diff — nothing changed. Filing "
                "a no-op amendment puts a governance record in the chain that "
                "corresponds to no event.".format(entry_id)
            )

        number = 1 + sum(
            1 for r in self._rows if r.get("entry_id") == entry_id
        )

        row = {
            "amendment_id": "{}-{:04d}".format(self._prefix, self._next),
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entry_id": entry_id,
            "number": number,
            "rationale": str(rationale).strip(),
            "impact": str(impact).strip(),
            "diff": changes,
            "invalidating": bool(invalidating),
            "ledger_anchor": ledger_anchor,
            # The version this amendment produces. Paired with `entry_hash` on
            # gate rows, this is what makes "logged before the code ran"
            # checkable rather than asserted.
            "entry_hash_after": (after or {}).get("hash"),
        }
        self._next += 1
        return self._write_and_chain(row)

    def for_entry(self, entry_id):
        """Amendments for one entry, in filing order."""
        return [dict(r) for r in self._rows
                if r.get("entry_id") == entry_id]


class RejectedIdeasLog(_ChainedLog):
    """Ideas considered and rejected.

    Alongside the amendment log so that a rejected idea later adopted in part
    leaves a trace. The original brief predicted registry rot as the way this
    discipline decays, and #26 demonstrated the shape: a record that cites
    something which is not actually there. An idea that was rejected, then
    quietly absorbed, is the same defect running backwards.
    """

    _prefix = "X"

    def reject(self, idea, reason, entry_id=None, ledger_anchor=None):
        """Record a rejected idea.

        Args:
            idea: What was considered.
            reason: Why it was rejected. Required — the reason is the whole
                value of the record. "we didn't like it" six months on is
                indistinguishable from having never considered it.
            entry_id: Optional entry this relates to.
            ledger_anchor: run_id of the last ledger row at rejection time.
        """
        if not idea or not str(idea).strip():
            raise ValueError("A rejection requires the idea it rejects.")
        if not reason or not str(reason).strip():
            raise ValueError(
                "A rejection requires a reason. Without one the record cannot "
                "distinguish a considered rejection from a forgotten idea."
            )

        row = {
            "rejection_id": "{}-{:04d}".format(self._prefix, self._next),
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "idea": str(idea).strip(),
            "reason": str(reason).strip(),
            "entry_id": entry_id,
            "ledger_anchor": ledger_anchor,
        }
        self._next += 1
        return self._write_and_chain(row)


class RerunRefused(Exception):
    """A gate re-run would repeat a spent window after a modification.

    NOT a failing gate. Nothing is recorded as an outcome — the only path
    forward is a validation window that postdates the amendment.
    """


def gate_rows_for(ledger_rows, entry_id):
    """Prior gate rows for one entry, oldest first."""
    return [
        r for r in ledger_rows
        if r.get("kind") == "gate" and r.get("registry_ref") == entry_id
    ]


def check_rerun_lock(entry_id, requested_blocks, amendments, ledger_rows):
    """Refuse a gate re-run on a window already spent before an amendment.

    THE RULE: a second gate evaluation for the same (entry, validation_blocks)
    after any amendment to that entry is refused. The only path forward is a
    new validation window postdating the amendment.

    Enforcement is at EVALUATION time, not registration time. Phase 1's review
    established why that distinction matters: the registration-time amendment
    check was bypassable because `validation_blocks` is empty at registration,
    so the overlap test passed trivially. A check on a field that is empty
    when the check runs is not a check.

    WHAT IS DELIBERATELY ALLOWED:
      * Re-running with no intervening amendment (idempotent replay). A
        pipeline repair produces a FIRST evaluation, not a second — §2.5 holds
        that pipeline failure is not a gate outcome, so no verdict was
        recorded to repeat. If this were refused, the lock would become an
        obstacle people route around, which is worse than not having it.
      * Re-running on blocks the pre-amendment gate did not touch.

    Args:
        entry_id: The entry being gated.
        requested_blocks: Blocks this evaluation would use.
        amendments: Amendment records for this entry (from
            AmendmentLog.for_entry).
        ledger_rows: All ledger rows (from Ledger.all_rows).

    Raises:
        RerunRefused: If the evaluation would repeat a spent window.
    """
    requested = set(requested_blocks or [])
    if not requested:
        return

    priors = gate_rows_for(ledger_rows, entry_id)
    if not priors or not amendments:
        return

    # An amendment anchored at run N was filed after run N was written.
    anchors = [
        (_run_number(a.get("ledger_anchor")), a) for a in amendments
    ]

    for row in priors:
        overlap = requested & set(row.get("blocks_touched") or [])
        if not overlap:
            continue
        gate_number = _run_number(row.get("run_id"))
        intervening = [a for anchor, a in anchors if anchor >= gate_number]
        if not intervening:
            continue
        first = intervening[0]
        raise RerunRefused(
            "Entry {} was gated on {} at {}, then amended (#{}: {}). Re-running "
            "the gate on {} would re-test a window already spent under a "
            "different specification — the cardinal sin. Register a validation "
            "window that postdates the amendment instead.\n"
            "  amendment  : {}\n"
            "  rationale  : {}".format(
                entry_id, sorted(overlap), row.get("run_id"),
                first.get("number"),
                ", ".join(c["field"] for c in first.get("diff", [])) or "(no diff)",
                sorted(overlap),
                first.get("amendment_id"),
                first.get("rationale"),
            )
        )


def check_amendment_ordering(entry_id, original_hash, amendments, ledger_rows):
    """Flag runs that used an entry version before its amendment was filed.

    §3.1: an amendment is *logged before the corresponding code runs*. This
    reconstructs, for each gate row, which entry version was in force when it
    ran, and compares that against the version the row says it used.

    Args:
        entry_id: The entry to check.
        original_hash: Hash of the entry as first registered.
        amendments: Amendment records for this entry, in filing order.
        ledger_rows: All ledger rows.

    Returns:
        List of finding dicts, each with run_id, expected, actual and reason.
        Empty when the ordering is clean.
    """
    findings = []
    ordered = sorted(amendments, key=lambda a: a.get("number", 0))
    later_hashes = {
        a.get("entry_hash_after"): a for a in ordered
        if a.get("entry_hash_after")
    }

    for row in gate_rows_for(ledger_rows, entry_id):
        actual = row.get("entry_hash")
        if not actual:
            # A gate row with no entry_hash cannot be checked. Say so rather
            # than passing it silently — an uncheckable record must not read
            # as a clean one.
            findings.append({
                "run_id": row.get("run_id"),
                "expected": None,
                "actual": None,
                "reason": "gate row records no entry_hash, so the version it "
                          "used cannot be established",
            })
            continue

        gate_number = _run_number(row.get("run_id"))
        in_force = original_hash
        for amendment in ordered:
            if _run_number(amendment.get("ledger_anchor")) < gate_number:
                in_force = amendment.get("entry_hash_after")
        if actual == in_force:
            continue

        culprit = later_hashes.get(actual)
        if culprit is not None:
            reason = (
                "ran entry version produced by amendment {} (#{}), which was "
                "not filed until after this run. The amendment must be logged "
                "before the code it authorizes runs.".format(
                    culprit.get("amendment_id"), culprit.get("number"))
            )
        else:
            reason = (
                "ran an entry version that appears nowhere in the amendment "
                "chain — the record does not explain how the entry reached "
                "this state."
            )
        findings.append({
            "run_id": row.get("run_id"),
            "expected": in_force,
            "actual": actual,
            "reason": reason,
        })

    return findings
