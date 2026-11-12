"""Database-free behavioral coverage for the request-log JSON generator."""

import csv
import io
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

    def test_correlation_fields_and_allowlisted_metadata_survive_both_formats(self):
        log = SimpleNamespace(
            request_id="correlated",
            provider="synthetic-provider",
            api_key_id="gateway-key-1",
            generation_id="generation-1",
            agent_run_id="run-1",
            agent_parent_run_id="parent-1",
            agent_root_run_id="root-1",
            agent_id="agent-1",
            agent_framework="custom",
            agent_workflow="support",
            agent_step="answer",
            agent_turn=0,
            billing_mode="byok",
            cost_source="unpriced",
            cost=Decimal("0"),
            metadata={
                "application": 'checkout, "quoted"',
                "service": "café\nworker",
                "private_custom_field": "not in export allowlist",
            },
            request_body={"private": "not exported"},
            request_headers={"private": "not exported"},
        )
        json_row = self.array_body("".join(export.export_json(Queryset([log]))))[0]
        csv_row = next(
            csv.DictReader(io.StringIO("".join(export.export_csv(Queryset([log])))))
        )
        for field in (
            "api_key_id",
            "generation_id",
            "agent_run_id",
            "agent_parent_run_id",
            "agent_root_run_id",
            "agent_id",
            "agent_framework",
            "agent_workflow",
            "agent_step",
            "billing_mode",
            "cost_source",
        ):
            self.assertEqual(json_row[field], getattr(log, field))
            self.assertEqual(csv_row[field], getattr(log, field))
        self.assertEqual(json_row["agent_turn"], 0)
        self.assertEqual(csv_row["agent_turn"], "0")
        expected_metadata = {
            key: log.metadata[key] for key in ("application", "service")
        }
        self.assertEqual(json_row["metadata"], expected_metadata)
        self.assertEqual(json.loads(csv_row["metadata"]), expected_metadata)
        self.assertNotIn("private_custom_field", json_row["metadata"])
        self.assertNotIn("request_body", json_row)
        self.assertNotIn("request_headers", csv_row)
        self.assertEqual(json_row["cost"], 0)
        self.assertEqual(export.MAX_EXPORT_ROWS, 10_000)

    def test_missing_metadata_is_an_empty_object_and_nullable_identity_stays_null(self):
        log = SimpleNamespace(
            provider="synthetic-provider",
            billing_mode=None,
            agent_run_id=None,
            metadata=None,
        )
        row = self.array_body("".join(export.export_json(Queryset([log]))))[0]
        self.assertEqual(row["metadata"], {})
        self.assertIsNone(row["agent_run_id"])

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
