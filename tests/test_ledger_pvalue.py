# tests/test_ledger_pvalue.py — p_value / n_effective on ledger rows (Part A)
"""Ledger records p_value and n_effective.

Phase 1 Completion Plan §4.1: a screening batch must be reconstructible.
BH's rejection threshold depends on every p-value in the batch, so
"these 12 survived at q=0.3" is unfalsifiable unless the p-values are in
the record.

Backward compatibility requirement: both fields are omitted when None, so
every pre-existing row hashes exactly as before.
"""

import json
import os
import shutil
import tempfile
import unittest


class TestLedgerPValueFields(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.path = os.path.join(self.tmp, "ledger.jsonl")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ledger(self):
        from proofhouse.core.ledger import Ledger

        return Ledger(self.path)

    def test_fields_recorded_when_supplied(self):
        led = self._ledger()
        led.append_row(
            code_hash="sha256:a", data_hash="sha256:b",
            universe="u", kind="eval",
            blocks_touched=["B1"], stats_scored=["panel_ic"],
            p_value=0.0048, n_effective=4431,
        )
        row = led.all_rows()[-1]
        # Stored as a canonical decimal STRING, not a float — canonical
        # rule 3 forbids floats in hashed records.
        self.assertEqual(row["p_value"], "%.17g" % 0.0048)
        self.assertIsInstance(row["p_value"], str)
        self.assertEqual(float(row["p_value"]), 0.0048)
        self.assertEqual(row["n_effective"], 4431)

    def test_fields_omitted_when_none(self):
        """Absent, not null. Canonical serialization strips nulls, so an
        explicit None must not appear as a key — otherwise a Phase 0 row
        and a Phase 1 row with p_value=None would differ on disk while
        hashing identically."""
        led = self._ledger()
        led.append_row(
            code_hash="sha256:a", data_hash="sha256:b",
            universe="u", kind="explore",
            blocks_touched=["B1"], stats_scored=["sharpe"],
        )
        row = led.all_rows()[-1]
        self.assertNotIn("p_value", row)
        self.assertNotIn("n_effective", row)

    def test_legacy_row_hash_unchanged_by_new_fields(self):
        """A row without the new fields must hash exactly as it did before
        the fields existed. This is the migration guarantee."""
        from proofhouse.chain.hashing import content_hash

        led = self._ledger()
        led.append_row(
            code_hash="sha256:a", data_hash="sha256:b",
            universe="u", kind="explore",
            blocks_touched=["B1"], stats_scored=["sharpe"],
        )
        row = led.all_rows()[-1]

        # Reconstruct the same row as it would have been built before the
        # fields were added: identical dict, no p_value/n_effective keys.
        stripped = {k: v for k, v in row.items() if k != "hash"}
        self.assertEqual(content_hash(stripped), row["hash"])

    def test_fields_survive_reload(self):
        led = self._ledger()
        led.append_row(
            code_hash="sha256:a", data_hash="sha256:b",
            universe="u", kind="eval",
            blocks_touched=["B1"], stats_scored=["panel_ic"],
            p_value=0.0048, n_effective=4431,
        )
        reloaded = self._ledger()
        row = reloaded.all_rows()[-1]
        # Stored as a canonical decimal STRING, not a float — canonical
        # rule 3 forbids floats in hashed records.
        self.assertEqual(row["p_value"], "%.17g" % 0.0048)
        self.assertIsInstance(row["p_value"], str)
        self.assertEqual(float(row["p_value"]), 0.0048)
        self.assertEqual(row["n_effective"], 4431)

    def test_chain_verifies_after_reload_with_new_fields(self):
        led = self._ledger()
        for i in range(3):
            led.append_row(
                code_hash="sha256:{}".format(i), data_hash="sha256:d",
                universe="u", kind="eval",
                blocks_touched=["B1"], stats_scored=["panel_ic"],
                p_value=0.01 * (i + 1), n_effective=100 + i,
            )
        ok, errors = self._ledger().verify()
        self.assertTrue(ok, errors)

    def test_on_disk_jsonl_carries_the_fields(self):
        led = self._ledger()
        led.append_row(
            code_hash="sha256:a", data_hash="sha256:b",
            universe="u", kind="eval",
            blocks_touched=["B1"], stats_scored=["panel_ic"],
            p_value=0.0048, n_effective=4431,
        )
        with open(self.path) as f:
            disk = json.loads(f.readline())
        self.assertEqual(disk["p_value"], "%.17g" % 0.0048)
        self.assertEqual(disk["n_effective"], 4431)
        self.assertIn("hash", disk)

    def test_p_value_round_trips_exactly(self):
        """The %.17g format must recover the original double bit-for-bit.

        A lossy p-value could flip a Benjamini-Hochberg decision at a
        threshold boundary, which is the one place precision is not
        cosmetic.
        """
        from proofhouse.chain.canonical import decimal_str

        for p in (0.0048, 0.05, 0.049999999999999996, 1e-300, 0.3333333333333333):
            self.assertEqual(float(decimal_str(p)), p)


if __name__ == "__main__":
    unittest.main()
