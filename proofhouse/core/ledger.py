# proofhouse.core.ledger — append-only multiple-testing ledger
"""
Multiple-testing ledger for proofhouse.

Phase 0 Build Plan §4.3 and governance sub-rules 7a–7h:

    Append-only JSONL file. Every evaluation that computes a performance
    statistic increments the ledger. Counters are per-universe (7b);
    universes sharing any instrument share the count.

    Each row carries a content hash and prev pointer, forming a hash chain.
    This is the highest-value tampering target in the system — deleting
    rows lowers the trial count, which loosens every threshold downstream.

    "kind: explore | eval | gate | maintenance | overlap_declaration"
    "7e: Dedup by code hash + data hash + universe. Changed parameter = new trial."
    "7f: Row written at scoring time, not completion."
    "7g: kind: maintenance rows: logged, excluded from multiplicity."
"""

import json
import os
from datetime import datetime, timezone

from proofhouse.chain.canonical import decimal_str
from proofhouse.chain.hashing import content_hash


# Valid row kinds.
#
#   subperiod — one slice of a parent eval, written by stage-1b's consistency
#       check. NOT a separate hypothesis: k entries inspected over s
#       sub-periods is k trials, not k*s. Each sub-period has a different
#       data_hash, so rule 7e's dedup would otherwise count every slice
#       separately and inflate the trial count s-fold.
#
#       A subperiod row MUST carry parent_run_id. Without that link the kind
#       is a laundering hole — anything could be labelled `subperiod` to
#       escape the count. With it, every excluded row points at an included
#       one. Same shape as `maintenance`: excluded from the count, never
#       unlogged.
_VALID_KINDS = frozenset({"explore", "eval", "gate", "maintenance", "subperiod"})

# System kinds — written by the harness itself, not by a researcher's run.
#   screen — a selection DECISION, not a trial. Records the batch that
#       produced a survivor set: BH's threshold depends on every p-value in
#       the batch, so "these 12 survived at q=0.3" is unfalsifiable unless m
#       and the batch are recorded.
_SYSTEM_KINDS = frozenset({"overlap_declaration", "screen"})
_ALL_KINDS = _VALID_KINDS | _SYSTEM_KINDS

# Kinds excluded from the multiplicity count.
_EXCLUDED_KINDS = frozenset({
    "maintenance", "overlap_declaration", "screen", "subperiod",
})


class Ledger:
    """Append-only multiple-testing ledger with per-universe trial counting.

    The ledger is a JSONL file on disk. Each row records one evaluation,
    is content-hashed, and chained via prev pointers.

    Trial counts are per-universe, with deduplication by
    (code_hash, data_hash, universe) and maintenance rows excluded.

    Universes can be declared as overlapping (sharing instruments),
    in which case they share a combined trial count. Overlap declarations
    are persisted as special rows in the JSONL.
    """

    def __init__(self, path):
        """Initialize a ledger, loading existing rows from disk if present.

        Args:
            path: Absolute path to the ledger JSONL file.
        """
        self._path = path
        self._rows = []
        # Overlap groups: universe_id → group_id
        self._overlap_groups = {}
        # Counter: next run_id number
        self._next_run = 1
        # Last hash in the chain (for prev pointer)
        self._last_hash = None

        # Load existing rows
        if os.path.exists(path):
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    self._rows.append(row)
                    self._last_hash = row.get("hash")
                    self._next_run += 1

                    # Replay overlap declarations
                    if row.get("kind") == "overlap_declaration":
                        a = row.get("universe_a")
                        b = row.get("universe_b")
                        if a and b:
                            self._apply_overlap(a, b)

    def declare_overlap(self, universe_a, universe_b):
        """Declare that two universes share instruments.

        After this call, count_trials for either universe returns the
        combined count across both.

        The declaration is persisted as a special row in the ledger JSONL,
        so it survives across sessions.

        Uses union-find internally to support transitive overlap
        (if A overlaps B and B overlaps C, all three share a count).
        """
        self._apply_overlap(universe_a, universe_b)

        # Persist the declaration as a chained ledger row
        row = {
            "run_id": "OVL-{:06d}".format(self._next_run),
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "overlap_declaration",
            "universe_a": universe_a,
            "universe_b": universe_b,
        }
        self._next_run += 1
        self._write_and_chain(row)

    def _apply_overlap(self, universe_a, universe_b):
        """Apply overlap in memory (union-find merge)."""
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
                   registry_ref=None, outcome=None,
                   stage=None, segment=None,
                   p_value=None, n_effective=None,
                   parent_run_id=None, entry_hash=None,
                   result_hash=None, manifest_hash=None,
                   source_eval_run_id=None, criteria=None):
        """Append a row to the ledger. Written to disk immediately (7f).

        Each row is content-hashed and chained via prev pointers.
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
            stage: Optional stage label (1 or 2). Legacy rows without
                   this field are handled explicitly in count methods.
            segment: Optional segment label ('E', 'S1', 'S2', 'V', etc.).
            result_hash: Digest of the manifest's `result` block. On an eval
                   row it is the result this run produced; on a gate row it
                   is the result the verdict was rendered against, and the
                   two must agree.
            manifest_hash: Digest of the whole manifest (run_id excluded).
            source_eval_run_id: Gate rows only — the eval run whose recorded
                   result this verdict was computed from.
            criteria: Gate rows only — the exact CriterionOutcome dicts.
                   Numeric fields are already canonical decimal strings
                   there, so canonical rule 3 is satisfied without
                   conversion.

        Raises:
            ValueError: If kind is invalid, or if a gate-only field is set on
                a row of another kind.
        """
        if kind not in _VALID_KINDS:
            raise ValueError(
                "Invalid kind '{}'. Must be one of: {}".format(
                    kind, ", ".join(sorted(_VALID_KINDS))
                )
            )

        # A subperiod row is excluded from the multiplicity count, so it must
        # prove it decomposes a counted run. Without this, `subperiod` is a
        # way to run trials that never hit the counter.
        if kind == "subperiod" and not parent_run_id:
            raise ValueError(
                "kind='subperiod' requires parent_run_id. Sub-period rows are "
                "excluded from the trial count, so each must point at the eval "
                "run it decomposes — otherwise the kind is a laundering hole."
            )
        if kind != "subperiod" and parent_run_id:
            raise ValueError(
                "parent_run_id is only meaningful for kind='subperiod'; "
                "got kind={!r}".format(kind)
            )

        # A row claiming criterion outcomes is claiming a verdict was
        # rendered. Only the gate path renders one — an explicit replay is
        # kind='maintenance', which is logged and excluded from the
        # multiplicity count, so it is the same claim about the same numbers
        # and carries the same fields. Any other kind asserting them would be
        # a verdict recorded outside the gate path, which is the whole thing
        # this binding exists to make impossible.
        if kind not in ("gate", "maintenance"):
            if source_eval_run_id is not None or criteria is not None:
                raise ValueError(
                    "source_eval_run_id and criteria record a rendered "
                    "verdict and are only meaningful for kind='gate' (or an "
                    "explicit kind='maintenance' replay); got "
                    "kind={!r}".format(kind)
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

        # Stage and segment are Phase 1 fields. Legacy rows (Phase 0)
        # will not have them. Both fields are included only if set, so
        # Phase 0 rows keep their hashes unchanged.
        if stage is not None:
            row["stage"] = stage
        if segment is not None:
            row["segment"] = segment

        # p_value and n_effective are Phase 1 Completion (Part A) fields.
        #
        # A screening decision is not auditable from a survivor list alone:
        # BH's threshold depends on every p-value in the batch, so the
        # p-values must be in the record, not only in the survivors. Same
        # reasoning that made the block-consumption ledger worth building.
        #
        # Both are omitted when None, so every pre-existing row keeps its
        # hash unchanged (canonical serialization strips nulls).
        # p_value is stored as a canonical DECIMAL STRING, not a float —
        # canonical rule 3 forbids floats in hashed records so that an
        # independent verifier reproduces identical bytes. Nowhere does
        # that matter more: a p-value a few ulps either side of a
        # Benjamini-Hochberg threshold decides whether a hypothesis
        # survives. n_effective is an integer count and needs no conversion.
        if p_value is not None:
            row["p_value"] = decimal_str(p_value)
        if n_effective is not None:
            row["n_effective"] = n_effective
        if parent_run_id is not None:
            row["parent_run_id"] = parent_run_id

        # Phase 2b. WHICH VERSION of the entry this run used.
        #
        # §3.1 requires an amendment to be logged before the code it
        # authorizes runs. That is unfalsifiable unless runs record the entry
        # version they ran against — otherwise "the amendment came first" is
        # prose, not a checkable claim. Paired with an amendment's
        # entry_hash_after, check_amendment_ordering can reconstruct which
        # version was in force at each run and flag any that ran ahead of
        # their paperwork.
        #
        # Must be set BEFORE hashing, like every other field: adding it to the
        # row afterwards would leave the stored hash disagreeing with the
        # row's contents and break the chain on the next verify.
        #
        # Omitted when None, so every pre-existing row keeps its hash.
        if entry_hash is not None:
            row["entry_hash"] = entry_hash

        # Phase 2d. WHICH RESULT this row is about.
        #
        # Every one of these is omitted when None, so every pre-existing row
        # keeps its hash — the same rule stage/segment/p_value/entry_hash
        # were added under. They are set BEFORE hashing for the same reason:
        # a field added to the row afterwards would leave the stored hash
        # disagreeing with the row's contents.
        #
        # These four are what turn "this exact run produced these exact
        # numbers, which produced this exact verdict" from prose into a
        # checkable claim. Before them, the chain proved nobody edited a ROW,
        # and the manifest holding the numbers sat outside it entirely.
        if result_hash is not None:
            row["result_hash"] = result_hash
        if manifest_hash is not None:
            row["manifest_hash"] = manifest_hash
        if source_eval_run_id is not None:
            row["source_eval_run_id"] = source_eval_run_id
        if criteria is not None:
            row["criteria"] = [dict(c) for c in criteria]

        self._write_and_chain(row)

    def append_screen(self, batch, m, q, method, threshold, survivors,
                      screen_stage, universe):
        """Record a screening DECISION. Not a trial — excluded from counting.

        Benjamini-Hochberg's rejection threshold depends on every p-value in
        the batch, so a survivor list on its own is an unfalsifiable claim:
        the same p-value survives at m=10 and fails at m=200. Recording the
        batch, m, q, method and threshold is what makes a screen
        reconstructible — the same reasoning that put p_value on eval rows.

        Args:
            batch: Entry IDs that entered the procedure (defines m).
            m: Batch size as seen by the FDR procedure.
            q: Target FDR level.
            method: 'bh' or 'by'.
            threshold: Largest passing (k/m)*q, or None when nothing passed.
            survivors: Entry IDs that survived.
            screen_stage: '1a' or '1b'.
            universe: Universe identifier.

        Returns:
            str: the assigned run_id.
        """
        row = {
            "run_id": "SCR-{:06d}".format(self._next_run),
            "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": "screen",
            "universe": universe,
            "screen_stage": screen_stage,
            "batch": list(batch),
            "m": m,
            # Floats become canonical decimal strings (rule 3) — a threshold
            # a few ulps out would change which hypotheses survive.
            "q": decimal_str(q),
            "fdr_method": method,
            "survivors": list(survivors),
        }
        if threshold is not None:
            row["threshold"] = decimal_str(threshold)

        self._next_run += 1
        self._write_and_chain(row)
        return row["run_id"]

    def _write_and_chain(self, row):
        """Hash, chain, persist, and track a row."""
        # Set prev pointer
        if self._last_hash is not None:
            row["prev"] = self._last_hash

        # Compute content hash (prev is included, hash is excluded)
        row["hash"] = content_hash(row)
        self._last_hash = row["hash"]

        self._rows.append(row)

        # Write to disk immediately. We use json.dumps (not canonical_bytes)
        # because canonical_bytes strips 'hash' — which is correct for hashing
        # but wrong for storage. The on-disk format must include 'hash' so
        # that verify() works after reload.
        parent_dir = os.path.dirname(self._path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(self._path, "a") as f:
            line = json.dumps(
                row, sort_keys=True, separators=(",", ":"),
                ensure_ascii=False,
            )
            f.write(line + "\n")

    def verify(self):
        """Verify the hash chain integrity of all ledger rows.

        Returns:
            Tuple of (ok: bool, errors: list of dicts).
            Each error dict has 'index', 'kind', and 'message'.
        """
        errors = []

        for i, row in enumerate(self._rows):
            # Check 1: hash integrity
            expected_hash = content_hash(row)
            stored_hash = row.get("hash")
            if stored_hash != expected_hash:
                errors.append({
                    "index": i,
                    "kind": "hash_mismatch",
                    "message": (
                        "Row {}: stored hash {} does not match "
                        "recomputed hash {}".format(i, stored_hash, expected_hash)
                    ),
                })

            # Check 2: prev pointer
            if i == 0:
                if "prev" in row:
                    errors.append({
                        "index": i,
                        "kind": "unexpected_prev",
                        "message": "Genesis row (index 0) must not have a prev field",
                    })
            else:
                stored_prev = row.get("prev")
                preceding_hash = self._rows[i - 1].get("hash")
                if stored_prev != preceding_hash:
                    errors.append({
                        "index": i,
                        "kind": "prev_mismatch",
                        "message": (
                            "Row {}: prev {} does not match "
                            "preceding row's hash {}".format(
                                i, stored_prev, preceding_hash
                            )
                        ),
                    })

        return (len(errors) == 0, errors)

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

            # 7g: maintenance and system kinds excluded
            if row.get("kind") in _EXCLUDED_KINDS:
                continue

            # 7e: dedup by (code_hash, data_hash, universe)
            key = (row.get("code_hash"), row.get("data_hash"), row_universe)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            count += 1

        return count

    def count_trials_stage2(self, universe):
        """Count trials for stage-2 multiplicity correction.

        CONSERVATIVE default: legacy rows (without 'stage' field) count
        toward stage-2 correction. This is the safe direction — a legacy
        row might have touched a V-block (no 'segment' field says otherwise),
        and excluding it understates multiplicity, producing false positives.

        Stage-1 rows (stage=1) are EXCLUDED from the stage-2 correction.
        This exclusion is valid because S1/S2 blocks were only touched
        by stage-1 survivors.

        Phase 1 plan: this is a deliberate design choice, not an accident.
        The alternative (refuse to run stage-2 until all legacy rows are
        classified) is stricter but the ledger has 0 rows at Phase 0 exit,
        so it costs nothing to be conservative.

        Args:
            universe: Universe identifier.

        Returns:
            int: Trial count for stage-2 multiplicity correction.
        """
        target_group = self._find_group(universe)
        related = set()
        for uid in set(u for u in self._overlap_groups):
            if self._find_group(uid) == target_group:
                related.add(uid)
        related.add(universe)

        seen_keys = set()
        count = 0

        for row in self._rows:
            row_universe = row.get("universe")
            if row_universe not in related:
                continue

            if row.get("kind") in _EXCLUDED_KINDS:
                continue

            key = (row.get("code_hash"), row.get("data_hash"), row_universe)
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Stage-1 rows are excluded from stage-2 correction.
            # Legacy rows (stage=None) are INCLUDED (conservative).
            row_stage = row.get("stage")
            if row_stage == 1:
                continue

            count += 1

        return count

    def all_rows(self):
        """Return all rows (copies)."""
        return [dict(r) for r in self._rows]
