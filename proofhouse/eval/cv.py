# proofhouse.eval.cv — purged + embargoed cross-validation
"""
Phase 1 Build Plan §1a — Cross-validation.

Parent plan §4 eval/cv/:
    Purged + embargoed CV, deterministic seeds.

Purged CV: observations in the purge window (±h around each test fold
boundary) are removed from training. This prevents information leakage
through autocorrelated features.

Embargoed CV: the first embargo_h observations of each test fold are
dropped, ensuring test data postdates all training data by at least
the embargo period.

All splits are reproducible from a seed stored in the manifest.
"""

import random


def purge_indices(n, test_start, test_end, purge_h):
    """Compute training indices after purging observations near the test fold.

    Purge removes observations within purge_h of the test fold boundaries:
    - Before test: indices in [test_start - purge_h, test_start - 1]
    - After test:  indices in [test_end + 1, test_end + purge_h]

    The observation at exactly (test_start - purge_h) IS purged.
    The observation at exactly (test_end + purge_h) IS purged.

    Args:
        n: Total number of observations.
        test_start: First index of the test fold (inclusive).
        test_end: Last index of the test fold (inclusive).
        purge_h: Purge window size (number of observations to remove
                 on each side of the test fold).

    Returns:
        List of training indices (sorted).
    """
    # Purge window before test
    purge_before_start = max(0, test_start - purge_h)
    # Purge window after test
    purge_after_end = min(n - 1, test_end + purge_h)

    train = []
    for i in range(n):
        # Skip test indices
        if test_start <= i <= test_end:
            continue
        # Skip purge window before test
        if purge_before_start <= i < test_start:
            continue
        # Skip purge window after test
        if test_end < i <= purge_after_end:
            continue
        train.append(i)

    return train


def embargo_test_indices(test_indices, embargo_h):
    """Remove the first embargo_h observations from a test fold.

    This ensures the test data postdates all training data by at least
    embargo_h observations, preventing forward-looking bias.

    Args:
        test_indices: Sorted list of test fold indices.
        embargo_h: Number of observations to embargo from the start.

    Returns:
        List of remaining test indices.
    """
    if embargo_h <= 0:
        return list(test_indices)
    return list(test_indices[embargo_h:])


def make_cv_splits(n, k, seed, purge_h, embargo_h):
    """Create k-fold CV splits with purging and embargo.

    The fold assignment is shuffled using the provided seed for
    reproducibility. Each fold's training indices are purged and
    test indices are embargoed.

    Args:
        n: Total number of observations.
        k: Number of folds.
        seed: Random seed for reproducibility.
        purge_h: Purge window size.
        embargo_h: Embargo window size.

    Returns:
        List of k dicts, each with:
            train: List of training indices.
            test: List of test indices (after embargo).
            test_raw: List of test indices (before embargo).
    """
    if k < 2:
        raise ValueError("k must be >= 2, got {}".format(k))
    if n < k:
        raise ValueError("n ({}) must be >= k ({})".format(n, k))

    # Shuffle indices deterministically
    rng = random.Random(seed)
    indices = list(range(n))
    rng.shuffle(indices)

    # Assign to folds
    fold_size = n // k
    remainder = n % k

    folds = []
    start = 0
    for i in range(k):
        # Distribute remainder across first 'remainder' folds
        end = start + fold_size + (1 if i < remainder else 0)
        fold_indices = sorted(indices[start:end])
        folds.append(fold_indices)
        start = end

    # Build splits
    splits = []
    for fold_idx in range(k):
        test_raw = folds[fold_idx]
        test_start = min(test_raw)
        test_end = max(test_raw)

        # Purge training indices
        train = purge_indices(n, test_start, test_end, purge_h)

        # Embargo test indices
        test = embargo_test_indices(test_raw, embargo_h)

        splits.append({
            "train": train,
            "test": test,
            "test_raw": test_raw,
        })

    return splits
