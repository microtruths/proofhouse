# proofhouse.core.ledger — append-only multiple-testing ledger
"""
Multiple-testing ledger for proofhouse.

Phase 0 Build Plan §4.3 and governance sub-rules 7a–7h:

    Append-only JSONL file. Every evaluation that computes a performance
    statistic increments the ledger. Counters are per-universe (7b);
    universes sharing any instrument share the count.

    "kind: explore | eval | gate | maintenance"
    "7e: Dedup by code hash + data hash + universe. Changed parameter = new trial."
    "7f: Row written at scoring time, not completion."
    "7g: kind: maintenance rows: logged, excluded from multiplicity."
"""

import json
import os
from datetime import datetime, timezone


# Valid row kinds
_VALID_KINDS = frozenset({"explore", "eval", "gate", "maintenance"})

# Kinds excluded from the multiplicity count
_EXCLUDED_KINDS = frozenset({"maintenance"})


class Ledger:
    """Append-only multiple-testing ledger with per-universe trial counting.

    The ledger is a JSONL file on disk. Each row records one evaluation.
    Trial counts are per-universe, with deduplication by
    (code_hash, data_hash, universe) and maintenance rows excluded.

    Universes can be declared as overlapping (sharing instruments),
    in which case they share a combined trial count.
    """

    def __init__(self, path):
        """Initialize a ledger, loading existing rows from disk if present.

        Args:
            path: Absolute path to the ledger JSONL file.
        """
        self._path = path
        self._rows = []
        # Dedup index: (code_hash, data_hash, universe) → True
        self._seen = set()
        # Overlap groups: universe_id → group_id
        self._overlap_groups = {}
        # Counter: next run_id number
        self._next_run = 1

        # Load existing rows
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self._rows.append(row)
                    key = (row.get("code_hash"), row.get("data_hash"), row.get("universe"))
                    self._seen.add(key)
                    self._next_run += 1

    def declare_overlap(self, universe_a, universe_b):
        """Declare that two universes share instruments.

        After this call, count_trials for either universe returns the
        combined count across both.

        Uses union-find internally to support transitive overlap
        (if A overlaps B and B overlaps C, all three share a count).
        """
        group_a = self._find_group(universe_a)
        group_b = self._find_group(universe_b)

        if group_a != group_b:
            # Merge: point everything in group_b to group_a
            for uid, gid in list(self._overlap_groups.items()):
                if gid == group_b:
                    self._overlap_groups[uid] = group_a

    def _find_group(self, universe_id):
        """Find the overlap group for a universe (union-find root)."""
        if universe_id not in self._overlap_groups:
            self._overlap_groups[universe_id] = universe_id
        return self._overlap_groups[universe_id]

    def append_row(self, code_hash, data_hash, universe, kind,
                   blocks_touched, stats_scored,
                   registry_ref=None, outcome=None):
        """Append a row to the ledger. Written to disk immediately (7f).

        The row is always written to the JSONL file. Deduplication
        affects counting, not logging — a duplicate row is logged but
        not counted as a new trial.

        Args:
            code_hash: SHA-256 of the code that ran.
            data_hash: SHA-256 of the data snapshot.
            universe: Universe identifier.
            kind: One of 'explore', 'eval', 'gate', 'maintenance'.
            blocks_touched: List of block IDs touched by this run.
            stats_scored: List of statistic names computed.
            registry_ref: Optional entry ID this run is linked to.
            outcome: Optional outcome description.

        Raises:
            ValueError: If kind is invalid.
        """
        if kind not in _VALID_KINDS:
            raise ValueError(
                "Invalid kind '{}'. Must be one of: {}".format(
                    kind, ", ".join(sorted(_VALID_KINDS))
                )
            )

        run_id = "R-{:06d}".format(self._next_run)
        self._next_run += 1

        row = {
            "run_id": run_id,
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": kind,
            "registry_ref": registry_ref,
            "universe": universe,
            "blocks_touched": blocks_touched,
            "code_hash": code_hash,
            "data_hash": data_hash,
            "stats_scored": stats_scored,
            "outcome": outcome,
        }

        self._rows.append(row)
        key = (code_hash, data_hash, universe)
        self._seen.add(key)

        # Write to disk immediately (7f: at scoring time, not completion)
        parent_dir = os.path.dirname(self._path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(self._path, "a") as f:
            f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    def count_trials(self, universe):
        """Count unique, non-maintenance trials for a universe.

        Dedup by (code_hash, data_hash, universe) per rule 7e.
        Maintenance rows excluded per rule 7g.
        Overlapping universes (declared via declare_overlap) share counts.

        Args:
            universe: Universe identifier.

        Returns:
            int: Number of unique non-maintenance trials.
        """
        target_group = self._find_group(universe)

        # Find all universes in the same overlap group
        related = set()
        for uid in set(u for u in self._overlap_groups):
            if self._find_group(uid) == target_group:
                related.add(uid)
        # Always include the queried universe itself
        related.add(universe)

        seen_keys = set()
        count = 0

        for row in self._rows:
            row_universe = row.get("universe")
            if row_universe not in related:
                continue

            # 7g: maintenance excluded
            if row.get("kind") in _EXCLUDED_KINDS:
                continue

            # 7e: dedup by (code_hash, data_hash, universe)
            key = (row.get("code_hash"), row.get("data_hash"), row_universe)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            count += 1

        return count

    def all_rows(self):
        """Return all rows (copies)."""
        return [dict(r) for r in self._rows]
