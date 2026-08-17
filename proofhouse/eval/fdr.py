# proofhouse.eval.fdr — false discovery rate control
"""Benjamini-Hochberg and Benjamini-Yekutieli step-up procedures.

Part B plan §2.

Both are pure functions over [(entry_id, p_value)]. They touch no ledger,
no blocks, no registry — Part C's run_screen supplies the batch and
records the outcome.

    BH  controls FDR under independence or positive regression dependence.
    BY  controls FDR under ARBITRARY dependence, replacing q with
        q / H(m) where H is the harmonic number.

Which to use is a registered per-entry choice (fdr_method), never a
default. Hypotheses screened on the same universe share data and are
therefore dependent, which is exactly where BH's assumption is
questionable — Harvey, Liu & Zhu recommend BY for this reason. The cost
is real: at m = 50, H(m) = 4.50, so BY at q = 0.3 behaves like BH at
q = 0.067.

THE STEP-UP PROPERTY (the thing to get right):
    Find the LARGEST k with p(k) <= (k/m)*q, then reject ranks 1..k.

    Ranks below k that fail their own threshold are STILL rejected. The
    intuitive "walk up until one fails, stop there" reading is step-DOWN,
    controls a different quantity, and returns too few. See test F-1.
"""


def harmonic_number(m):
    """Return H(m) = sum(1/i) for i = 1..m. H(0) = 0.

    Used by Benjamini-Yekutieli to deflate q for arbitrary dependence.

    Summed in ascending order of i (largest terms first). At the batch
    sizes here the ordering is numerically irrelevant, but it is fixed so
    the value is reproducible rather than incidentally stable.
    """
    if m < 0:
        raise ValueError("m must be non-negative; got {!r}".format(m))
    total = 0.0
    for i in range(1, int(m) + 1):
        total += 1.0 / i
    return total


def _validate_batch(pairs, q):
    """Validate inputs and return the batch sorted for the step-up scan.

    Sort key is (p_value, entry_id) — determinism rule 3.1. Ties break by
    id, never by input order, so a screening decision cannot depend on how
    the batch happened to be assembled.
    """
    if not (0.0 < q <= 1.0):
        raise ValueError("q must be in (0, 1]; got {!r}".format(q))

    seen = set()
    cleaned = []
    for item in pairs:
        entry_id, p = item
        if p is None:
            raise ValueError(
                "entry {!r} has p_value=None. A missing p-value is never "
                "coerced: 1.0 would silently drop the hypothesis and 0.0 "
                "would silently pass it.".format(entry_id)
            )
        if not (0.0 <= p <= 1.0):
            raise ValueError(
                "entry {!r} has p_value {!r} outside [0, 1]".format(entry_id, p)
            )
        if entry_id in seen:
            raise ValueError(
                "duplicate entry id {!r} in batch — the result would be "
                "ambiguous".format(entry_id)
            )
        seen.add(entry_id)
        cleaned.append((entry_id, float(p)))

    cleaned.sort(key=lambda t: (t[1], t[0]))
    return cleaned


def _step_up(sorted_pairs, q_effective):
    """Run the step-up scan and return (k, threshold, rejected_ids).

    Verbatim from the Part B plan §2.1:
        "Sort ascending, find the largest k with p(k) <= (k/m)*q,
         reject 1...k."

    Note the scan runs over EVERY k and keeps the largest that passes. It
    does not stop at the first failure.
    """
    m = len(sorted_pairs)
    if m == 0:
        return 0, None, []

    best_k = 0
    best_threshold = None

    for idx, (_entry_id, p) in enumerate(sorted_pairs):
        k = idx + 1
        threshold = (k / float(m)) * q_effective
        if p <= threshold:          # rule 3.2: <=, matching the textbook
            best_k = k
            best_threshold = threshold

    rejected = [entry_id for entry_id, _p in sorted_pairs[:best_k]]
    return best_k, best_threshold, rejected


def benjamini_hochberg(pairs, q):
    """Benjamini-Hochberg step-up FDR control.

    Controls FDR at level q under independence or positive regression
    dependence (PRDS).

    Args:
        pairs: Iterable of (entry_id, p_value).
        q: Target false discovery rate in (0, 1].

    Returns:
        Dict with:
            method: 'bh'
            m: batch size (the count entering the procedure)
            k: number rejected
            threshold: the largest passing (k/m)*q, or None if k = 0
            rejected: list of entry_ids, ordered by (p_value, entry_id)
            q: the q supplied

    Raises:
        ValueError: on out-of-range q or p, a None p-value, or duplicate ids.
    """
    sorted_pairs = _validate_batch(pairs, q)
    k, threshold, rejected = _step_up(sorted_pairs, q)
    return {
        "method": "bh",
        "m": len(sorted_pairs),
        "k": k,
        "threshold": threshold,
        "rejected": rejected,
        "q": q,
    }


def benjamini_yekutieli(pairs, q):
    """Benjamini-Yekutieli step-up FDR control under arbitrary dependence.

    Identical to BH with q replaced by q / H(m). Because H(m) >= 1 for
    m >= 1, BY's rejection set is always a subset of BH's on the same
    batch (test F-5).

    Args:
        pairs: Iterable of (entry_id, p_value).
        q: Target false discovery rate in (0, 1].

    Returns:
        Same shape as benjamini_hochberg, plus:
            q_effective: q / H(m), the deflated level actually applied
    """
    sorted_pairs = _validate_batch(pairs, q)
    m = len(sorted_pairs)

    if m == 0:
        return {
            "method": "by", "m": 0, "k": 0, "threshold": None,
            "rejected": [], "q": q, "q_effective": q,
        }

    q_effective = q / harmonic_number(m)
    k, threshold, rejected = _step_up(sorted_pairs, q_effective)
    return {
        "method": "by",
        "m": m,
        "k": k,
        "threshold": threshold,
        "rejected": rejected,
        "q": q,
        "q_effective": q_effective,
    }
