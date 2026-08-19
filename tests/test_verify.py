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

    def test_the_real_catalog_parses_to_27(self):
        resolver = catalog_citation_resolver(self.CATALOG)
        assert resolver is not None
        assert resolver.highest == 27

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
