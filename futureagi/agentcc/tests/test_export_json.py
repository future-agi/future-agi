"""Database-free behavioral coverage for the request-log JSON generator."""

import json
import unittest
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from agentcc.services import export


class Queryset:
    def __init__(self, rows):
        self.rows = rows
        self.visited = 0

    def iterator(self, chunk_size):
        assert chunk_size == 1000
        for row in self.rows:
            self.visited += 1
            yield row


class JsonExportTests(unittest.TestCase):
    def array_body(self, content):
        try:
            body = json.loads(content)
        except json.JSONDecodeError as exc:
            self.fail(f"Expected one JSON array, not an invalid document: {exc.msg}")
        self.assertIsInstance(body, list)
        return body

    def test_multiple_rows_are_one_json_document(self):
        rows = [
            SimpleNamespace(
                request_id="first", provider="synthetic-provider", billing_mode="byok"
            ),
            SimpleNamespace(
                request_id="second", provider="synthetic-provider", billing_mode="byok"
            ),
        ]
        content = "".join(export.export_json(Queryset(rows)))
        self.assertEqual(
            [row["request_id"] for row in self.array_body(content)], ["first", "second"]
        )

    def test_empty_and_single_row_are_json_arrays(self):
        for rows in (
            [],
            [
                SimpleNamespace(
                    request_id="one", provider="synthetic-provider", billing_mode="byok"
                )
            ],
        ):
            with self.subTest(count=len(rows)):
                result = self.array_body("".join(export.export_json(Queryset(rows))))
                self.assertIsInstance(result, list)
                self.assertEqual(len(result), len(rows))

    def test_preserves_field_types_and_escapes_strings(self):
        rows = [
            SimpleNamespace(
                request_id='quote " and newline\n',
                provider="synthetic-provider",
                billing_mode="byok",
                started_at=datetime(2026, 10, 27, tzinfo=UTC),
                cost=Decimal("0.25"),
                status_code=200,
                is_error=False,
                error_message=None,
            )
        ]
        row = self.array_body("".join(export.export_json(Queryset(rows))))[0]
        self.assertEqual(row["request_id"], rows[0].request_id)
        self.assertEqual(row["provider"], "synthetic-provider")
        self.assertEqual(row["started_at"], "2026-10-27T00:00:00+00:00")
        self.assertEqual(row["cost"], 0.25)
        self.assertEqual(row["status_code"], 200)
        self.assertIs(row["is_error"], False)
        self.assertIsNone(row["error_message"])

    def test_streams_without_materializing_the_queryset(self):
        queryset = Queryset(
            SimpleNamespace(
                request_id=str(i), provider="synthetic-provider", billing_mode="byok"
            )
            for i in range(3)
        )
        stream = export.export_json(queryset)
        self.assertEqual(queryset.visited, 0)
        prefix = next(stream)
        self.assertLessEqual(queryset.visited, 1)
        result = self.array_body(prefix + "".join(stream))
        self.assertEqual(len(result), 3)
        self.assertEqual(queryset.visited, 3)
