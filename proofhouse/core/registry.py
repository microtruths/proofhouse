# proofhouse.core.registry — append-only hypothesis registry
"""
Append-only registry for proofhouse entries.

Entries are stored as YAML files in registry/H-YYYY-NNN.yaml.
Each entry is validated (V1–V12, E1–E6), hashed, and chained
before being written to disk.

Registration fails loudly on any validation error.
"""

import copy
import os
from datetime import datetime, timezone

import yaml

from proofhouse.chain.chain import verify_chain
from proofhouse.chain.hashing import content_hash
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

    def register(self, entry):
        """Validate, hash, chain, and persist a new entry.

        Runs all validation checks (V1–V12, E1–E6). On any failure,
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

        # Validate (V1–V12, E1–E6)
        validate_entry(
            entry,
            block_registry=self._block_registry,
            existing_entries=self._entries,
        )

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
