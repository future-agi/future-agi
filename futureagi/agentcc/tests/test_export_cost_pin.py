"""Database-free pin of the export's cost cell: the stored cost, as it is.

Logs reads a request no charge was computed for (a cost of 0 and no cost
source) as a dash, but the CSV and JSON export keep writing the stored cost:
scripts read the column as a number. So a request no charge was computed for
exports 0, an unpriced request at a cost of 0 exports 0, and a priced request
exports its amount.
"""

import csv
import io
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from agentcc.services import export


class Queryset:
    def __init__(self, rows):
        self.rows = rows

    def iterator(self, chunk_size):
        yield from self.rows


def _log(request_id, cost, cost_source, billing_mode):
    return SimpleNamespace(
        request_id=request_id,
        provider="synthetic-provider",
        billing_mode=billing_mode,
        cost=cost,
        cost_source=cost_source,
        metadata={},
    )


NO_CHARGE = _log("no-charge", Decimal("0.000000"), None, "managed")
UNPRICED_ZERO = _log("unpriced", Decimal("0.000000"), "unpriced", "byok")
PRICED = _log("priced", Decimal("0.042000"), "catalog", "managed")
ROWS = [NO_CHARGE, UNPRICED_ZERO, PRICED]


class ExportWritesTheStoredCostTests(unittest.TestCase):
    def json_costs(self):
        rows = json.loads("".join(export.export_json(Queryset(ROWS))))
        return {row["request_id"]: row["cost"] for row in rows}

    def csv_costs(self):
        content = "".join(export.export_csv(Queryset(ROWS)))
        reader = csv.DictReader(io.StringIO(content))
        return {row["request_id"]: row["cost"] for row in reader}

    def test_no_charge_computed_exports_zero(self):
        self.assertEqual(self.json_costs()["no-charge"], 0)
        self.assertIsNotNone(self.json_costs()["no-charge"])
        cell = self.csv_costs()["no-charge"]
        self.assertNotEqual(cell, "")
        self.assertEqual(Decimal(cell), 0)

    def test_an_unpriced_zero_exports_zero(self):
        self.assertEqual(self.json_costs()["unpriced"], 0)
        self.assertIsNotNone(self.json_costs()["unpriced"])
        cell = self.csv_costs()["unpriced"]
        self.assertNotEqual(cell, "")
        self.assertEqual(Decimal(cell), 0)

    def test_a_priced_request_exports_its_amount(self):
        self.assertEqual(self.json_costs()["priced"], 0.042)
        self.assertEqual(Decimal(self.csv_costs()["priced"]), Decimal("0.042"))

    def test_the_cost_is_a_number_in_json(self):
        for request_id, cost in self.json_costs().items():
            with self.subTest(request_id=request_id):
                self.assertIsInstance(cost, float)


if __name__ == "__main__":
    unittest.main()
