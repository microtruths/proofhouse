# proofhouse.core.registry — append-only hypothesis registry
"""
Append-only registry for proofhouse entries.

Entries are stored as YAML files in registry/H-YYYY-NNN.yaml.
Each entry is validated (V1–V14, E1–E6), hashed, and chained
before being written to disk.

Registration fails loudly on any validation error.
"""

import copy
import os
from datetime import datetime, timezone

import yaml

from proofhouse.chain.chain import verify_chain
from proofhouse.chain.hashing import content_hash
from proofhouse.core.blocks import _SINGLE_USE_SEGMENTS
from proofhouse.schema.validate import validate_entry, ValidationError
from proofhouse.schema.versions import SCHEMA_VERSION


class Registry:
    """Append-only registry of hypothesis entries.

    Entries are content-hashed, chained via prev pointers, and persisted
    as individual YAML files. The chain is verified on load.
    """

    def __init__(self, registry_dir, block_registry=None):
        """Initialize the registry from a directory.

        Args:
            registry_dir: Path to the registry/ directory.
            block_registry: Optional BlockRegistry for block-level checks.
        """
        self._dir = registry_dir
        self._block_registry = block_registry
        self._entries = {}  # id -> entry dict
        self._chain = []  # ordered list of entries

        if os.path.exists(registry_dir):
            self._load()

    def _load(self):
        """Load all entries from disk, ordered by walking prev pointers.

        Chain order is reconstructed by following the prev → hash links,
        starting from the genesis record (the one with no prev).
        This is authoritative — timestamp sort would break on
        same-second collisions.
        """
        # Load all entries from disk
        all_entries = {}
        for fname in sorted(os.listdir(self._dir)):
            if not fname.endswith(".yaml"):
                continue
            path = os.path.join(self._dir, fname)
            with open(path) as f:
                entry = yaml.safe_load(f)
            if entry and isinstance(entry, dict):
                entry_hash = entry.get("hash")
                if entry_hash:
                    all_entries[entry_hash] = entry

        if not all_entries:
            return

        # Find genesis (no prev field)
        genesis = None
        for entry in all_entries.values():
            if "prev" not in entry:
                genesis = entry
                break

        if genesis is None:
            # Fallback: if no genesis found, sort by created_utc
            # (handles pre-chain records, shouldn't happen in practice)
            entries = sorted(
                all_entries.values(),
                key=lambda e: e.get("created_utc", ""),
            )
            self._chain = entries
            self._entries = {e["id"]: e for e in entries}
            return

        # Walk the chain from genesis
        chain = [genesis]
        # Build reverse index: prev_hash → entry
        by_prev = {}
        for entry in all_entries.values():
            prev = entry.get("prev")
            if prev:
                by_prev[prev] = entry

        current = genesis
        while current["hash"] in by_prev:
            current = by_prev[current["hash"]]
            chain.append(current)

        self._chain = chain
        self._entries = {e["id"]: e for e in chain}

    def ancestor_ids(self, entry):
        """Every entry this one descends from, transitively.

        The invalidating-amendment rule is about a LINEAGE, not a parent: an
        intermediate amendment must not be able to launder a block its
        grandparent already spent.

        Cycle-safe by a `seen` set as well as by construction — the registry
        is append-only, so depends_on cannot close a loop, but a walk that
        relies on that assumption is one schema change from hanging.
        """
        seen = set()
        queue = list(entry.get("depends_on") or [])
        while queue:
            current = queue.pop()
            if current in seen or current not in self._entries:
                continue
            seen.add(current)
            queue.extend(self._entries[current].get("depends_on") or [])
        return seen

    def _next_revision(self, entry):
        """How many times this pre-registration has been invalidated, +1.

        Counted from the LINEAGE, not from the entry's own amendments list.
        That list is inherited or not depending on whether the caller built
        the successor by copying the parent, so `len(amendments)` means two
        different things for two successors that are otherwise identical —
        and it under-reports, which is the wrong direction for a disclosure
        whose whole job is to show accumulated forks.

        Each ancestor's records already carry their own revision, so taking
        the maximum and adding one is monotone and construction-independent.
        """
        highest = 0
        for ancestor_id in self.ancestor_ids(entry):
            for record in self._entries[ancestor_id].get("amendments") or []:
                highest = max(highest, record.get("revision", 1))
        for record in entry.get("amendments") or []:
            highest = max(highest, record.get("revision", 0))
        return highest + 1

    def _declared_by_ancestors(self, entry):
        """Every validation block named anywhere in this entry's lineage."""
        declared = set()
        for ancestor_id in self.ancestor_ids(entry):
            declared.update(
                self._entries[ancestor_id].get("validation_blocks") or [])
        return declared

    def _spent_by_ancestors(self, entry, block_ids):
        """Map {block_id: consuming_ancestor} for blocks the lineage spent.

        Consumption by an UNRELATED entry is a different error and E4 raises
        it at gate time. Conflating them here would report a stranger's
        consumption as an amendment-lineage violation, which points the
        reader at the wrong problem.
        """
        ancestors = self.ancestor_ids(entry)
        spent = {}
        for block_id in sorted(block_ids):
            try:
                block = self._block_registry.get_block(block_id)
            except KeyError:
                # A block that is not registered cannot have been spent. The
                # missing-block problem is E6/V-8's to report.
                continue
            for consumption in block.get("consumed_by") or []:
                if consumption.get("entry_id") in ancestors:
                    spent[block_id] = consumption["entry_id"]
                    break
        return spent

    def register(self, entry):
        """Validate, hash, chain, and persist a new entry.

        Runs all validation checks (V1–V14, E1–E6). On any failure,
        raises ValidationError with all errors collected.

        On success, the entry is:
        1. Assigned a timestamp (created_utc)
        2. Assigned schema_version
        3. Content-hashed
        4. Chained (prev → last entry's hash)
        5. Written to disk as YAML

        Args:
            entry: Dict with all required fields.

        Returns:
            The registered entry (with hash, prev, created_utc filled).

        Raises:
            ValidationError: If any check fails.
            ValueError: If the entry ID already exists.
        """
        entry = copy.deepcopy(entry)

        # Set metadata
        entry["schema_version"] = SCHEMA_VERSION
        if not entry.get("created_utc"):
            entry["created_utc"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        entry["status"] = "registered"

        # Check for duplicate ID
        if entry.get("id") in self._entries:
            raise ValueError(
                "Entry {} already exists in the registry".format(entry["id"])
            )

        # Validate (V1–V14, E1–E6)
        validate_entry(
            entry,
            block_registry=self._block_registry,
            existing_entries=self._entries,
        )

        # ------------------------------------------------------------------
        # Pre-registration-invalidating amendments.
        #
        # Two fields cannot change without invalidating the pre-registration:
        #
        #   expected_effect_size — winner's curse. Stage-1 survivors carry
        #       inflated estimates; AgentCopy measured train +4,620 bps ->
        #       test -424 bps. A stage-1-informed effect size must not feed
        #       the stage-2 power check.
        #
        #   signal.parameters.polarity — one-sided p-values are legitimate
        #       ONLY because the direction was fixed before the data was
        #       seen. Allowing a post-hoc flip would silently convert every
        #       one-sided test into a two-sided one at half the stated
        #       p-value. Failure catalog #23 and #17 are both direction
        #       errors.
        #
        # Enforcement, not annotation: the amended entry must validate on
        # blocks its parent did not use. Recording the amendment without
        # requiring fresh blocks would leave the constraint advisory, which
        # is what "invalidates the validation window" is meant to prevent.
        # ------------------------------------------------------------------
        depends_on = entry.get("depends_on", [])
        if depends_on:
            from proofhouse.core.screen import (
                check_effect_size_amendment,
                check_polarity_amendment,
            )

            checks = (
                ("expected_effect_size", check_effect_size_amendment,
                 "winner's-curse risk: a stage-1-informed effect size must "
                 "not feed the stage-2 power check"),
                ("signal.parameters.polarity", check_polarity_amendment,
                 "one-sided p-values require the direction to have been "
                 "fixed before the data was seen"),
            )

            for dep_id in depends_on:
                if dep_id not in self._entries:
                    continue
                original = self._entries[dep_id]

                for field_name, check_fn, why in checks:
                    result = check_fn(entry, original)
                    if not result["amended"]:
                        continue

                    new_blocks = set(entry.get("validation_blocks", []))
                    # Overlap is measured against the WHOLE LINEAGE's
                    # declarations, not the immediate parent's. Against
                    # `original` alone, A -> B -> C computed an empty overlap
                    # whenever B happened to declare different blocks from A,
                    # so C could reuse anything A had spent.
                    reused = new_blocks & self._declared_by_ancestors(entry)

                    # FRESH MEANS UNSPENT, NOT UNDECLARED.
                    #
                    # validation_blocks states intent at registration;
                    # consumption happens later, at gate time. Comparing the
                    # two declared lists could not tell a named-and-untouched
                    # block from a spent one, so an entry amended before it
                    # ever gated was forced onto different blocks — spending
                    # MORE data to protect blocks that were already clean.
                    #
                    # Ancestors are walked transitively. The one-level loop
                    # was also a live hole in the other direction: A -> B -> C
                    # let C reuse a block A had spent, because C's list
                    # overlapped A's and only B was ever compared.
                    if self._block_registry is None:
                        # Conservative branch: absence of evidence must not
                        # read as evidence of cleanliness. Keyed on declared
                        # overlap, which is all there is to go on here.
                        if reused:
                            raise ValidationError([
                                "{} changed from {!r} to {!r} relative to "
                                "parent {}, which invalidates the "
                                "pre-registration — but validation_blocks "
                                "still reuse {}, and no block registry is "
                                "available to check whether those blocks were "
                                "ever spent ({}).".format(
                                    field_name, result["original"],
                                    result["current"], dep_id, sorted(reused),
                                    why,
                                )
                            ])
                        spent = {}
                    else:
                        # Checked over EVERY block this entry declares, not
                        # only the overlapping ones: an ancestor may have
                        # spent a block it never listed as validation (a
                        # stage-1 read, say), and validating on that is the
                        # same violation wearing different clothes.
                        spent = self._spent_by_ancestors(entry, new_blocks)

                    if spent:
                        raise ValidationError([
                            "{} changed from {!r} to {!r} relative to parent "
                            "{}, which invalidates the pre-registration — and "
                            "validation_blocks reuse {}, already spent by {}. "
                            "An amended entry must validate on blocks its "
                            "lineage did not use ({}).".format(
                                field_name,
                                result["original"],
                                result["current"],
                                dep_id,
                                sorted(spent),
                                ", ".join(sorted(set(spent.values()))),
                                why,
                            )
                        ])

                    # A permitted reuse is a WEAKER isolation claim than a
                    # fresh block, and the record must not flatten the two.
                    # The note used to say "validates on fresh blocks"
                    # unconditionally, which becomes a falsehood the moment
                    # reuse is allowed — and a later reader seeing reuse
                    # could not tell whether it was checked or missed.
                    if reused:
                        isolation = (
                            "reuses {}, verified unconsumed at registration — "
                            "a weaker isolation claim, holding only for as "
                            "long as nothing else spends them".format(
                                sorted(reused))
                        )
                    else:
                        isolation = "validates on fresh blocks"

                    entry.setdefault("amendments", [])
                    entry["amendments"].append({
                        "field": field_name,
                        "original": result["original"],
                        "current": result["current"],
                        "parent_id": dep_id,
                        "reused_blocks": sorted(reused),
                        "revision": self._next_revision(entry),
                        "note": (
                            "{} changed from {} to {} after registration of "
                            "parent {}. Pre-registration invalidated; this "
                            "entry {} ({})."
                            .format(
                                field_name,
                                result["original"],
                                result["current"],
                                dep_id,
                                isolation,
                                why,
                            )
                        ),
                    })

        # ------------------------------------------------------------------
        # A declared single-use block that anyone has already spent.
        #
        # E4 makes a V block single-use: consume_block refuses it for any
        # entry but the one that spent it. So an entry declaring such a block
        # can never gate on it, whoever the spender was — its own lineage (a
        # successor copied from a gated parent, BACKLOG D-10) or a stranger.
        # `verify` already reports both as an error. Reporting at
        # registration, while the choice is still open, is the whole point:
        # the record is append-only, and a successor that carried the spent
        # block in sat as an error no later successor could clear.
        #
        # The invalidating-amendment branch above has its own, more specific
        # message for the lineage case when an effect size or polarity moved;
        # this check is what runs when nothing invalidating changed, and for
        # a plain registration. Unspent blocks declared by a parent remain
        # reusable by its successor: fresh means unspent, not undeclared.
        # ------------------------------------------------------------------
        if self._block_registry is not None:
            ancestors = self.ancestor_ids(entry)
            spent_lines = []
            for block_id in entry.get("validation_blocks") or []:
                try:
                    block = self._block_registry.get_block(block_id)
                except KeyError:
                    continue        # V-8 / E6 territory, reported there
                if block.get("segment") not in _SINGLE_USE_SEGMENTS:
                    continue
                spenders = sorted({
                    c.get("entry_id")
                    for c in block.get("consumed_by") or []
                    if c.get("entry_id") and c.get("entry_id") != entry.get("id")
                })
                if not spenders:
                    continue
                relation = ("in this entry's own lineage"
                            if any(s in ancestors for s in spenders)
                            else "not related to this entry")
                spent_lines.append("{} (spent by {}, {})".format(
                    block_id, ", ".join(spenders), relation))
            if spent_lines:
                raise ValidationError([
                    "validation_blocks declare a block already spent: {}. A "
                    "validation block is spent once (E4), so this entry could "
                    "never gate on it and would sit as a permanent verify "
                    "error. For a successor that only records a change, set "
                    "validation_blocks: []. To validate again, name blocks "
                    "that postdate the change.".format("; ".join(spent_lines))
                ])

        # Hash and chain
        entry.pop("hash", None)
        entry.pop("prev", None)

        if self._chain:
            entry["prev"] = self._chain[-1]["hash"]

        entry["hash"] = content_hash(entry)

        # Persist
        os.makedirs(self._dir, exist_ok=True)
        fname = "{}.yaml".format(entry["id"])
        path = os.path.join(self._dir, fname)
        with open(path, "w") as f:
            yaml.dump(entry, f, default_flow_style=False, sort_keys=True,
                      allow_unicode=True)

        # Update in-memory state
        self._chain.append(entry)
        self._entries[entry["id"]] = entry

        return entry

    def get_entry(self, entry_id):
        """Get an entry by ID.

        Raises:
            KeyError: If not found.
        """
        if entry_id not in self._entries:
            raise KeyError("Entry not found: {}".format(entry_id))
        return copy.deepcopy(self._entries[entry_id])

    def list_entries(self):
        """Return all entries in chain order."""
        return [copy.deepcopy(e) for e in self._chain]

    def verify(self):
        """Verify the chain integrity of all entries.

        Returns:
            Tuple of (ok: bool, errors: list).
        """
        return verify_chain(self._chain)
