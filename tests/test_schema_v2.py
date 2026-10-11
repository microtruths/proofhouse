# tests/test_schema_v2.py — the schema bump to 2, and what it must not break
"""
V-13 (p_value_sidedness) and V-14 (fdr_method + justification) became
required on 2026-08-17 while SCHEMA_VERSION stayed at 1. Every record from
before that date was then re-validated under rules it was never registered
under, and `verify` failed on a registry nobody had touched. The bump to 2 is
the repair; these tests pin what the bump means.

  - a v1 record missing the v2 fields is a legacy record, not an error
  - the legacy state is visible as a warning, never silent, and says
    whether the v2 rules would also pass the record
  - a v1 record is still held to the v1 rules
  - a new registration is written at 2 and cannot dodge V-13/V-14 by
    claiming 1
  - a v2 record missing either field is refused
  - a future version is refused at validation and is an error in verify
  - the hash chain over v1 records is untouched by any of this
"""

import os
import tempfile

import pytest
import yaml

from proofhouse.chain.chain import build_chain
from proofhouse.core.blocks import BlockRegistry
from proofhouse.core.registry import Registry
from proofhouse.core.verify import verify_all
from proofhouse.schema.validate import ValidationError, validate_entry
from proofhouse.schema.versions import SCHEMA_VERSION, check_version

V2_FIELDS = ("p_value_sidedness", "fdr_method", "fdr_method_justification")


def _v2_entry(entry_id="H-2026-001"):
    """A minimally valid current-version entry. Inlined rather than imported
    from another test module — pytest does not put sibling test files on
    sys.path."""
    return {
        "schema_version": SCHEMA_VERSION,
        "id": entry_id,
        "created_utc": "2026-08-20T14:03:11Z",
        "status": "registered",
        "depends_on": [],
        "mechanism": {
            "class": "funding-basis",
            "counterparty": "Leveraged longs paying funding on perpetuals",
            "claim": "Funding rate predicts subsequent returns",
            "expected_effect_size": "1.2",
            "expected_decay": "6 months",
        },
        "signal": {
            "inputs": ["funding_rate"],
            "transform": "z-score over trailing 12 periods",
            "frequency": "8h",
            "parameters": {"k": 12, "threshold": "0.8"},
        },
        "universe": {
            "id": "hl-perps-top20",
            "venues": ["hyperliquid"],
            "instruments": "top 20 by OI",
        },
        "feasibility": {
            "min_order_notional_usd": "10",
            "sleeve_range": ["100", "20000"],
            "instruments_held": 8,
            "rebalance_band": "0.15",
        },
        "costs": {"model_ref": "costs/hl-taker-v1"},
        "gate": {
            "statistic": "dsr",
            "combination": "g1 and g2",
            "criteria": {
                "g1": {"comparator": ">", "threshold": {"literal": "0"}},
                "g2": {"comparator": ">=", "threshold": {"literal": "0.95"}},
            },
            "benchmark": "btc-subtraction",
            "benchmark_justification": "BTC subtraction chosen because...",
            "benchmark_sensitivity": ["market-index", "eth-subtraction"],
            "on_pass": "promote-to-live",
            "on_fail": "retire",
        },
        "p_value_sidedness": "one-sided",
        "fdr_method": "by",
        "fdr_method_justification": (
            "Hypotheses share the hl-perps universe and are dependent."),
        "pass_licenses": "A shadow trial on the live sleeve for 6 weeks.",
        "pass_does_not_establish": "Deployable alpha or robustness.",
        "live_gate": {"min_duration_weeks": 6},
        "validation_blocks": [],
        "seam_acknowledged": False,
        "diagnostics": [],
        "power_check": {
            "inputs": {"effect_size": "1.2"},
            "value": {"pending": True,
                      "fill_rule": "power(effect_size, blocks)"},
        },
        "prior_art": [],
    }


def _v1_entry(entry_id="H-2026-001"):
    """The same entry as it was registered before 2026-08-17: version 1 and
    none of the fields v2 requires. The literal 1 is the point."""
    entry = _v2_entry(entry_id)
    entry["schema_version"] = 1
    for key in V2_FIELDS:
        del entry[key]
    return entry


def _write_chained(entries):
    """Write entries to disk as a hash chain, bypassing the registry's
    validation — which is how a v1 record exists on disk today. Returns the
    registry directory."""
    registry_dir = os.path.join(tempfile.mkdtemp(), "registry")
    os.makedirs(registry_dir)
    for record in build_chain(entries):
        path = os.path.join(registry_dir, "{}.yaml".format(record["id"]))
        with open(path, "w") as fh:
            yaml.dump(record, fh, default_flow_style=False, sort_keys=True,
                      allow_unicode=True)
    return registry_dir


def _schema(findings, severity):
    return [f for f in findings
            if f.check == "schema" and f.severity == severity]


class TestWhatTheToolWrites:

    def test_the_tool_writes_version_2(self):
        assert SCHEMA_VERSION == 2

    def test_a_registration_is_written_at_the_current_version(self):
        """On disk, not only in the returned dict."""
        registry_dir = os.path.join(tempfile.mkdtemp(), "registry")
        stored = Registry(registry_dir).register(_v2_entry())

        assert stored["schema_version"] == SCHEMA_VERSION
        with open(os.path.join(registry_dir, "H-2026-001.yaml")) as fh:
            assert yaml.safe_load(fh)["schema_version"] == 2

    def test_claiming_version_1_does_not_dodge_v13_and_v14(self):
        """The registry stamps the current version before validating, so a
        caller cannot opt out of the v2 fields by writing 1."""
        registry = Registry(os.path.join(tempfile.mkdtemp(), "registry"))

        with pytest.raises(ValidationError) as exc_info:
            registry.register(_v1_entry())

        message = str(exc_info.value)
        assert "V-13" in message and "V-14" in message

    @pytest.mark.parametrize("field", V2_FIELDS)
    def test_a_v2_record_missing_a_required_field_is_refused(self, field):
        entry = _v2_entry()
        del entry[field]

        with pytest.raises(ValidationError) as exc_info:
            validate_entry(entry)

        code = "V-13" if field == "p_value_sidedness" else "V-14"
        assert code in str(exc_info.value)

    def test_a_future_version_is_refused(self):
        entry = _v2_entry()
        entry["schema_version"] = SCHEMA_VERSION + 1

        with pytest.raises(ValidationError, match="V-11"):
            validate_entry(entry)
        with pytest.raises(ValueError, match="newer proofhouse"):
            check_version(SCHEMA_VERSION + 1)

    def test_a_bool_is_not_a_version(self):
        """isinstance(True, int) holds in Python; `schema_version: true`
        must not read as version 1."""
        with pytest.raises(ValueError):
            check_version(True)


class TestLegacyRecordsUnderTheV2Tool:
    """The two records that exposed the defect: registered before V-13 and
    V-14 existed, chained, and untouched."""

    def _legacy_registry(self):
        return Registry(_write_chained(
            [_v1_entry("H-2026-AC10"), _v1_entry("H-2026-TP01")]))

    def test_a_v1_record_missing_the_v2_fields_is_not_an_error(self):
        ok, findings = verify_all(registry=self._legacy_registry(),
                                  block_registry=BlockRegistry())

        assert ok, [str(f) for f in findings]
        assert _schema(findings, "error") == []

    def test_the_legacy_state_is_a_warning_not_silence(self):
        """One per record, naming the version it was held to and the
        fields the current rules would ask for."""
        _, findings = verify_all(registry=self._legacy_registry(),
                                 block_registry=BlockRegistry())

        warnings = _schema(findings, "warning")
        assert [w.where for w in warnings] == ["H-2026-AC10", "H-2026-TP01"]
        for warning in warnings:
            assert "schema_version 1" in warning.message
            assert "v1 rules" in warning.message
            assert "V-13" in warning.message and "V-14" in warning.message
            assert "would refuse" in warning.message

    def test_a_v1_record_carrying_the_v2_fields_says_so(self):
        """The live research registry's case: every entry is v1 and every
        entry already declares sidedness and FDR. Still a legacy record,
        and the warning must say nothing is missing."""
        entry = _v2_entry("H-2026-PFR1")
        entry["schema_version"] = 1

        ok, findings = verify_all(registry=Registry(_write_chained([entry])),
                                  block_registry=BlockRegistry())

        assert ok, [str(f) for f in findings]
        assert _schema(findings, "error") == []
        warnings = _schema(findings, "warning")
        assert len(warnings) == 1
        assert "also satisfies the v2 rules" in warnings[0].message

    def test_a_v1_record_is_still_held_to_the_v1_rules(self):
        """Legacy is not exempt. A v1 record with no gate.combination was
        never valid, and skipping it because it is old would read an
        unverified record as a verified one."""
        entry = _v1_entry("H-2026-AC10")
        entry["gate"]["combination"] = None

        ok, findings = verify_all(registry=Registry(_write_chained([entry])),
                                  block_registry=BlockRegistry())

        assert ok is False
        errors = _schema(findings, "error")
        assert len(errors) == 1
        assert "V-3" in errors[0].message
        assert "V-13" not in errors[0].message     # v1 is not asked for it

    def test_a_current_record_gets_no_schema_finding(self):
        ok, findings = verify_all(
            registry=Registry(_write_chained([_v2_entry()])),
            block_registry=BlockRegistry())

        assert ok, [str(f) for f in findings]
        assert [f for f in findings if f.check == "schema"] == []

    def test_a_future_version_is_an_error_not_a_warning(self):
        """A record this build cannot check is not clean."""
        entry = _v2_entry()
        entry["schema_version"] = SCHEMA_VERSION + 1

        ok, findings = verify_all(registry=Registry(_write_chained([entry])),
                                  block_registry=BlockRegistry())

        assert ok is False
        errors = _schema(findings, "error")
        assert len(errors) == 1
        assert "V-11" in errors[0].message
        assert _schema(findings, "warning") == []


class TestTheChainIsUntouched:

    def test_the_v1_chain_verifies_unchanged(self):
        registry = Registry(_write_chained(
            [_v1_entry("H-2026-AC10"), _v1_entry("H-2026-TP01")]))

        ok, errors = registry.verify()
        assert ok, errors
        first, second = registry.list_entries()
        assert "prev" not in first
        assert second["prev"] == first["hash"]

        _, findings = verify_all(registry=registry)
        assert [f for f in findings if f.check == "chain"] == []

    def test_a_tampered_v1_record_is_still_caught(self):
        """Legacy treatment is about which rules apply, not about trust."""
        registry_dir = _write_chained(
            [_v1_entry("H-2026-AC10"), _v1_entry("H-2026-TP01")])
        path = os.path.join(registry_dir, "H-2026-AC10.yaml")
        with open(path) as fh:
            record = yaml.safe_load(fh)
        record["pass_licenses"] = "Something else entirely."
        with open(path, "w") as fh:
            yaml.dump(record, fh, default_flow_style=False, sort_keys=True)

        registry = Registry(registry_dir)
        ok, errors = registry.verify()
        assert ok is False
        assert errors[0]["kind"] == "hash_mismatch"

        ok, findings = verify_all(registry=registry)
        assert ok is False
        assert [f for f in findings
                if f.check == "chain" and f.severity == "error"]
