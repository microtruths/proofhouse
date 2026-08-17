# tests/test_pvalue.py — hand-computed p-value tests (Part A)
"""Hand-computed tests for the parametric p-value path.

Phase 1 Completion Plan §2.4 and §7 Part A.

Every expected value below was computed by hand or taken from a published
t-table BEFORE the implementation was written. None was read back out of
the code.

Canonical anchors used:
  - Student's t two-sided 5% critical values: df=1 → 12.706, df=10 → 2.228,
    df→∞ → 1.960. These are standard t-table values.
  - One-sided 5% at df→∞ → 1.645.
"""

import math
import unittest


class TestSpearmanT(unittest.TestCase):
    """IC → t conversion: t = IC * sqrt((n-2) / (1 - IC^2))."""

    def test_p1_agentcopy_ic_gate_anchor(self):
        """P-1: the AgentCopy Phase 0.5 IC gate figure, reproduced from the formula.

        AMENDMENT_A2 §A2.1 records IC = 0.0389, n = 4,431, t = 2.59.

        Hand computation:
            IC^2          = 0.0389^2      = 0.00151321
            1 - IC^2      =                 0.99848679
            n - 2         =                 4429
            ratio         = 4429 / 0.99848679 = 4435.7116
            sqrt(ratio)   =                 66.60114
            t             = 0.0389 * 66.60114 = 2.590784

        Rounds to 2.59 at two decimal places, matching the published value.
        This anchors the formula against a real recorded result before the
        E3 reproduction depends on it.
        """
        from proofhouse.eval.stats import spearman_t

        t = spearman_t(0.0389, 4431)
        self.assertAlmostEqual(t, 2.590784, places=5)
        self.assertEqual(round(t, 2), 2.59)

    def test_p1b_simple_hand_case(self):
        """A second anchor small enough to verify entirely by hand.

        IC = 0.5, n = 18, df = 16.
            1 - 0.25      = 0.75
            16 / 0.75     = 21.333333
            sqrt          = 4.6188022
            t = 0.5 * 4.6188022 = 2.3094011
        """
        from proofhouse.eval.stats import spearman_t

        self.assertAlmostEqual(spearman_t(0.5, 18), 2.3094011, places=6)

    def test_zero_ic_gives_zero_t(self):
        from proofhouse.eval.stats import spearman_t

        self.assertEqual(spearman_t(0.0, 100), 0.0)

    def test_negative_ic_gives_negative_t(self):
        """Sign is preserved — a negative IC is an anti-signal, not a small one."""
        from proofhouse.eval.stats import spearman_t

        self.assertAlmostEqual(spearman_t(-0.5, 18), -2.3094011, places=6)

    def test_n_below_three_rejected(self):
        """df = n - 2 must be >= 1."""
        from proofhouse.eval.stats import spearman_t

        with self.assertRaises(ValueError):
            spearman_t(0.5, 2)

    def test_ic_of_one_rejected(self):
        """|IC| = 1 makes the denominator zero — undefined, not infinite."""
        from proofhouse.eval.stats import spearman_t

        with self.assertRaises(ValueError):
            spearman_t(1.0, 100)


class TestPFromT(unittest.TestCase):
    """p-value from a t-statistic. Verified against published t-table values."""

    def test_two_sided_normal_limit(self):
        """t = 1.959964 at very large df is the canonical two-sided 5% point."""
        from proofhouse.eval.stats import p_from_t

        p = p_from_t(1.959964, df=10_000_000, sidedness="two-sided")
        self.assertAlmostEqual(p, 0.05, places=5)

    def test_one_sided_normal_limit(self):
        """t = 1.644854 at very large df is the canonical one-sided 5% point."""
        from proofhouse.eval.stats import p_from_t

        p = p_from_t(1.644854, df=10_000_000, sidedness="one-sided")
        self.assertAlmostEqual(p, 0.05, places=5)

    def test_two_sided_df10(self):
        """Published t-table: df = 10, two-sided 5% critical value = 2.228."""
        from proofhouse.eval.stats import p_from_t

        p = p_from_t(2.228139, df=10, sidedness="two-sided")
        self.assertAlmostEqual(p, 0.05, places=5)

    def test_two_sided_df1(self):
        """Published t-table: df = 1, two-sided 5% critical value = 12.706."""
        from proofhouse.eval.stats import p_from_t

        p = p_from_t(12.706205, df=1, sidedness="two-sided")
        self.assertAlmostEqual(p, 0.05, places=5)

    def test_t_zero_gives_p_one_two_sided(self):
        from proofhouse.eval.stats import p_from_t

        self.assertAlmostEqual(
            p_from_t(0.0, df=50, sidedness="two-sided"), 1.0, places=10
        )

    def test_one_sided_is_half_of_two_sided_for_positive_t(self):
        """For t > 0 the one-sided p is exactly half the two-sided p."""
        from proofhouse.eval.stats import p_from_t

        two = p_from_t(2.5, df=100, sidedness="two-sided")
        one = p_from_t(2.5, df=100, sidedness="one-sided")
        self.assertAlmostEqual(one, two / 2.0, places=12)

    def test_one_sided_negative_t_is_large(self):
        """A negative t under H1: effect > 0 gives p close to 1, not close to 0.

        This is the sign error that would make an anti-signal look significant.
        """
        from proofhouse.eval.stats import p_from_t

        p = p_from_t(-2.5, df=100, sidedness="one-sided")
        self.assertGreater(p, 0.98)

    def test_sidedness_required_and_validated(self):
        """No default. The choice halves or doubles every p-value, so it is
        made explicitly at every call site (the A5 lesson)."""
        from proofhouse.eval.stats import p_from_t

        with self.assertRaises(ValueError):
            p_from_t(2.0, df=100, sidedness="whatever")
        with self.assertRaises(TypeError):
            p_from_t(2.0, df=100)  # sidedness is not optional

    def test_p_always_in_unit_interval(self):
        from proofhouse.eval.stats import p_from_t

        for t in (-50.0, -3.0, -0.1, 0.0, 0.1, 3.0, 50.0):
            for sided in ("one-sided", "two-sided"):
                p = p_from_t(t, df=25, sidedness=sided)
                self.assertGreaterEqual(p, 0.0)
                self.assertLessEqual(p, 1.0)

    def test_df_must_be_positive(self):
        from proofhouse.eval.stats import p_from_t

        with self.assertRaises(ValueError):
            p_from_t(2.0, df=0, sidedness="two-sided")


class TestEndToEndAnchor(unittest.TestCase):
    """The composed path the E3 reproduction will use."""

    def test_agentcopy_ic_to_p(self):
        """IC = 0.0389, n = 4,431 → t = 2.59 → p.

        AgentCopy recorded the gate as one-sided (v10 §3.5.3: "One-sided
        tests"). At t = 2.5908 with df = 4429 the distribution is
        indistinguishable from normal, so one-sided p = 1 - Phi(2.5908).

        Phi(2.5908) ~ 0.99521, so p ~ 0.00479. Asserted loosely here
        because the published record gives t, not p — the tight assertion
        is on t in P-1.
        """
        from proofhouse.eval.stats import spearman_t, p_from_t

        t = spearman_t(0.0389, 4431)
        p = p_from_t(t, df=4431 - 2, sidedness="one-sided")
        self.assertLess(p, 0.006)
        self.assertGreater(p, 0.004)


if __name__ == "__main__":
    unittest.main()
