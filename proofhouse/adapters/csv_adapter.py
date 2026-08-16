# proofhouse.adapters.csv_adapter — generic CSV returns adapter
"""
Phase 1 Build Plan §1a — CSV Adapter.

Parent plan §4:
    "CSV/returns ingest is the v1 path."
    "adapter API designed after adapter #2."

Loads returns/trades from CSV files, computes deterministic data hashes,
and enforces point-in-time semantics.
"""

import csv
import hashlib
import os
from datetime import datetime


class CSVAdapter:
    """Load returns/trades data from CSV files.

    The adapter computes a deterministic SHA-256 hash of the file contents
    for provenance tracking. Hash is computed over sorted, canonical bytes
    so it's independent of OS line endings.

    Attributes:
        path: Path to the CSV file.
        data_hash: SHA-256 hash of the file contents.
    """

    def __init__(self, path):
        """Initialize from a CSV file path.

        Args:
            path: Absolute path to a CSV file.

        Raises:
            FileNotFoundError: If the file does not exist.
        """
        if not os.path.exists(path):
            raise FileNotFoundError("CSV file not found: {}".format(path))
        self.path = path
        self._rows = None
        self._headers = None
        self.data_hash = self._compute_hash()

    def _compute_hash(self):
        """Compute deterministic SHA-256 of the CSV file.

        Reads the file, normalizes line endings, sorts rows by all columns
        (so hash is independent of row order), and hashes the result.

        Returns:
            str: 'sha256:<hex>'
        """
        with open(self.path, "r", newline="") as f:
            content = f.read()

        # Normalize line endings and strip trailing whitespace
        lines = [line.strip() for line in content.splitlines() if line.strip()]

        if not lines:
            raw = b""
        else:
            # Keep header, sort data rows
            header = lines[0]
            data_lines = sorted(lines[1:])
            canonical = "\n".join([header] + data_lines)
            raw = canonical.encode("utf-8")

        digest = hashlib.sha256(raw).hexdigest()
        return "sha256:" + digest

    def load(self):
        """Load the CSV file into memory.

        Returns:
            List of dicts (one per row), keyed by column headers.
        """
        if self._rows is not None:
            return self._rows

        with open(self.path, "r", newline="") as f:
            reader = csv.DictReader(f)
            self._headers = reader.fieldnames
            self._rows = list(reader)

        return self._rows

    @property
    def headers(self):
        """Return the column headers."""
        if self._headers is None:
            self.load()
        return self._headers

    def load_returns(self, date_col="date", return_col="return",
                     cutoff_date=None):
        """Load returns data with optional point-in-time filtering.

        Args:
            date_col: Name of the date column.
            return_col: Name of the returns column.
            cutoff_date: Optional date string (YYYY-MM-DD). Only rows
                with date <= cutoff_date are returned. Enforces
                point-in-time semantics.

        Returns:
            List of (date_str, return_float) tuples, sorted by date.
        """
        rows = self.load()

        results = []
        for row in rows:
            date_str = row[date_col]
            ret = float(row[return_col])

            if cutoff_date is not None and date_str > cutoff_date:
                continue

            results.append((date_str, ret))

        results.sort(key=lambda x: x[0])
        return results
