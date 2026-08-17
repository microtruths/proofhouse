# tests/test_scored_result_fields.py — ScoredResult p_value / n_effective (Part A)
"""Tests for the new first-class fields on ScoredResult.

Phase 1 Completion Plan §2.

Design points under test:
  - p_value and n_effective are FIRST-CLASS, not metadata. metadata is
    documented as "not part of the gate decision"; a p-value drives
    selection, so it is part of the decision.
  - p_value defaults to None and is never silently coerced. Defaulting to
    1.0 would silently drop a hypothesis; 0.0 would silently pass it.
  - n_effective requires n_effective_basis (failure catalog #4: effective
    N is independent units, declared, never inferred).
"""

import unittest


class TestScoredResultNewFields(unittest.TestCase):

    def _minimal(self, **kw):
        from proofhouse.eval.types import ScoredResult

        base = dict(statistic="panel_ic", value=0.0389, trial_count=1)
        base.update(kw)
        return ScoredResult(**base)

    def test_defaults_are_none_not_coerced(self):
        """Absent p_value must be None — never 1.0, never 0.0.

        A default of 1.0 silently drops the hypothesis from any FDR batch.
        A default of 0.0 silently passes it. Both are worse than None,
        which run_screen can refuse explicitly.
        """
        r = self._minimal()
        self.assertIsNone(r.p_value)
        self.assertIsNone(r.n_effective)
        self.assertIsNone(r.n_effective_basis)

    def test_accepts_p_value_and_n_effective(self):
        r = self._minimal(
            p_value=0.0048,
            n_effective=4431,
            n_effective_basis="one wallet per cross-section; wallets independent",
        )
        self.assertEqual(r.p_value, 0.0048)
        self.assertEqual(r.n_effective, 4431)
        self.assertIn("wallet", r.n_effective_basis)

    def test_n_effective_requires_basis(self):
        """Failure catalog #4: effective N = independent units, not row count.

        Setting n_effective without stating what an independent unit is
        is exactly the error #4 records. The basis is cheap and forces
        the thought.
        """
        with self.assertRaises(ValueError):
            self._minimal(n_effective=4431)

    def test_n_effective_basis_must_be_non_empty(self):
        with self.assertRaises(ValueError):
            self._minimal(n_effective=4431, n_effective_basis="   ")

    def test_basis_without_n_effective_is_allowed(self):
        """Stating the basis without the count is harmless, not an error."""
        r = self._minimal(n_effective_basis="wallet-level cross-section")
        self.assertIsNone(r.n_effective)

    def test_p_value_range_validated(self):
        for bad in (-0.001, 1.001, 2.0, -1.0):
            with self.assertRaises(ValueError):
                self._minimal(p_value=bad)

    def test_p_value_boundaries_allowed(self):
        self.assertEqual(self._minimal(p_value=0.0).p_value, 0.0)
        self.assertEqual(self._minimal(p_value=1.0).p_value, 1.0)

    def test_n_effective_must_be_positive_integer(self):
        for bad in (0, -5):
            with self.assertRaises(ValueError):
                self._minimal(n_effective=bad, n_effective_basis="x")

    def test_to_dict_includes_new_fields(self):
        """The manifest must carry them — a p-value not in the record cannot
        be audited, and Part C's screen reconstruction needs it."""
        r = self._minimal(
            p_value=0.0048,
            n_effective=4431,
            n_effective_basis="wallet cross-section",
        )
        d = r.to_dict()
        self.assertEqual(d["p_value"], 0.0048)
        self.assertEqual(d["n_effective"], 4431)
        self.assertEqual(d["n_effective_basis"], "wallet cross-section")

    def test_to_dict_still_round_trips_without_new_fields(self):
        d = self._minimal().to_dict()
        self.assertIsNone(d["p_value"])
        self.assertEqual(d["statistic"], "panel_ic")

    def test_p_value_is_not_in_metadata(self):
        """Structural: p_value must be its own attribute, not a metadata key.

        metadata is documented as excluded from the gate decision. A
        p-value drives selection. Keeping it in metadata would contradict
        the type's own contract and reopen the A4 boundary.
        """
        r = self._minimal(p_value=0.0048)
        self.assertNotIn("p_value", r.metadata)
        self.assertTrue(hasattr(r, "p_value"))


if __name__ == "__main__":
    unittest.main()
