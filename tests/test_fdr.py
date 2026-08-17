# tests/test_fdr.py — hand-computed FDR tests (Part B)
"""Benjamini-Hochberg and Benjamini-Yekutieli.

Part B plan §6. Every expected value below was worked by hand or taken
from the published BH (1995) example BEFORE the implementation existed.

The single most likely defect is implementing the STEP-UP procedure as a
step-DOWN one: walking up the sorted p-values and stopping at the first
failure. That is a different procedure, controls a different quantity,
and silently rejects too few. F-1 exists specifically to catch it.
"""

import unittest


# The BH (1995) published worked example. m = 15, q = 0.05 -> 4 rejections.
BH1995 = [
    0.0001, 0.0004, 0.0019, 0.0095, 0.0201,
    0.0278, 0.0298, 0.0344, 0.0459, 0.3240,
    0.4262, 0.5719, 0.6528, 0.7590, 1.0000,
]


def _pairs(pvals, prefix="H"):
    return [("{}-{:02d}".format(prefix, i), p) for i, p in enumerate(pvals)]


class TestBenjaminiHochberg(unittest.TestCase):

    def test_f1_step_up_rejects_all_five(self):
        """F-1: the case that distinguishes step-up from step-down.

        p = [0.001, 0.008, 0.039, 0.041, 0.042], q = 0.05, m = 5

            k | p(k)  | (k/m)q | p<=thr
            1 | 0.001 | 0.01   | yes
            2 | 0.008 | 0.02   | yes
            3 | 0.039 | 0.03   | NO
            4 | 0.041 | 0.04   | NO
            5 | 0.042 | 0.05   | yes   <- largest passing k

        BH rejects 1..k for the LARGEST passing k, so all five are
        rejected -- including k=3 and k=4, which fail their own
        thresholds. A step-down implementation stops at k=2 and returns
        two. That is the error this test exists to catch.
        """
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg(
            _pairs([0.001, 0.008, 0.039, 0.041, 0.042]), q=0.05
        )
        self.assertEqual(res["k"], 5)
        self.assertEqual(len(res["rejected"]), 5)
        # The two that individually fail must still be rejected.
        self.assertIn("H-02", res["rejected"])
        self.assertIn("H-03", res["rejected"])

    def test_f2_published_bh1995_example(self):
        """F-2: the original paper's worked example rejects exactly 4.

        Anchored outside this project so the test is not merely checking
        my own arithmetic.
        """
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg(_pairs(BH1995), q=0.05)
        self.assertEqual(res["k"], 4)
        self.assertEqual(res["m"], 15)
        self.assertEqual(
            res["rejected"], ["H-00", "H-01", "H-02", "H-03"]
        )

    def test_f2_threshold_reported(self):
        """The rejection threshold must be reported: a screening result is
        not reconstructible without it (Part C records it)."""
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg(_pairs(BH1995), q=0.05)
        # Largest passing k = 4 -> threshold = (4/15) * 0.05
        self.assertAlmostEqual(res["threshold"], (4 / 15) * 0.05, places=12)

    def test_f6_m_of_one_is_plain_comparison(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        self.assertEqual(benjamini_hochberg([("a", 0.04)], q=0.05)["k"], 1)
        self.assertEqual(benjamini_hochberg([("a", 0.06)], q=0.05)["k"], 0)

    def test_f7_all_ones_rejects_nothing(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg(_pairs([1.0] * 10), q=0.05)
        self.assertEqual(res["k"], 0)
        self.assertEqual(res["rejected"], [])
        self.assertIsNone(res["threshold"])

    def test_f8_all_tiny_rejects_everything(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg(_pairs([1e-12] * 10), q=0.05)
        self.assertEqual(res["k"], 10)

    def test_f10_empty_batch(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        res = benjamini_hochberg([], q=0.05)
        self.assertEqual(res["k"], 0)
        self.assertEqual(res["m"], 0)
        self.assertEqual(res["rejected"], [])

    def test_f11_p_out_of_range_raises(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        for bad in (-0.001, 1.001):
            with self.assertRaises(ValueError):
                benjamini_hochberg([("a", bad)], q=0.05)

    def test_q_out_of_range_raises(self):
        from proofhouse.eval.fdr import benjamini_hochberg

        for bad in (0.0, -0.1, 1.5):
            with self.assertRaises(ValueError):
                benjamini_hochberg([("a", 0.01)], q=bad)

    def test_none_p_value_raises(self):
        """A missing p-value must never be silently coerced -- defaulting
        to 1.0 drops the hypothesis, 0.0 passes it (Part A)."""
        from proofhouse.eval.fdr import benjamini_hochberg

        with self.assertRaises(ValueError):
            benjamini_hochberg([("a", None)], q=0.05)

    def test_f9_tie_breaking_is_order_independent(self):
        """Determinism rule 3.1: ties break by entry id, not input order.

        The same batch in any permutation must give the same answer, or a
        screening decision depends on how the batch was assembled.
        """
        from proofhouse.eval.fdr import benjamini_hochberg

        batch = [("H-c", 0.01), ("H-a", 0.01), ("H-b", 0.01)]
        first = benjamini_hochberg(batch, q=0.05)["rejected"]
        for perm in (
            [("H-a", 0.01), ("H-b", 0.01), ("H-c", 0.01)],
            [("H-b", 0.01), ("H-c", 0.01), ("H-a", 0.01)],
        ):
            self.assertEqual(benjamini_hochberg(perm, q=0.05)["rejected"], first)
        self.assertEqual(first, ["H-a", "H-b", "H-c"])

    def test_duplicate_ids_rejected(self):
        """Two rows with the same id make the result ambiguous."""
        from proofhouse.eval.fdr import benjamini_hochberg

        with self.assertRaises(ValueError):
            benjamini_hochberg([("a", 0.01), ("a", 0.02)], q=0.05)


class TestHarmonicNumber(unittest.TestCase):

    def test_f3_h15(self):
        """H(15) = 3.3182289932, summed by hand."""
        from proofhouse.eval.fdr import harmonic_number

        self.assertAlmostEqual(harmonic_number(15), 3.3182289932, places=9)

    def test_f3_h1(self):
        from proofhouse.eval.fdr import harmonic_number

        self.assertEqual(harmonic_number(1), 1.0)

    def test_h2(self):
        from proofhouse.eval.fdr import harmonic_number

        self.assertAlmostEqual(harmonic_number(2), 1.5, places=12)

    def test_h0_is_zero(self):
        from proofhouse.eval.fdr import harmonic_number

        self.assertEqual(harmonic_number(0), 0.0)


class TestBenjaminiYekutieli(unittest.TestCase):

    def test_f4_by_on_bh1995_rejects_three(self):
        """F-4: BY on the same data rejects 3, against BH's 4.

        q_eff = 0.05 / H(15) = 0.05 / 3.3182289932 = 0.0150683

            k | p(k)   | (k/15)*q_eff | p<=thr
            3 | 0.0019 | 0.0030137    | yes
            4 | 0.0095 | 0.0040182    | NO
        """
        from proofhouse.eval.fdr import benjamini_yekutieli

        res = benjamini_yekutieli(_pairs(BH1995), q=0.05)
        self.assertEqual(res["k"], 3)
        self.assertEqual(res["rejected"], ["H-00", "H-01", "H-02"])

    def test_f4_reports_effective_q(self):
        from proofhouse.eval.fdr import benjamini_yekutieli, harmonic_number

        res = benjamini_yekutieli(_pairs(BH1995), q=0.05)
        self.assertAlmostEqual(
            res["q_effective"], 0.05 / harmonic_number(15), places=12
        )

    def test_f5_by_is_subset_of_bh(self):
        """F-5: BY never rejects anything BH does not. Checked across a
        spread of batch sizes and p-value shapes."""
        from proofhouse.eval.fdr import benjamini_hochberg, benjamini_yekutieli

        batches = [
            [0.001, 0.01, 0.02, 0.03, 0.2],
            [0.04] * 8,
            [1e-6, 1e-5, 0.5, 0.9],
            BH1995,
            [0.02, 0.021, 0.022, 0.023, 0.024, 0.025, 0.5],
        ]
        for pvals in batches:
            pairs = _pairs(pvals)
            bh = set(benjamini_hochberg(pairs, q=0.1)["rejected"])
            by = set(benjamini_yekutieli(pairs, q=0.1)["rejected"])
            self.assertTrue(
                by.issubset(bh),
                "BY rejected something BH did not: {}".format(by - bh),
            )

    def test_by_m_of_one_equals_bh(self):
        """H(1) = 1, so BY and BH coincide at m = 1."""
        from proofhouse.eval.fdr import benjamini_hochberg, benjamini_yekutieli

        pairs = [("a", 0.04)]
        self.assertEqual(
            benjamini_hochberg(pairs, q=0.05)["k"],
            benjamini_yekutieli(pairs, q=0.05)["k"],
        )

    def test_by_empty_batch(self):
        from proofhouse.eval.fdr import benjamini_yekutieli

        self.assertEqual(benjamini_yekutieli([], q=0.05)["k"], 0)


class TestBoundaryComparator(unittest.TestCase):

    def test_p_exactly_at_threshold_is_rejected(self):
        """Determinism rule 3.2: the comparator is <=, matching the
        textbook procedure -- deliberately NOT the house `>` gate default
        (§0.3). Deviating from the standard formulation would make results
        incomparable to every published result.
        """
        from proofhouse.eval.fdr import benjamini_hochberg

        # m = 2, q = 0.05. Threshold at k=1 is exactly 0.025.
        res = benjamini_hochberg([("a", 0.025), ("b", 0.9)], q=0.05)
        self.assertEqual(res["k"], 1)
        self.assertEqual(res["rejected"], ["a"])


if __name__ == "__main__":
    unittest.main()
