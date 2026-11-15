"""The fresh legacy read must carry a complete, explicit replacement-edit contract."""

import json
from contextlib import nullcontext
from unittest.mock import MagicMock

import pytest

from tracer.services import entity_tag_reads as reads
from tracer.tests.test_entity_tag_target import winner

pytestmark = pytest.mark.unit


@pytest.fixture
def assignments(monkeypatch):
    manager = MagicMock()
    records = manager.using.return_value.filter.return_value.values.return_value
    records.__getitem__.return_value = []
    monkeypatch.setattr(reads.EntityTagAssignment, "objects", manager)
    monkeypatch.setattr(reads, "application_postgres_reads", lambda **kw: nullcontext())
    return records


LEGACY = [
    {"name": "A", "color": "#123456"},
    {"name": "B", "color": "#abcdef"},
    {"name": "sdk", "color": "#112233"},
]


@pytest.mark.parametrize("kind", ["trace", "span"])
def test_absent_assignment_exposes_complete_legacy_labels_including_sdk_overlap(
    assignments, kind
):
    row = winner(trace_tags=json.dumps(LEGACY))
    reads.enrich_entity_tags([row], kind=kind)
    assert row["manual_tags"] == LEGACY
    assert row["tag_assignment_exists"] is False
    assert row["tag_revision"] == 0
    assert row["source_tag_names"] == ["sdk"]
    assert reads.tag_fields(row)["tag_assignment_exists"] is False
    # The browser can carry A/B and the legacy-owned SDK color without promoting
    # a different, SDK-only label into the first manual assignment.
    assert row["tags"] == [LEGACY[2], LEGACY[0], LEGACY[1]]


@pytest.mark.parametrize("manual", [[], [{"name": "saved", "color": "#abcdef"}]])
def test_assignment_presence_not_emptiness_suppresses_legacy(assignments, manual):
    row = winner(trace_tags=json.dumps(LEGACY))
    assignments.__getitem__.return_value = [
        {
            **dict(
                zip(
                    reads._IDENTITY_FIELDS,
                    reads.tag_assignment_key(row, kind="span"),
                    strict=True,
                )
            ),
            "manual_tags": manual,
            "revision": 2,
        }
    ]
    reads.enrich_entity_tags([row], kind="span")
    assert row["tag_assignment_exists"] is True
    assert row["manual_tags"] == manual
    assert row["tags"] == [{"name": "sdk"}, *manual]
    assert row["tag_revision"] == 2


@pytest.mark.parametrize(
    "legacy",
    [
        [{"name": "A", "unknown_payload": "must-not-be-dropped"}],
        [{"name": "A", "color": "invalid-color"}],
        ["A", None],
        "not-json",
        [f"tag-{i}" for i in range(101)],
        ["x" * 257],
        ["x" * (1024 * 1024)],
    ],
)
def test_unrepresentable_legacy_edit_contract_is_read_only_not_partial(
    assignments, legacy
):
    row = winner(
        trace_tags=json.dumps(legacy) if not isinstance(legacy, str) else legacy
    )
    reads.enrich_entity_tags([row], kind="span")
    assert row["tag_target"] is None
    assert row["manual_tags"] is None
    assert row["tag_assignment_exists"] is False


def test_absent_assignment_without_legacy_is_explicitly_editable_empty(assignments):
    row = winner(trace_tags="[]")
    reads.enrich_entity_tags([row], kind="span")
    assert row["manual_tags"] == []
    assert row["tag_assignment_exists"] is False
    assert row["tag_target"] is not None


def test_legacy_metadata_is_editor_only_not_exported(assignments):
    row = winner(trace_tags=json.dumps(LEGACY))
    reads.enrich_entity_tags([row], kind="span")
    exported = reads.tag_export_row(row)
    assert exported["tags"] == ["sdk", "A", "B"]
    assert "manual_tags" not in exported
    assert "tag_assignment_exists" not in exported


def test_assignment_lookup_remains_scoped_to_exact_project(assignments):
    # A record for another tenant/project must not be accepted as this row's
    # authoritative assignment, even with every remaining coordinate identical.
    from tracer.services.entity_tags import TagReadUnavailable

    row = winner(trace_tags=json.dumps(LEGACY))
    record = dict(
        zip(
            reads._IDENTITY_FIELDS,
            reads.tag_assignment_key(row, kind="span"),
            strict=True,
        )
    )
    record.update(
        project_id="00000000-0000-4000-8000-000000000099", manual_tags=[], revision=1
    )
    assignments.__getitem__.return_value = [record]
    before = row.copy()
    with pytest.raises(TagReadUnavailable, match="tag_assignment_identity_mismatch"):
        reads.enrich_entity_tags([row], kind="span")
    assert row == before
