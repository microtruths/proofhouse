# tests/test_verify.py — full integrity check
"""
Phase 2.5 §5.1 hand-computed tests.

V-1  Tampered entry detected at the right index.
V-2  Dangling citation -> HARD FAILURE (the #26 case, replayed).
V-3  Validation window predating created_utc -> failure.
V-4  Verify runs clean from a fresh clone.

V-2 is the one with teeth, and the one most likely to be softened the first
time it fires on something inconvenient. Failure catalog #26: a locked
document cited catalog entry #26 for three weeks while the catalog ended at
#25. Nothing was tampered with — the citation pointed at nothing, and every
chain verified clean.
"""

import copy
import os
import tempfile

import pytest
import yaml

from proofhouse.core.amend import AmendmentLog
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.fill import FillLog
from proofhouse.core.ledger import Ledger
from proofhouse.core.registry import Registry
from proofhouse.core.verify import catalog_citation_resolver, verify_all

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "fixtures", "agentcopy_v10_ic_gate.yaml",
)


def _blocks():
    registry = BlockRegistry()
    registry.add_block({
        "id": "B-V-01", "start": "2026-09-01", "end": "2026-11-01",
        "segment": "V", "data_complete": True, "completed_at": "2026-11-15",
        "sources": ["hl"], "seams": [],
    })
    registry.add_block({
        "id": "B-OLD", "start": "2024-01-01", "end": "2024-03-01",
        "segment": "V", "data_complete": True, "completed_at": "2024-03-15",
        "sources": ["hl"], "seams": [],
    })
    return registry


def _project(entry_overrides=None):
    root = tempfile.mkdtemp()
    registry = Registry(os.path.join(root, "registry"),
                        block_registry=_blocks())
    with open(FIXTURE) as fh:
        entry = yaml.safe_load(fh)
    if entry_overrides:
        entry = copy.deepcopy(entry)
        entry.update(entry_overrides)
    registry.register(entry)
    return root, registry


def _errors(findings, check=None):
    return [f for f in findings
            if f.severity == "error" and (check is None or f.check == check)]


class TestV4CleanProject:
    """V-4: a well-formed project verifies clean."""

    def test_a_fresh_project_verifies(self):
        root, registry = _project()
        ok, findings = verify_all(
            registry=registry,
            ledger=Ledger(os.path.join(root, "ledger.jsonl")),
            block_registry=_blocks(),
        )
        assert ok, [str(f) for f in findings]

    def test_missing_logs_are_skipped_not_failed(self):
        root, registry = _project()
        ok, _ = verify_all(registry=registry, block_registry=_blocks())
        assert ok


class TestV1Tampering:
    """V-1: chain recomputation, now reaching every log rather than two."""

    def test_a_tampered_amendment_is_reported(self):
        root, registry = _project()
        path = os.path.join(root, "amendments.jsonl")
        log = AmendmentLog(path)
        entry = registry.list_entries()[0]
        after = copy.deepcopy(entry)
        after["gate"]["criteria"]["g1"]["threshold"]["literal"] = "0.04"
        log.file(entry["id"], entry, after, rationale="original reason",
                 impact="i")

        with open(path) as fh:
            content = fh.read()
        with open(path, "w") as fh:
            fh.write(content.replace("original reason", "rewritten reason"))

        ok, findings = verify_all(
            registry=registry, amendment_log=AmendmentLog(path),
            block_registry=_blocks())
        assert ok is False
        assert _errors(findings, "chain")

    def test_a_tampered_fill_is_reported(self):
        root, registry = _project()
        path = os.path.join(root, "fills.jsonl")
        log = FillLog(path)
        log.record("H-2026-AC10", "power_check", "power(a, b, c)", {},
                   {"value": 0.5, "flagged": False, "derivation": {}},
                   "h0", "h1")

        with open(path) as fh:
            content = fh.read()
        with open(path, "w") as fh:
            fh.write(content.replace('"flagged":false', '"flagged":true'))

        ok, findings = verify_all(
            registry=registry, fill_log=FillLog(path),
            block_registry=_blocks())
        assert ok is False
        assert _errors(findings, "chain")


class TestV2DanglingCitations:
    """V-2: the #26 case. A hard failure, never a warning."""

    def _catalog(self, highest):
        path = os.path.join(tempfile.mkdtemp(), "failure-catalog.md")
        with open(path, "w") as fh:
            fh.write("# Failure catalog\n\n")
            for n in range(1, highest + 1):
                fh.write("## {}. Something that went wrong\n\nDetail.\n\n"
                         .format(n))
        return path

    def test_the_26_case_replayed(self):
        """A record citing #26 against a catalog ending at #25."""
        root, registry = _project(
            {"prior_art": ["See failure catalog #26 for the precedent."]})
        resolver = catalog_citation_resolver(self._catalog(25))
        assert resolver.highest == 25

        ok, findings = verify_all(
            registry=registry, block_registry=_blocks(),
            citation_resolver=resolver)

        assert ok is False
        reference_errors = _errors(findings, "references")
        assert reference_errors
        assert "#26" in reference_errors[0].message

    def test_the_same_citation_resolves_once_the_catalog_has_26(self):
        root, registry = _project(
            {"prior_art": ["See failure catalog #26 for the precedent."]})
        ok, findings = verify_all(
            registry=registry, block_registry=_blocks(),
            citation_resolver=catalog_citation_resolver(self._catalog(26)))
        assert ok, [str(f) for f in findings]

    def test_an_unresolvable_citation_is_an_error_not_a_warning(self):
        """The distinction that matters: a warning would let a locked record
        keep claiming support it does not have."""
        root, registry = _project(
            {"prior_art": ["failure catalog #99"]})
        ok, findings = verify_all(
            registry=registry, block_registry=_blocks(),
            citation_resolver=catalog_citation_resolver(self._catalog(25)))
        assert ok is False
        assert all(f.severity == "error"
                   for f in findings if f.check == "references")

    def test_without_a_resolver_citations_are_UNCHECKED_not_passed(self):
        """An unverifiable claim must not read as a verified one. The verify
        still passes — but it says so."""
        root, registry = _project({"prior_art": ["failure catalog #99"]})
        ok, findings = verify_all(
            registry=registry, block_registry=_blocks(),
            citation_resolver=None)
        assert ok is True
        unchecked = [f for f in findings if "not checked" in f.message]
        assert unchecked and unchecked[0].severity == "warning"

    def test_a_dangling_depends_on_is_an_error(self):
        root, registry = _project()
        entry = registry.list_entries()[0]
        entry["depends_on"] = ["H-DOES-NOT-EXIST"]
        ok, findings = verify_all(
            registry=_StubRegistry([entry]), block_registry=_blocks())
        assert ok is False
        assert _errors(findings, "references")

    def test_a_ledger_row_naming_an_unknown_entry_is_an_error(self):
        root, registry = _project()
        ledger = Ledger(os.path.join(root, "ledger.jsonl"))
        ledger.append_row(code_hash="c", data_hash="d", universe="u",
                          kind="gate", blocks_touched=[], stats_scored=["ic"],
                          registry_ref="H-GHOST", outcome="fail")
        ok, findings = verify_all(registry=registry, ledger=ledger,
                                  block_registry=_blocks())
        assert ok is False
        assert any("H-GHOST" in f.message for f in _errors(findings))


class TestV3TemporalEmbargo:
    """V-3: an embargo and a holdout are not equal evidence.

    * Embargo — the window had not finished at registration, so peeking was
      physically impossible and the claim rests on nothing.
    * Holdout — the window had concluded, so "it was not examined" rests
      entirely on attestation.

    The tool cannot adjudicate an attestation. It can refuse to let the weaker
    case pass silently as the stronger one, which is a warning, not an error.

    Found by running verify against the retro-filed v11 W6 entry: W6's test
    period ended 2026-06-30 and v11's original lock was 2026-07-23.
    """

    def test_a_concluded_window_is_reported_as_a_holdout(self):
        root, registry = _project({
            "created_utc": "2026-07-22T00:00:00Z",
            "validation_blocks": ["B-OLD"],       # ends 2024-03-01
        })
        ok, findings = verify_all(registry=registry, block_registry=_blocks())

        temporal = [f for f in findings if f.check == "temporal"]
        assert temporal
        assert temporal[0].severity == "warning"
        assert "HOLDOUT" in temporal[0].message
        assert "attestation" in temporal[0].message

    def test_it_does_not_fail_the_verify(self):
        """A retro-filed entry is a legitimate design, so this must not make
        verify unusable on one. It must also not be silent."""
        root, registry = _project({
            "created_utc": "2026-07-22T00:00:00Z",
            "validation_blocks": ["B-OLD"],
        })
        ok, _ = verify_all(registry=registry, block_registry=_blocks())
        assert ok is True

    def test_a_yaml_date_object_does_not_crash_the_comparison(self):
        """YAML parses an unquoted 2026-07-01 into a date object, and
        comparing that against a string raises rather than returning False."""
        import datetime
        blocks = _blocks()
        blocks.add_block({
            "id": "B-DATE", "start": datetime.date(2024, 1, 1),
            "end": datetime.date(2024, 3, 1), "segment": "V",
            "data_complete": True, "completed_at": "2024-03-15",
            "sources": ["hl"], "seams": [],
        })

        registry = Registry(os.path.join(tempfile.mkdtemp(), "registry"),
                            block_registry=blocks)
        with open(FIXTURE) as fh:
            entry = yaml.safe_load(fh)
        entry["created_utc"] = "2026-07-22T00:00:00Z"
        entry["validation_blocks"] = ["B-DATE"]
        registry.register(entry)

        ok, findings = verify_all(registry=registry, block_registry=blocks)
        assert [f for f in findings if f.check == "temporal"]

    def test_a_window_ending_after_registration_is_fine(self):
        root, registry = _project({
            "created_utc": "2026-07-22T00:00:00Z",
            "validation_blocks": ["B-V-01"],      # ends 2026-11-01
        })
        ok, findings = verify_all(registry=registry, block_registry=_blocks())
        assert ok, [str(f) for f in findings]


class TestOrderingIsActuallyCalled:
    """check_amendment_ordering has existed since 2b with no production call
    site outside its own tests. This is it."""

    def test_a_gate_run_ahead_of_its_amendment_is_reported(self):
        root, registry = _project()
        entry = registry.list_entries()[0]

        ledger = Ledger(os.path.join(root, "ledger.jsonl"))
        ledger.append_row(code_hash="c", data_hash="d", universe="u",
                          kind="gate", blocks_touched=["B-V-01"],
                          stats_scored=["ic"], registry_ref=entry["id"],
                          outcome="fail", entry_hash="h-unknown")

        ok, findings = verify_all(registry=registry, ledger=ledger,
                                  block_registry=_blocks())
        assert ok is False
        assert _errors(findings, "ordering")


class _StubRegistry:
    """Minimal stand-in exposing only what verify_all reads."""

    def __init__(self, entries):
        self._entries = entries

    def list_entries(self):
        return self._entries

    def verify(self):
        return True, []


class TestTheResolverAgainstTheRealCatalog:
    """Regression: the first version of this check was itself broken.

    It read entry numbers as '## 14.' or '| 14 |' and missed the emphasized
    form '| **27** |', which is how the real catalog writes its two most
    recent entries. Against the actual file it reported the catalog as ending
    at #25 — so every legitimate #26 and #27 citation would have been flagged
    as dangling.

    A citation check that cries wolf is worse than no check: it teaches you to
    skip the output, and then the one real dangling reference goes past
    unread. Found by running the check against the real catalog rather than a
    synthesized one.
    """

    CATALOG = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "failure-catalog.md")

    def test_the_real_catalog_parses_to_33(self):
        """Pinned to the real catalog's highest entry.

        Bump this when a catalog entry lands — the test failing on a new
        entry is the pin working, not a regression. #33 landed 2026-08-19.
        """
        resolver = catalog_citation_resolver(self.CATALOG)
        assert resolver is not None
        assert resolver.highest == 33

    def test_emphasized_row_numbers_are_read(self):
        """'| **27** |' is the format that broke it."""
        path = os.path.join(tempfile.mkdtemp(), "catalog.md")
        with open(path, "w") as fh:
            fh.write("| 25 | plain |\n| **26** | bold |\n| _27_ | italic |\n")
        assert catalog_citation_resolver(path).highest == 27

    def test_the_w6_fixtures_own_citation_resolves(self):
        """The W6 fixture cites '(failure catalog #26)' in signal.transform —
        prose, not a citations list, exactly like the original #26 case."""
        resolver = catalog_citation_resolver(self.CATALOG)
        assert resolver("...requiring new registration (failure catalog #26).")

    def test_a_missing_catalog_yields_no_resolver_not_a_permissive_one(self):
        """Absence must not be treated as success."""
        assert catalog_citation_resolver("/nonexistent/catalog.md") is None


class TestCitationsAreCheckedInProseNotJustPriorArt:
    """The #26 case was a citation in a locked document's BODY — v11's
    pre-registration §1.2 — not in a field named prior_art."""

    def _catalog(self, highest):
        path = os.path.join(tempfile.mkdtemp(), "failure-catalog.md")
        with open(path, "w") as fh:
            for n in range(1, highest + 1):
                fh.write("## {}. Entry\n\n".format(n))
        return path

    def test_a_dangling_citation_in_a_prose_field_is_caught(self):
        root, registry = _project()
        entry = registry.list_entries()[0]
        entry["signal"]["transform"] = "Rank wallets (failure catalog #99)."

        ok, findings = verify_all(
            registry=_StubRegistry([entry]), block_registry=_blocks(),
            citation_resolver=catalog_citation_resolver(self._catalog(25)))

        assert ok is False
        errors = _errors(findings, "references")
        assert errors and "signal.transform" in errors[0].message

    def test_prose_without_citations_is_not_reported(self):
        root, registry = _project()
        ok, findings = verify_all(
            registry=registry, block_registry=_blocks(),
            citation_resolver=catalog_citation_resolver(self._catalog(25)))
        assert ok, [str(f) for f in findings]


class TestBlockSegmentsAgreeWithConsumption:
    """A segment claiming a block is untouched, beside a list of things that
    touched it.

    The live registry carried exactly this: B-2026-03 labelled Reserve while
    v11's W6 window overlapped it and the cohort pipeline trained through it.
    Every chain verified clean. It surfaced because a person sat down and
    reconciled the registry by hand, which is the wrong way to find it — the
    module whose entire purpose is consistency BETWEEN records had no block
    checks at all.
    """

    def _reserve_block(self):
        registry = BlockRegistry()
        registry.add_block({
            "id": "B-R-01", "start": "2026-05-25", "end": "2026-07-25",
            "segment": "R", "data_complete": True,
            "completed_at": "2026-07-26", "sources": ["hl"], "seams": [],
        })
        return registry

    def test_an_untouched_reserve_block_is_clean(self):
        ok, findings = verify_all(block_registry=self._reserve_block())
        assert ok
        assert not [f for f in findings if f.check == "blocks"]

    def test_a_read_reserve_block_is_an_error(self):
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")

        ok, findings = verify_all(block_registry=blocks)

        assert ok is False
        errors = _errors(findings, "blocks")
        assert len(errors) == 1
        assert "H-2026-ACCOHORT" in errors[0].message

    def test_a_consumption_note_clears_it(self):
        """Option 3: the segment stays, the explanation attaches."""
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")
        blocks.acknowledge_consumption(
            "B-R-01", "H-2026-ACCOHORT",
            rationale="Cohort pipeline trained through this window. Spent for "
                      "wallet-selection work; holdout-grade at best "
                      "elsewhere.",
            acknowledged_at="2026-08-19T00:00:00Z")

        ok, findings = verify_all(block_registry=blocks)

        assert ok, [str(f) for f in findings]
        assert blocks.get_block("B-R-01")["segment"] == "R"

    def test_a_note_covers_only_the_entry_it_names(self):
        """No blank cheques. A second reader re-raises the error."""
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")
        blocks.acknowledge_consumption(
            "B-R-01", "H-2026-ACCOHORT", rationale="explained",
            acknowledged_at="2026-08-19T00:00:00Z")
        blocks.consume_block("B-R-01", "H-2026-LATER")

        ok, findings = verify_all(block_registry=blocks)

        assert ok is False
        errors = _errors(findings, "blocks")
        assert len(errors) == 1
        assert "H-2026-LATER" in errors[0].message

    def test_a_note_for_a_non_reader_is_an_error(self):
        """How a pre-acknowledgment would look if one were ever written in by
        hand — blocks.yaml is a plain file, so verify is the only backstop."""
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")
        raw = blocks.get_block("B-R-01")
        raw["consumption_notes"] = [{"entry_id": "H-2026-NEVER",
                                     "rationale": "pre-clearing",
                                     "acknowledged_at": "2026-08-19"}]
        blocks._blocks["B-R-01"] = raw

        ok, findings = verify_all(block_registry=blocks)

        assert ok is False
        messages = " ".join(f.message for f in _errors(findings, "blocks"))
        assert "H-2026-NEVER" in messages
        assert "pre-clears" in messages

    def test_exploration_and_screen_consumption_is_not_flagged(self):
        """The check must not cry wolf.

        The first cut flagged any consumed block whose segment was not V.
        Against the live registry that produced six false alarms — every
        exploration and screen block, where being read is the entire point —
        burying the single real R case. Caught by running the check against
        the real instance rather than a synthesized one, which is also how
        the citation resolver's #25 bug was found.
        """
        blocks = BlockRegistry()
        for block_id, segment in (("B-E", "E"), ("B-S1", "S1"),
                                  ("B-S2", "S2"), ("B-L", "L")):
            blocks.add_block({
                "id": block_id, "start": "2026-01-01", "end": "2026-03-01",
                "segment": segment, "data_complete": True,
                "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
            })
            blocks.consume_block(block_id, "H-2026-S002")

        ok, findings = verify_all(block_registry=blocks)

        assert ok, [str(f) for f in findings]
        assert not [f for f in findings if f.check == "blocks"]

    def test_promoting_a_read_block_into_reserve_is_refused(self):
        """R is the untouched claim, so promoting INTO it is claim-making too.

        E→R over existing consumption asserts 'nothing has read this' about a
        block that has been read, exactly as R→V does.
        """
        blocks = BlockRegistry()
        blocks.add_block({
            "id": "B-E2", "start": "2026-01-01", "end": "2026-03-01",
            "segment": "E", "data_complete": True,
            "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
        })
        blocks.consume_block("B-E2", "H-2026-S002")

        # Unexplained: refused outright, and NOT because of the label — an E
        # block never claimed to be untouched. The note is required before
        # any crossing into a claim-making segment.
        with pytest.raises(ValueError, match="no consumption note"):
            blocks.promote_block("B-E2", "R")

        blocks.acknowledge_consumption("B-E2", "H-2026-S002", "explored here",
                                       "2026-08-19T00:00:00Z")
        with pytest.raises(ValueError, match="already been read"):
            blocks.promote_block("B-E2", "R")

    def test_promoting_is_not_a_cheaper_way_to_clear_the_error(self):
        """THE INCENTIVE MUST NOT POINT AT THE WORST ACTION.

        An unexplained reading on an R block is an ERROR; an acknowledged
        promotion is only a WARNING. So for one commit the cheapest way to
        make `verify` pass was to promote the contaminated block into
        validation — precisely the act the whole check exists to prevent,
        and the tool rewarded it.

        Filing the note must be a PREREQUISITE of promoting, never an
        alternative to it.
        """
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")
        assert verify_all(block_registry=blocks)[0] is False

        with pytest.raises(ValueError, match="no consumption note"):
            blocks.promote_block("B-R-01", "V",
                                 acknowledge_prior_consumption=True,
                                 rationale="making the error go away")

        assert verify_all(block_registry=blocks)[0] is False

    def test_promoting_to_the_same_segment_stays_a_no_op(self):
        """A no-op promotion had always been silently idempotent, and the
        consumption guard briefly made it raise."""
        blocks = _blocks()
        blocks.consume_block("B-V-01", "H-2026-AC10")
        blocks.promote_block("B-V-01", "V")
        assert blocks.get_block("B-V-01")["segment"] == "V"

    def test_consumption_of_a_v_block_is_not_flagged(self):
        """Consuming validation data is what V is for."""
        blocks = _blocks()
        blocks.consume_block("B-V-01", "H-2026-AC10")

        ok, findings = verify_all(block_registry=blocks)

        assert ok, [str(f) for f in findings]

    def test_a_promotion_over_prior_consumption_warns_but_passes(self):
        """Permitted, recorded, and not equal evidence to a block held clean.

        A warning for the same reason the holdout check is one: the tool
        cannot adjudicate the attestation, only refuse to let it pass in
        silence.
        """
        blocks = self._reserve_block()
        blocks.consume_block("B-R-01", "H-2026-ACCOHORT")
        blocks.acknowledge_consumption(
            "B-R-01", "H-2026-ACCOHORT", rationale="explained",
            acknowledged_at="2026-08-19T00:00:00Z")
        blocks.promote_block("B-R-01", "V",
                             acknowledge_prior_consumption=True,
                             rationale="exploration only, nothing scored")

        ok, findings = verify_all(block_registry=blocks)

        assert ok, [str(f) for f in findings]
        warnings = [f for f in findings
                    if f.check == "blocks" and f.severity == "warning"]
        assert len(warnings) == 1
        assert "exploration only" in warnings[0].message


class TestAnEntryThatCanNeverGateSaysSo:
    """The time-of-check/time-of-use gap opened by permitting block reuse.

    Two entries declare the same V blocks; whichever gates first consumes
    them; E4 refuses the other forever. Soundness holds — E4 is the backstop
    — but without this the tool lets you register something it will later
    refuse to run, and says nothing while the choice is still open.

    There is no supersession concept: `status` is always "registered", so
    nothing marks a parent as retired.
    """

    def _blocks(self):
        registry = BlockRegistry()
        for name in ("B1", "B2"):
            registry.add_block({
                "id": name, "start": "2026-01-01", "end": "2026-03-01",
                "segment": "V", "data_complete": True,
                "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
            })
        return registry

    def _entry(self, entry_id, vblocks):
        return {"id": entry_id, "schema_version": 1,
                "created_utc": "2026-08-19T00:00:00Z",
                "validation_blocks": list(vblocks)}

    def test_a_spent_block_makes_the_other_entry_unrunnable(self):
        blocks = self._blocks()
        blocks.consume_block("B1", "H-FIRST")

        ok, findings = verify_all(
            registry=_StubRegistry([self._entry("H-SECOND", ["B1"])]),
            block_registry=blocks)

        assert ok is False
        errors = _errors(findings, "runnable")
        assert len(errors) == 1
        assert "H-FIRST" in errors[0].message
        assert "can never be gated" in errors[0].message

    def test_the_entry_that_spent_it_is_not_reported(self):
        blocks = self._blocks()
        blocks.consume_block("B1", "H-FIRST")

        ok, findings = verify_all(
            registry=_StubRegistry([self._entry("H-FIRST", ["B1"])]),
            block_registry=blocks)

        assert not [f for f in findings if f.check == "runnable"]

    def test_a_contested_unspent_block_warns_but_passes(self):
        """The warning is the error's early form, and self-clears when one
        of them gates — which is what stops it becoming an alarm nobody
        hears."""
        blocks = self._blocks()

        ok, findings = verify_all(
            registry=_StubRegistry([self._entry("H-P", ["B1", "B2"]),
                                    self._entry("H-S", ["B1", "B2"])]),
            block_registry=blocks)

        # Scoped to this check: the stub entries are intentionally minimal
        # and trip the schema validator, which is not what is under test.
        warnings = [f for f in findings if f.check == "runnable"]
        assert len(warnings) == 2        # one per contested block
        assert all(w.severity == "warning" for w in warnings)
        assert "H-P, H-S" in warnings[0].message
        assert "no amendment relationship" in warnings[0].message

    def test_one_claimant_is_not_contested(self):
        blocks = self._blocks()
        ok, findings = verify_all(
            registry=_StubRegistry([self._entry("H-P", ["B1", "B2"])]),
            block_registry=blocks)

        assert not [f for f in findings if f.check == "runnable"]

    def test_non_v_blocks_are_not_contested(self):
        """Reuse is the point of E, S1 and S2. Only V is single-use."""
        blocks = BlockRegistry()
        blocks.add_block({
            "id": "B-S2", "start": "2026-01-01", "end": "2026-03-01",
            "segment": "S2", "data_complete": True,
            "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
        })
        blocks.consume_block("B-S2", "H-FIRST")

        ok, findings = verify_all(
            registry=_StubRegistry([self._entry("H-A", ["B-S2"]),
                                    self._entry("H-B", ["B-S2"])]),
            block_registry=blocks)

        assert not [f for f in findings if f.check == "runnable"]


class TestSupersessionIsInferredFromDescent:
    """The intended workflow must not leave a permanent error.

    Amend P into S, then gate S — the correct path. Without descent
    awareness, verify then errored on P forever: "P declares B1, which S has
    already spent." True, useless, and unclearable, because nothing in the
    schema marks P as superseded.

    An error that can never clear is one people learn to skip. This tool has
    now recorded that failure twice (catalog #29 and the R-block labelling
    decision), and this is the third place it tried to appear.
    """

    def _blocks(self):
        registry = BlockRegistry()
        for name in ("B1", "B2"):
            registry.add_block({
                "id": name, "start": "2026-01-01", "end": "2026-03-01",
                "segment": "V", "data_complete": True,
                "completed_at": "2026-03-15", "sources": ["hl"], "seams": [],
            })
        return registry

    def _entry(self, entry_id, vblocks, depends=()):
        return {"id": entry_id, "schema_version": 1,
                "created_utc": "2026-08-19T00:00:00Z",
                "depends_on": list(depends),
                "validation_blocks": list(vblocks)}

    def _runnable(self, entries, blocks):
        _, findings = verify_all(registry=_StubRegistry(entries),
                                 block_registry=blocks)
        return [f for f in findings if f.check == "runnable"]

    def test_a_superseded_parent_is_not_reported(self):
        blocks = self._blocks()
        blocks.consume_block("B1", "H-S")
        blocks.consume_block("B2", "H-S")

        found = self._runnable(
            [self._entry("H-P", ["B1", "B2"]),
             self._entry("H-S", ["B1", "B2"], depends=["H-P"])], blocks)

        assert found == [], [str(f) for f in found]

    def test_supersession_is_transitive(self):
        """P -> M -> S. S spends the blocks; P is still superseded."""
        blocks = self._blocks()
        blocks.consume_block("B1", "H-S")

        found = self._runnable(
            [self._entry("H-P", ["B1"]),
             self._entry("H-M", ["B1"], depends=["H-P"]),
             self._entry("H-S", ["B1"], depends=["H-M"])], blocks)

        assert found == [], [str(f) for f in found]

    def test_an_unrelated_spender_is_still_an_error(self):
        """Descent is the exemption, not consumption itself."""
        blocks = self._blocks()
        blocks.consume_block("B1", "H-STRANGER")

        found = self._runnable(
            [self._entry("H-P", ["B1"]),
             self._entry("H-STRANGER", ["B1"])], blocks)

        errors = [f for f in found if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].where == "H-P"

    def test_an_ancestor_spending_it_is_still_an_error(self):
        """Direction matters. A successor cannot inherit blocks its PARENT
        spent — that is the invalidating-amendment rule, and here it is
        being reported after the fact rather than refused up front."""
        blocks = self._blocks()
        blocks.consume_block("B1", "H-P")

        found = self._runnable(
            [self._entry("H-P", ["B1"]),
             self._entry("H-S", ["B1"], depends=["H-P"])], blocks)

        errors = [f for f in found if f.severity == "error"]
        assert len(errors) == 1
        assert errors[0].where == "H-S"

    def test_one_finding_per_pair_not_per_block(self):
        """Six colliding blocks is one situation. Six identically-shaped
        lines is how a reader learns to skim the section."""
        blocks = self._blocks()
        blocks.consume_block("B1", "H-STRANGER")
        blocks.consume_block("B2", "H-STRANGER")

        found = self._runnable(
            [self._entry("H-P", ["B1", "B2"]),
             self._entry("H-STRANGER", ["B1", "B2"])], blocks)

        errors = [f for f in found if f.severity == "error"]
        assert len(errors) == 1
        assert "B1" in errors[0].message and "B2" in errors[0].message

    def test_a_lineage_carrying_blocks_forward_does_not_warn(self):
        """PFR1 -> PFR2 -> PFR3 all declaring the same six blocks is one
        live hypothesis with a history, not three entries in contention.

        Found against the real registry the first time this check met it —
        six warnings on an ordinary amendment chain. Same false positive the
        error branch had, in the branch that had not been fixed yet.
        """
        blocks = self._blocks()

        found = self._runnable(
            [self._entry("H-1", ["B1", "B2"]),
             self._entry("H-2", ["B1", "B2"], depends=["H-1"]),
             self._entry("H-3", ["B1", "B2"], depends=["H-2"])], blocks)

        assert found == [], [str(f) for f in found]

    def test_an_outsider_joining_a_lineage_still_warns(self):
        """Suppression is per-claimant, not per-block: a lineage plus one
        unrelated entry is still a genuine contest between two."""
        blocks = self._blocks()

        found = self._runnable(
            [self._entry("H-1", ["B1"]),
             self._entry("H-2", ["B1"], depends=["H-1"]),
             self._entry("H-OTHER", ["B1"])], blocks)

        warnings = [f for f in found if f.severity == "warning"]
        assert len(warnings) == 1
        assert "H-2, H-OTHER" in warnings[0].message
        assert "H-1" not in warnings[0].message
