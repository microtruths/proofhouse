# tests/test_manifest_result.py — the result lives in the manifest
"""
Phase 2b follow-on: §5.3's "a result without a manifest is not a result",
made true.

Until now the manifest carried code and data hashes but never the statistic
they produced. Two consequences, both real:

  * Phase 1's stated exit criterion — reproduce a past result from a manifest
    — could not be checked, because the result was not in the manifest.
  * The gate had nowhere to read scored results from, which is what blocked
    the `gate` CLI and left run_gate unwired.

ScoredResult.to_dict() had claimed "for manifest storage" in its docstring the
whole time, with zero production call sites. That is the failure catalog #26
shape: a record describing a connection that was never made.
"""

import math

import pytest

from proofhouse.eval.types import ScoredResult


def _full():
    return ScoredResult(
        statistic="panel_ic",
        value=0.038898,
        trial_count=14,
        p_value=0.0048,
        n_effective=4431,
        n_effective_basis="one wallet cross-section",
        benchmark_results={"btc-subtraction": 0.0577,
                           "market-index": -0.0599},
        secondary_statistics={"mean_net_bps_per_event": 46.42003773745695},
        metadata={"n": 4431, "se": 0.015},
    )


class TestRoundTrip:
    """A stored result that cannot be read back is a record, not a result."""

    def test_round_trip_preserves_every_field(self):
        original = _full()
        restored = ScoredResult.from_dict(original.to_dict())

        assert restored.statistic == original.statistic
        assert restored.value == original.value
        assert restored.trial_count == original.trial_count
        assert restored.p_value == original.p_value
        assert restored.n_effective == original.n_effective
        assert restored.n_effective_basis == original.n_effective_basis
        assert restored.benchmark_results == original.benchmark_results
        assert restored.secondary_statistics == original.secondary_statistics
        assert restored.metadata == original.metadata

    def test_round_trip_is_exact_not_approximate(self):
        """The whole reason for decimal strings. E3's IC to full precision
        must come back bit-identical, because the gate compares it against a
        registered threshold."""
        original = ScoredResult("panel_ic", 0.038897999999999999, 14)
        restored = ScoredResult.from_dict(original.to_dict())
        assert restored.value == original.value

    def test_round_trip_survives_yaml(self):
        """The manifest is YAML on disk. Decimal strings are what stop a
        float from shifting through the serializer."""
        import yaml
        original = _full()
        reloaded = yaml.safe_load(yaml.safe_dump(original.to_dict()))
        assert ScoredResult.from_dict(reloaded).value == original.value
        assert ScoredResult.from_dict(reloaded).p_value == original.p_value

    def test_none_p_value_survives_as_none(self):
        """'Not computed' is a distinct state from any number. run_screen
        refuses a None rather than guessing, so the round-trip must not
        quietly turn it into 1.0 or 0.0."""
        restored = ScoredResult.from_dict(
            ScoredResult("panel_ic", 0.03, 14).to_dict())
        assert restored.p_value is None

    def test_nan_survives(self):
        """NaN is infeasibility, which the gate counts as a loss. It has to
        arrive at the gate still recognisable."""
        restored = ScoredResult.from_dict(
            ScoredResult("panel_ic", float("nan"), 14).to_dict())
        assert math.isnan(restored.value)

    def test_corrupt_numeric_field_raises_rather_than_guessing(self):
        data = _full().to_dict()
        data["value"] = "not-a-number"
        with pytest.raises(ValueError):
            ScoredResult.from_dict(data)


class TestCanonicalForm:
    def test_numbers_are_decimal_strings(self):
        d = _full().to_dict()
        assert isinstance(d["value"], str)
        assert isinstance(d["p_value"], str)
        assert all(isinstance(v, str) for v in d["benchmark_results"].values())
        assert all(isinstance(v, str)
                   for v in d["secondary_statistics"].values())

    def test_a_secondary_statistic_keeps_every_digit(self):
        """It is compared against a registered threshold, so the whole
        argument for decimal strings applies to it — and it is the field's
        reason for existing. The live case: mean_net_bps_per_event
        46.42003773745695 against a literal 25, which spent six validation
        blocks. Pinned to what to_dict RETURNS: '%.17g' of that double."""
        d = _full().to_dict()
        assert d["secondary_statistics"]["mean_net_bps_per_event"] == (
            "46.420037737456951")
        assert ScoredResult.from_dict(d).secondary_statistics[
            "mean_net_bps_per_event"] == 46.42003773745695

    def test_counts_stay_integers(self):
        d = _full().to_dict()
        assert d["trial_count"] == 14
        assert d["n_effective"] == 4431

    def test_metadata_is_left_alone_so_it_round_trips(self):
        """Canonicalizing an open dict is irreversible — on read there is no
        way to tell an encoded 0.015 from a genuine string "0.015". It is safe
        to skip because metadata is documented as outside the gate decision,
        so no verdict can turn on an ulp of it."""
        d = _full().to_dict()
        assert d["metadata"]["se"] == 0.015
        assert d["metadata"]["n"] == 4431

    def test_diagnostics_are_not_in_the_result(self):
        """A4: putting diagnostics in the record the gate reads would reopen
        the boundary through the back door."""
        d = _full().to_dict()
        assert set(d) == {
            "statistic", "value", "trial_count", "p_value", "n_effective",
            "n_effective_basis", "benchmark_results", "secondary_statistics",
            "metadata",
        }


class TestASecondaryStatisticIsFirstClass:
    """§2.1(a). A gate criterion read its second statistic out of `metadata`,
    which to_dict deliberately does not canonicalize — licensed by "nothing
    in it is compared against a threshold, so no verdict can turn on an ulp
    of it." Reading a criterion out of it revoked that licence.
    """

    def test_none_survives_as_not_computed(self):
        """Same rule as p_value: 'not computed' is a distinct state. A gate
        asking for it must refuse on infeasibility, not compare a guess."""
        original = ScoredResult("panel_ic", 0.03, 14,
                                secondary_statistics={"turnover": None})
        restored = ScoredResult.from_dict(original.to_dict())
        assert restored.secondary_statistics == {"turnover": None}

    def test_absent_and_empty_are_the_same_thing(self):
        assert ScoredResult("panel_ic", 0.03, 14).secondary_statistics == {}
        assert ScoredResult.from_dict(
            {"statistic": "panel_ic", "value": "0.03",
             "trial_count": 14}).secondary_statistics == {}

    def test_it_may_not_restate_the_primary(self):
        """Two representations of one number is how they come to disagree."""
        with pytest.raises(ValueError) as exc:
            ScoredResult("panel_ic", 0.03, 14,
                         secondary_statistics={"panel_ic": 0.04})
        assert "primary" in str(exc.value)

    def test_a_bool_is_refused_at_construction(self):
        """Python compares True as 1.0, so a bool would pass a `>= 1` gate on
        something that was never a measurement. evaluate_gate refuses one at
        the comparison; refusing here means it never reaches a manifest."""
        with pytest.raises(ValueError):
            ScoredResult("panel_ic", 0.03, 14,
                         secondary_statistics={"beat": True})

    def test_a_corrupt_secondary_raises_rather_than_guessing(self):
        data = _full().to_dict()
        data["secondary_statistics"]["mean_net_bps_per_event"] = "n/a"
        with pytest.raises(ValueError) as exc:
            ScoredResult.from_dict(data)
        assert "secondary_statistics" in str(exc.value)


class TestGateReadsAManifestResult:
    """The point of all of this: a verdict rendered from a stored result."""

    def test_verdict_from_a_manifest_matches_the_live_one(self):
        import yaml
        from proofhouse.core.gate import evaluate_gate

        entry = {
            "id": "H-2026-AC10",
            "gate": {
                "statistic": "panel_ic",
                "combination": "g1",
                "criteria": {"g1": {"comparator": ">=",
                                    "threshold": {"literal": "0.05"}}},
            },
        }
        live = ScoredResult("panel_ic", 0.038898, 14)

        # Straight through the serializer, exactly as the manifest stores it.
        persisted = yaml.safe_load(yaml.safe_dump({"result": live.to_dict()}))
        restored = ScoredResult.from_dict(persisted["result"])

        from_live = evaluate_gate(entry, {"panel_ic": live})
        from_disk = evaluate_gate(entry, {"panel_ic": restored})

        assert from_live.to_dict() == from_disk.to_dict()
        assert from_disk.passed is False
