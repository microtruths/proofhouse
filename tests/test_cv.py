# tests/test_cv.py — Hand-computed tests for cross-validation
"""
Phase 1 Build Plan §1a — CV tests.

Parent plan §11:
    "embargo boundary off-by-one"

Tests written before implementation. Values computed by hand.
"""


class TestPurgedCV:
    """Purged cross-validation tests."""

    def test_purge_removes_boundary_observations(self):
        """Purge removes observations within h of the fold boundary.

        Dataset: indices 0..9, test fold = [5, 6, 7], purge h=1.
        Training before purge: [0, 1, 2, 3, 4, 8, 9]
        Purge window: [4] (h=1 before test start) and [8] (h=1 after test end)
        Training after purge: [0, 1, 2, 3, 9]
        """
        from proofhouse.eval.cv import purge_indices

        n = 10
        test_start = 5
        test_end = 7
        purge_h = 1

        train = purge_indices(n, test_start, test_end, purge_h)
        expected = [0, 1, 2, 3, 9]
        assert train == expected, (
            "Purged training indices should be {}. Got {}".format(expected, train)
        )

    def test_purge_off_by_one_start(self):
        """Off-by-one test: observation at exactly (test_start - h) IS purged.

        Dataset: indices 0..9, test fold = [5, 6], purge h=2.
        Purge before test: indices 3 and 4 (= test_start - h to test_start - 1)
        Purge after test: indices 7 and 8 (= test_end + 1 to test_end + h)
        Training after purge: [0, 1, 2, 9]
        """
        from proofhouse.eval.cv import purge_indices

        n = 10
        test_start = 5
        test_end = 6
        purge_h = 2

        train = purge_indices(n, test_start, test_end, purge_h)
        expected = [0, 1, 2, 9]
        assert train == expected, (
            "Off-by-one at purge boundary. Expected {}. Got {}".format(expected, train)
        )

    def test_purge_off_by_one_end(self):
        """Off-by-one test: observation at exactly (test_end + h) IS purged.

        Dataset: indices 0..7, test fold = [2, 3], purge h=1.
        Purge before test: index 1
        Purge after test: index 4
        Test: [2, 3]
        Training: [0, 5, 6, 7]
        """
        from proofhouse.eval.cv import purge_indices

        n = 8
        test_start = 2
        test_end = 3
        purge_h = 1

        train = purge_indices(n, test_start, test_end, purge_h)
        expected = [0, 5, 6, 7]
        assert train == expected, (
            "Off-by-one at purge end boundary. Expected {}. Got {}".format(expected, train)
        )

    def test_purge_h_zero_no_removal(self):
        """Purge h=0 removes no observations beyond test fold.

        Dataset: indices 0..5, test fold = [2, 3], purge h=0.
        Training: [0, 1, 4, 5]
        """
        from proofhouse.eval.cv import purge_indices

        n = 6
        test_start = 2
        test_end = 3
        purge_h = 0

        train = purge_indices(n, test_start, test_end, purge_h)
        expected = [0, 1, 4, 5]
        assert train == expected


class TestEmbargoedCV:
    """Embargoed cross-validation tests."""

    def test_embargo_removes_after_train(self):
        """Embargo ensures test data postdates training by embargo_h.

        With embargo_h > 0, additional observations after the train set
        are removed.

        Dataset: indices 0..9
        Fold: train=[0..4], test=[5..9], embargo_h=2
        After embargo: test becomes [7, 8, 9] (first 2 of test are embargoed)
        """
        from proofhouse.eval.cv import embargo_test_indices

        test_indices = [5, 6, 7, 8, 9]
        embargo_h = 2

        result = embargo_test_indices(test_indices, embargo_h)
        expected = [7, 8, 9]
        assert result == expected, (
            "Embargoed test indices should be {}. Got {}".format(expected, result)
        )

    def test_embargo_zero_no_removal(self):
        """Embargo h=0 removes nothing from the test set."""
        from proofhouse.eval.cv import embargo_test_indices

        test_indices = [5, 6, 7]
        embargo_h = 0

        result = embargo_test_indices(test_indices, embargo_h)
        assert result == [5, 6, 7]


class TestDeterministicSplits:
    """Deterministic seed tests."""

    def test_same_seed_same_splits(self):
        """Same seed produces identical CV splits."""
        from proofhouse.eval.cv import make_cv_splits

        splits_a = make_cv_splits(n=100, k=5, seed=42, purge_h=1, embargo_h=0)
        splits_b = make_cv_splits(n=100, k=5, seed=42, purge_h=1, embargo_h=0)

        for i, (a, b) in enumerate(zip(splits_a, splits_b)):
            assert a["train"] == b["train"], "Fold {} train indices differ".format(i)
            assert a["test"] == b["test"], "Fold {} test indices differ".format(i)

    def test_different_seed_different_splits(self):
        """Different seeds produce different CV splits."""
        from proofhouse.eval.cv import make_cv_splits

        splits_a = make_cv_splits(n=100, k=5, seed=42, purge_h=1, embargo_h=0)
        splits_b = make_cv_splits(n=100, k=5, seed=99, purge_h=1, embargo_h=0)

        # At least one fold should differ
        any_different = False
        for a, b in zip(splits_a, splits_b):
            if a["train"] != b["train"]:
                any_different = True
                break

        assert any_different, "Different seeds should produce different splits"
