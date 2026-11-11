"""Shared wire fixtures and fail-closed binding/read component contracts."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from tracer.services.attachments.binding import (
    IngestionBinding,
    Parent,
    ParentState,
    PendingLink,
    Scope,
    Writer,
    reconcile_pending,
    stage_ingested_span,
)
from tracer.services.attachments.reads import (
    AuthorizedStatus,
    ReadTarget,
    attachment_statuses,
)
from tracer.services.attachments.references import (
    NONCE,
    REFS,
    SCHEMA,
    SESSION,
    InvalidReference,
    canonical_trace_id,
    extract_projection,
    is_attachment_media,
    normalize_attributes,
    parse_references,
)

FIXTURES = (
    Path(__file__).resolve().parents[3]
    / "fi-collector/testdata/attachment_references.json"
)
A = "12345678-1234-1234-1234-123456789abc"
B = "87654321-4321-4321-4321-cba987654321"
C = "11111111-1111-1111-1111-111111111111"
REF = {
    "attachment_id": A,
    "occurrence_id": B,
    "direction": "mixed",
    "modality": "audio",
    "mime_type": "audio/wav",
}
SCOPE = Scope(A, B, C)
PARENT = Parent(A, "1234567890abcdef")
NOW = datetime(2026, 11, 10, tzinfo=UTC)
ATTRS = {SCHEMA: 1, REFS: json.dumps([REF]), SESSION: A, NONCE: B}


class AttachmentReferencesTests(TestCase):
    def test_shared_reference_fixtures(self):
        for case in json.loads(FIXTURES.read_text())["references"]:
            with self.subTest(case=case["name"]):
                if case["valid"]:
                    refs = parse_references(case["version"], case["raw"])
                    self.assertEqual(list(refs), json.loads(case["raw"]))
                    normalized = normalize_attributes(
                        {SCHEMA: case["version"], REFS: case["raw"]}
                    )
                    self.assertEqual(json.loads(normalized[REFS]), list(refs))
                else:
                    with self.assertRaisesRegex(
                        InvalidReference, "^invalid_attachment_reference$"
                    ):
                        parse_references(case["version"], case["raw"])
                    self.assertNotIn(
                        REFS,
                        normalize_attributes(
                            {SCHEMA: case["version"], REFS: case["raw"]}
                        ),
                    )

    def test_shared_identity_fixtures(self):
        for case in json.loads(FIXTURES.read_text())["identities"]:
            with self.subTest(case=case["input"]):
                if case["output"]:
                    self.assertEqual(canonical_trace_id(case["input"]), case["output"])
                else:
                    with self.assertRaises(InvalidReference):
                        canonical_trace_id(case["input"])

    def test_limits_and_invalid_types(self):
        invalid = [
            json.dumps([REF] * 65),
            " " * 16385,
            "[" * 5000,
            "\ud800",
            json.dumps([dict(REF, channel=True)]),
            json.dumps([dict(REF, message_index=1.0)]),
            json.dumps([dict(REF, channel=32)]),
            json.dumps([dict(REF, direction="input|output")]),
        ]
        for raw in invalid:
            with self.subTest(length=len(raw)):
                with self.assertRaises(InvalidReference):
                    parse_references(1, raw)
        for version in (True, 1.0, "1", None):
            with self.assertRaises(InvalidReference):
                parse_references(version, "[]")
        unique = [
            dict(REF, occurrence_id=f"00000000-0000-0000-0000-{i + 1:012x}")
            for i in range(64)
        ]
        self.assertEqual(len(parse_references(1, json.dumps(unique))), 64)
        with self.assertRaises(InvalidReference):
            parse_references(1, json.dumps([REF, REF]))

    def test_secret_fields_denied_not_echoed(self):
        for field in (
            "source_url",
            "filename",
            "hash",
            "object_key",
            "token",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    normalize_attributes(
                        {
                            SCHEMA: 1,
                            REFS: json.dumps([dict(REF, **{field: "canary-secret"})]),
                            "safe": 2,
                        }
                    ),
                    {"safe": 2},
                )

    def test_carriers_removed_and_forged_stamps_discard_projection(self):
        clean = normalize_attributes(ATTRS)
        self.assertEqual(set(clean), {SCHEMA, REFS})
        self.assertEqual(normalize_attributes(clean), clean)
        self.assertEqual(
            normalize_attributes(dict(ATTRS, **{"fi.attachment.verified": True})), {}
        )
        with self.assertRaises(InvalidReference):
            extract_projection({SCHEMA: 1, REFS: "[]", SESSION: A})

    def test_new_scheme_never_legacy_including_malformed(self):
        for value in (
            "fi-attachment:" + B,
            "FI-ATTACHMENT:broken",
            {"uri": "fi-attachment:broken"},
        ):
            self.assertTrue(is_attachment_media(value))
        self.assertFalse(is_attachment_media("https://example.invalid/legacy.wav"))


class AttachmentBindingTests(TestCase):
    def setUp(self):
        self.link = PendingLink(
            1, "scope-pk", SCOPE, PARENT, A, B, "mixed", "audio", NOW,
            NOW + timedelta(hours=24),
        )
        self.source = ParentState(
            SCOPE, PARENT, True, {B: REF}, "source-state", NOW + timedelta(hours=2)
        )
        self.store = Mock()
        self.store.claim_pending.return_value = [self.link]
        self.store.settle.return_value = True
        self.reader = Mock()
        self.reader.latest.return_value = {(SCOPE, PARENT): self.source}

    def test_stage_requires_authenticated_writer_and_session(self):
        args = {
            "attributes": ATTRS,
            "trace_id": A.replace("-", ""),
            "span_id": PARENT.span_id,
            "now": NOW,
        }
        self.assertFalse(stage_ingested_span(**args, writer=None, store=self.store))
        self.store.stage.assert_not_called()
        writer = Writer(A, SCOPE)
        self.store.stage.return_value = False  # broker rejects forged session
        self.assertFalse(stage_ingested_span(**args, writer=writer, store=self.store))
        self.store.stage.return_value = True
        self.assertTrue(stage_ingested_span(**args, writer=writer, store=self.store))
        candidate = self.store.stage.call_args.args[0]
        self.assertEqual(candidate.parent, PARENT)
        self.assertEqual(candidate.references, ((A, B),))

    def test_mixed_project_does_not_inherit_first_scope(self):
        binding = IngestionBinding(Writer(A, SCOPE), self.store)
        project = SimpleNamespace(id=B, organization_id=A, workspace_id=B)
        self.assertFalse(binding.stage({"attributes": ATTRS}, project, A, NOW))
        self.store.stage.assert_not_called()

    def test_arrival_permutations_need_no_sdk_retry(self):
        # Attachment before span, span before its references, span first.
        for name, parents in (
            ("attachment_first", {}),
            (
                "span_without_refs_yet",
                {(SCOPE, PARENT): replace(self.source, references={})},
            ),
            ("span_first", {(SCOPE, PARENT): self.source}),
        ):
            with self.subTest(order=name):
                self.reader.latest.return_value = parents
                counts = reconcile_pending(self.store, self.reader, now=NOW)
                self.assertEqual(counts["bound"], int(name == "span_first"))
                self.assertEqual(counts["pending"], int(name != "span_first"))
                self.reader.latest.return_value = {(SCOPE, PARENT): self.source}
                self.assertEqual(
                    reconcile_pending(self.store, self.reader, now=NOW)["bound"], 1
                )
        state, source = self.store.settle.call_args.args[1:3]
        self.assertEqual((state, source), ("bound", self.source))

    def test_privacy_negatives(self):
        other = dict(REF, attachment_id=C)
        for changes, expected in (
            ({"live": False}, "expired"),
            ({"references": {B: other}}, "rejected"),
            ({"references": {B: dict(REF, direction="input")}}, "rejected"),
            ({"references": {B: dict(REF, modality="image")}}, "rejected"),
            ({"source": None}, "pending"),
            ({"expires_at": None}, "pending"),
            ({"expires_at": NOW}, "expired"),
            ({"scope": Scope(B, A, C)}, "rejected"),
            ({"parent": Parent(B, PARENT.span_id)}, "rejected"),
        ):
            with self.subTest(changes=changes):
                self.reader.latest.return_value = {
                    (SCOPE, PARENT): replace(self.source, **changes)
                }
                self.assertEqual(
                    reconcile_pending(self.store, self.reader, now=NOW)[expected], 1
                )

    def test_tail_dropped_jobs_expire_and_publication_conflicts_fail_closed(self):
        self.reader.latest.return_value = {}
        self.assertEqual(
            reconcile_pending(self.store, self.reader, now=NOW + timedelta(hours=24))[
                "expired"
            ],
            1,
        )
        self.reader.latest.return_value = {(SCOPE, PARENT): self.source}
        self.store.settle.return_value = False  # deletion after read, before CAS
        counts = reconcile_pending(self.store, self.reader, now=NOW)
        self.assertEqual(counts["bound"], 0)
        self.assertEqual(counts["conflict"], 1)

    def test_worker_query_bounds(self):
        with self.assertRaises(ValueError):
            reconcile_pending(self.store, self.reader, now=NOW, limit=257)
        self.store.claim_pending.return_value = [self.link] * 2
        with self.assertRaises(ValueError):
            reconcile_pending(self.store, self.reader, now=NOW, limit=1)
        self.reader.latest.assert_not_called()


class AttachmentReadTests(TestCase):
    def test_one_batch_authorized_states_no_urls(self):
        target = ReadTarget(SCOPE, PARENT)
        reader = Mock()
        reader.list_authorized.return_value = [
            AuthorizedStatus(
                target, REF, "ready", True, False, NOW + timedelta(hours=1)
            )
        ]
        result = attachment_statuses(
            viewer="synthetic-session", targets=[target, target], reader=reader, now=NOW
        )
        self.assertEqual(result[target][0]["status"], "partial")
        self.assertEqual(
            set(result[target][0]), {"reference", "status", "capture_complete"}
        )
        reader.list_authorized.assert_called_once_with(
            "synthetic-session", (target,), per_parent_limit=64
        )

    def test_denied_foreign_deleted_and_expired_have_no_metadata(self):
        target = ReadTarget(SCOPE, PARENT)
        reader = Mock()
        for row in (
            AuthorizedStatus(
                ReadTarget(Scope(B, A, C), PARENT),
                REF,
                "ready",
                True,
                True,
                NOW + timedelta(hours=1),
            ),
            AuthorizedStatus(
                target, REF, "deleted", True, True, NOW + timedelta(hours=1)
            ),
            AuthorizedStatus(target, REF, "ready", True, True, NOW),
        ):
            reader.list_authorized.return_value = [row]
            self.assertEqual(
                attachment_statuses(
                    viewer="synthetic-session", targets=[target], reader=reader, now=NOW
                ),
                {},
            )
        reader.list_authorized.return_value = []  # foreign and nonexistent are identical
        self.assertEqual(
            attachment_statuses(
                viewer="synthetic-session", targets=[target], reader=reader, now=NOW
            ),
            {},
        )

    def test_default_off_missing_viewer_and_unbound_ready(self):
        target = ReadTarget(SCOPE, PARENT)
        reader = Mock()
        self.assertEqual(
            attachment_statuses(viewer=None, targets=[target], reader=reader, now=NOW),
            {},
        )
        reader.list_authorized.assert_not_called()
        self.assertEqual(
            attachment_statuses(viewer="s", targets=[target], reader=None, now=NOW), {}
        )
        reader.list_authorized.return_value = [
            AuthorizedStatus(
                target, REF, "ready", False, True, NOW + timedelta(hours=1)
            )
        ]
        self.assertEqual(
            attachment_statuses(viewer="s", targets=[target], reader=reader, now=NOW)[
                target
            ][0]["status"],
            "pending",
        )
