# tests/test_amend.py — Amendments, rejected ideas, and the re-run lock
"""
Phase 2 Build Plan §6 — hand-computed tests, written before implementation.

A-1  Second gate on the same (entry, blocks) after an amendment -> refused.
A-2  Same gate with FRESH blocks after an amendment -> allowed.
A-3  Re-running with NO intervening amendment -> allowed (idempotent replay).
A-4  An amendment logged AFTER its code ran -> flagged.

The lock carries more weight than it originally would have: Phase 2 §3.3
dropped holdout tokens as redundant, which makes check_rerun_lock the sole
enforcement of the cardinal sin. These are not redundant coverage.
"""

import os
import tempfile

import pytest

from proofhouse.core.amend import (
    AmendmentLog,
    RejectedIdeasLog,
    RerunRefused,
    check_amendment_ordering,
    check_rerun_lock,
    diff_entries,
)
from proofhouse.core.ledger import Ledger


ENTRY = "H-2026-AC10"


def _tmp(name):
    return os.path.join(tempfile.mkdtemp(), name)


def _entry(hash_="h0", threshold="0.05", blocks=None):
    return {
        "id": ENTRY,
        "hash": hash_,
        "validation_blocks": list(blocks or ["B-V-01"]),
        "universe": {"id": "hl-perps-wallets"},
        "gate": {
            "statistic": "ic",
            "combination": "g1",
            "criteria": {
                "g1": {"comparator": ">=",
                       "threshold": {"literal": threshold}},
            },
        },
    }


def _amendment(number=1, anchor="R-000001", after_hash="h1", diff=None):
    """A hand-built amendment record, matching AmendmentLog.file's shape."""
    return {
        "amendment_id": "A-{:04d}".format(number),
        "entry_id": ENTRY,
        "number": number,
        "rationale": "Threshold was mis-transcribed from the locked plan.",
        "impact": "Changes which results pass.",
        "diff": diff or [{"field": "gate.criteria.g1.threshold.literal",
                          "before": "0.05", "after": "0.04"}],
        "invalidating": False,
        "ledger_anchor": anchor,
        "entry_hash_after": after_hash,
    }


def _gate_row(run_id="R-000001", blocks=None, entry_hash="h0"):
    return {
        "run_id": run_id,
        "kind": "gate",
        "registry_ref": ENTRY,
        "blocks_touched": list(blocks or ["B-V-01"]),
        "entry_hash": entry_hash,
    }


class TestA1TheCardinalSin:
    """A-1: gate, amend, re-run on the same window -> refused."""

    def test_a1_rerun_after_amendment_on_same_blocks_is_refused(self):
        with pytest.raises(RerunRefused) as exc:
            check_rerun_lock(
                entry_id=ENTRY,
                requested_blocks=["B-V-01"],
                amendments=[_amendment(anchor="R-000001")],
                ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
            )
        message = str(exc.value)
        assert "cardinal sin" in message
        assert "B-V-01" in message

    def test_a1_partial_overlap_is_still_a_reuse(self):
        """Re-running on {B1, B2} after a pre-amendment gate on {B2, B3}
        re-tests B2. One spent block is enough."""
        with pytest.raises(RerunRefused):
            check_rerun_lock(
                entry_id=ENTRY,
                requested_blocks=["B-V-01", "B-V-02"],
                amendments=[_amendment(anchor="R-000001")],
                ledger_rows=[_gate_row("R-000001", ["B-V-02", "B-V-03"])],
            )

    def test_a1_amendment_anchored_after_a_later_gate_still_refuses(self):
        """Two prior gates; the amendment lands after the second. Re-running
        on the first gate's window is still a re-test."""
        with pytest.raises(RerunRefused):
            check_rerun_lock(
                entry_id=ENTRY,
                requested_blocks=["B-V-01"],
                amendments=[_amendment(anchor="R-000009")],
                ledger_rows=[
                    _gate_row("R-000001", ["B-V-01"]),
                    _gate_row("R-000005", ["B-V-02"]),
                ],
            )

    def test_a1_refusal_names_the_earliest_intervening_amendment(self):
        """Audit finding. The refusal took amendments[0] in list order rather
        than the earliest by anchor, so an unordered list pointed the auditor
        at the wrong change. `for_entry` returns filing order today, but this
        function is public and takes a plain list."""
        late = _amendment(number=2, anchor="R-000009", after_hash="h2")
        early = _amendment(number=1, anchor="R-000002", after_hash="h1")
        early["rationale"] = "the change that actually spent the window"

        with pytest.raises(RerunRefused) as exc:
            check_rerun_lock(
                entry_id=ENTRY, requested_blocks=["B-V-01"],
                amendments=[late, early],          # deliberately out of order
                ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
            )
        assert "actually spent the window" in str(exc.value)
        assert "A-0001" in str(exc.value)

    def test_a1_the_refusal_names_the_amendment_and_its_reason(self):
        """The message has to be actionable — an auditor reading it later
        needs to know which change spent the window."""
        with pytest.raises(RerunRefused) as exc:
            check_rerun_lock(
                entry_id=ENTRY,
                requested_blocks=["B-V-01"],
                amendments=[_amendment(anchor="R-000001")],
                ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
            )
        message = str(exc.value)
        assert "A-0001" in message
        assert "mis-transcribed" in message


class TestA2FreshBlocks:
    """A-2: the only legitimate path forward after an amendment."""

    def test_a2_fresh_blocks_after_an_amendment_are_allowed(self):
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-99"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
        )

    def test_a2_another_entrys_gate_does_not_constrain_this_one(self):
        other = _gate_row("R-000001", ["B-V-01"])
        other["registry_ref"] = "H-2026-OTHER"
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[other],
        )

    def test_a2_a_prior_eval_row_is_not_a_prior_gate(self):
        """Block single-use governs eval runs; the lock governs verdicts. An
        eval on the window does not itself trip the lock."""
        eval_row = _gate_row("R-000001", ["B-V-01"])
        eval_row["kind"] = "eval"
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[eval_row],
        )


class TestA3IdempotentReplay:
    """A-3: re-running with no intervening amendment is allowed.

    §9 named the risk: a lock that blocks legitimate re-runs becomes an
    obstacle people route around, which is worse than no lock.
    """

    def test_a3_replay_with_no_amendment_at_all(self):
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[],
            ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
        )

    def test_a3_amendment_filed_before_the_gate_ran_does_not_lock_it(self):
        """Amend, then gate, then replay. The gate already reflected the
        amendment, so the replay repeats nothing."""
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[_gate_row("R-000002", ["B-V-01"])],
        )

    def test_a3_no_prior_gate_is_always_allowed(self):
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[],
        )

    def test_a3_empty_requested_blocks_is_not_a_reuse(self):
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=[],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
        )

    def test_a3_anchor_boundary_same_run_number_counts_as_after(self):
        """An amendment anchored AT the gate's run was filed once that run
        existed — therefore after it. The boundary resolves toward refusing,
        because the ambiguous case is the one worth being strict about."""
        with pytest.raises(RerunRefused):
            check_rerun_lock(
                entry_id=ENTRY,
                requested_blocks=["B-V-01"],
                amendments=[_amendment(anchor="R-000001")],
                ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
            )

    def test_a3_anchor_one_before_does_not_lock(self):
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor="R-000001")],
            ledger_rows=[_gate_row("R-000002", ["B-V-01"])],
        )

    def test_a3_null_anchor_precedes_everything(self):
        """An amendment filed when the ledger was empty cannot postdate a
        run."""
        check_rerun_lock(
            entry_id=ENTRY,
            requested_blocks=["B-V-01"],
            amendments=[_amendment(anchor=None)],
            ledger_rows=[_gate_row("R-000001", ["B-V-01"])],
        )


class TestA4OrderingOfTheRecord:
    """A-4: an amendment logged after its code ran is flagged."""

    def test_a4_run_using_a_version_whose_amendment_was_not_yet_filed(self):
        """The gate ran against h1 — the version amendment 1 produces — but
        that amendment was not filed until after the run."""
        findings = check_amendment_ordering(
            entry_id=ENTRY,
            original_hash="h0",
            amendments=[_amendment(number=1, anchor="R-000005",
                                   after_hash="h1")],
            ledger_rows=[_gate_row("R-000001", entry_hash="h1")],
        )
        assert len(findings) == 1
        assert findings[0]["run_id"] == "R-000001"
        assert "not filed until after this run" in findings[0]["reason"]

    def test_a4_correct_ordering_is_clean(self):
        findings = check_amendment_ordering(
            entry_id=ENTRY,
            original_hash="h0",
            amendments=[_amendment(number=1, anchor="R-000001",
                                   after_hash="h1")],
            ledger_rows=[
                _gate_row("R-000001", entry_hash="h0"),
                _gate_row("R-000002", entry_hash="h1"),
            ],
        )
        assert findings == []

    def test_a4_unknown_version_is_flagged(self):
        """A run against a hash that appears nowhere in the chain means the
        record does not explain how the entry reached that state."""
        findings = check_amendment_ordering(
            entry_id=ENTRY,
            original_hash="h0",
            amendments=[_amendment(number=1, anchor="R-000001",
                                   after_hash="h1")],
            ledger_rows=[_gate_row("R-000002", entry_hash="hZZZ")],
        )
        assert len(findings) == 1
        assert "appears nowhere" in findings[0]["reason"]

    def test_a4_missing_entry_hash_is_reported_not_passed(self):
        """An uncheckable record must not read as a clean one."""
        row = _gate_row("R-000001")
        del row["entry_hash"]
        findings = check_amendment_ordering(
            entry_id=ENTRY, original_hash="h0",
            amendments=[_amendment()], ledger_rows=[row],
        )
        assert len(findings) == 1
        assert "cannot be established" in findings[0]["reason"]

    def test_a4_multiple_amendments_track_the_version_in_force(self):
        findings = check_amendment_ordering(
            entry_id=ENTRY,
            original_hash="h0",
            amendments=[
                _amendment(number=1, anchor="R-000001", after_hash="h1"),
                _amendment(number=2, anchor="R-000003", after_hash="h2"),
            ],
            ledger_rows=[
                _gate_row("R-000001", entry_hash="h0"),
                _gate_row("R-000002", entry_hash="h1"),
                _gate_row("R-000004", entry_hash="h2"),
            ],
        )
        assert findings == []


class TestAmendmentLog:
    """Filing discipline — the fields that make the record worth having."""

    def test_rationale_and_impact_are_required(self):
        log = AmendmentLog(_tmp("amendments.jsonl"))
        before, after = _entry(), _entry(threshold="0.04")

        with pytest.raises(ValueError):
            log.file(ENTRY, before, after, rationale="", impact="x")
        with pytest.raises(ValueError):
            log.file(ENTRY, before, after, rationale="x", impact="   ")

    def test_empty_diff_is_refused(self):
        """A governance record corresponding to no event is noise in a chain
        whose value is that every row means something."""
        log = AmendmentLog(_tmp("amendments.jsonl"))
        with pytest.raises(ValueError) as exc:
            log.file(ENTRY, _entry(), _entry(),
                     rationale="r", impact="i")
        assert "empty diff" in str(exc.value)

    def test_a12_shape_a_verdict_preserving_change_is_still_filed(self):
        """A-12 changed a schedule, touched no threshold or comparator, and
        was still filed as a numbered amendment. Deviating from a locked
        procedure goes through the process, not around it."""
        log = AmendmentLog(_tmp("amendments.jsonl"))
        before = _entry()
        after = _entry()
        after["validation_blocks"] = ["B-V-01", "B-V-02"]

        record = log.file(
            ENTRY, before, after,
            rationale="W8 cannot change the verdict; leaving it unspent.",
            impact="None on the verdict. Preserves one validation block.",
            ledger_anchor="R-000007",
        )
        assert record["number"] == 1
        assert record["ledger_anchor"] == "R-000007"
        assert record["diff"][0]["field"] == "validation_blocks"

    def test_numbering_is_per_entry_and_sequential(self):
        log = AmendmentLog(_tmp("amendments.jsonl"))
        log.file(ENTRY, _entry(), _entry(threshold="0.04"),
                 rationale="r", impact="i")
        log.file(ENTRY, _entry(threshold="0.04"), _entry(threshold="0.03"),
                 rationale="r", impact="i")

        other_before = dict(_entry(), id="H-OTHER")
        other_after = dict(_entry(threshold="0.04"), id="H-OTHER")
        log.file("H-OTHER", other_before, other_after,
                 rationale="r", impact="i")

        assert [a["number"] for a in log.for_entry(ENTRY)] == [1, 2]
        assert [a["number"] for a in log.for_entry("H-OTHER")] == [1]

    def test_log_is_chained_and_verifies(self):
        path = _tmp("amendments.jsonl")
        log = AmendmentLog(path)
        log.file(ENTRY, _entry(), _entry(threshold="0.04"),
                 rationale="r", impact="i")
        log.file(ENTRY, _entry(threshold="0.04"), _entry(threshold="0.03"),
                 rationale="r", impact="i")

        ok, errors = log.verify()
        assert ok, errors
        assert AmendmentLog(path).verify()[0]

    def test_tampering_with_a_filed_amendment_is_detected(self):
        path = _tmp("amendments.jsonl")
        log = AmendmentLog(path)
        log.file(ENTRY, _entry(), _entry(threshold="0.04"),
                 rationale="original reason", impact="i")

        with open(path) as fh:
            content = fh.read()
        with open(path, "w") as fh:
            fh.write(content.replace("original reason", "rewritten reason"))

        ok, _ = AmendmentLog(path).verify()
        assert ok is False


class TestRejectedIdeasLog:
    """A rejected idea later adopted in part must leave a trace."""

    def test_rejection_requires_a_reason(self):
        log = RejectedIdeasLog(_tmp("rejected.jsonl"))
        with pytest.raises(ValueError):
            log.reject(idea="Drop the BY correction", reason="")
        with pytest.raises(ValueError):
            log.reject(idea="", reason="too weak")

    def test_rejection_is_recorded_and_chained(self):
        path = _tmp("rejected.jsonl")
        log = RejectedIdeasLog(path)
        log.reject(
            idea="Widen the IC tolerance so W3 reproduces.",
            reason="That is tolerance-tuning to reach a target — failure "
                   "catalog #18. Investigate the mismatch instead.",
            entry_id=ENTRY,
            ledger_anchor="R-000003",
        )
        rows = RejectedIdeasLog(path).all_rows()
        assert len(rows) == 1
        assert rows[0]["entry_id"] == ENTRY
        assert "#18" in rows[0]["reason"]
        assert RejectedIdeasLog(path).verify()[0]


class TestDiff:
    """The diff is what an auditor actually reads."""

    def test_nested_fields_get_dotted_paths(self):
        changes = diff_entries(_entry(), _entry(threshold="0.04"))
        assert changes == [{
            "field": "gate.criteria.g1.threshold.literal",
            "before": "0.05",
            "after": "0.04",
        }]

    def test_chain_bookkeeping_is_not_a_semantic_change(self):
        """Every amendment rewrites the hash by construction; showing it
        would bury the field that actually moved."""
        changes = diff_entries(_entry(hash_="h0"), _entry(hash_="h1"))
        assert changes == []

    def test_added_and_removed_fields_appear(self):
        before = {"a": 1}
        after = {"b": 2}
        fields = {c["field"] for c in diff_entries(before, after)}
        assert fields == {"a", "b"}

    def test_floats_are_normalized_to_decimal_strings(self):
        """Canonical rule 3: no floats in a hashed record."""
        changes = diff_entries({"x": 0.1}, {"x": 0.2})
        assert isinstance(changes[0]["before"], str)
        assert isinstance(changes[0]["after"], str)

    def test_list_change_is_reported_whole(self):
        changes = diff_entries(
            {"validation_blocks": ["B1"]},
            {"validation_blocks": ["B1", "B2"]},
        )
        assert changes[0]["before"] == ["B1"]
        assert changes[0]["after"] == ["B1", "B2"]


class TestLedgerEntryHash:
    """entry_hash must be inside the hashed payload, not bolted on after."""

    def test_entry_hash_survives_reload_and_verifies(self):
        path = _tmp("ledger.jsonl")
        ledger = Ledger(path)
        ledger.append_row(
            code_hash="c", data_hash="d", universe="u", kind="gate",
            blocks_touched=["B-V-01"], stats_scored=["ic"],
            registry_ref=ENTRY, outcome="fail", entry_hash="h0",
        )
        reloaded = Ledger(path)
        ok, errors = reloaded.verify()
        assert ok, errors
        assert reloaded.all_rows()[0]["entry_hash"] == "h0"

    def test_omitting_entry_hash_leaves_prior_row_hashes_unchanged(self):
        """Canonical serialization drops nulls, so adding the field must not
        disturb any record written before it existed."""
        path_a, path_b = _tmp("a.jsonl"), _tmp("b.jsonl")
        kwargs = dict(
            code_hash="c", data_hash="d", universe="u", kind="eval",
            blocks_touched=["B-E-01"], stats_scored=["ic"],
        )
        Ledger(path_a).append_row(**kwargs)
        Ledger(path_b).append_row(entry_hash=None, **kwargs)
        assert (Ledger(path_a).all_rows()[0]["hash"]
                == Ledger(path_b).all_rows()[0]["hash"])
