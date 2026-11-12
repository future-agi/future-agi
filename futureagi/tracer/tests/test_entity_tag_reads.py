"""Finite assignment-reader isolation, consistency and failure contracts."""

from contextlib import nullcontext
from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.db import OperationalError

from tracer.services import entity_tag_reads as reads
from tracer.services.entity_tags import TagReadUnavailable
from tracer.tests.test_entity_tag_target import PROJECT, TRACE, winner

pytestmark = pytest.mark.unit


@pytest.fixture
def store(monkeypatch):
    manager = MagicMock()
    records = manager.using.return_value.filter.return_value.values.return_value
    records.__getitem__.return_value = []
    monkeypatch.setattr(reads.EntityTagAssignment, "objects", manager)
    monkeypatch.setattr(reads, "application_postgres_reads", lambda **kw: nullcontext())
    return manager, records


def test_sdk_only_values_use_the_same_semantics_without_an_overlay(store):
    row = winner(tags="[]", attrs_string={"tag.tags": '["sdk"]'})
    reads.enrich_entity_tags([row], kind="span")
    assert reads.tag_fields(row)["tags"] == [{"name": "sdk"}]
    assert row["manual_tags"] == [] and row["tag_revision"] == 0
    store[0].using.assert_called_once_with("default")


def test_root_alias_and_every_child_coordinate_are_explicit():
    root = winner()
    assert reads.tag_assignment_key(root, kind="span") == reads.tag_assignment_key(
        root, kind="trace"
    )
    child = winner(parent_span_id="parent")
    key = reads.tag_assignment_key(child, kind="span")
    for change in (
        {"project_id": "other"},
        {"trace_id": "other"},
        {"id": "other"},
        {"service_name": "other"},
        {"observation_type": "other"},
        {"start_hour": child["start_hour"] + timedelta(hours=1)},
    ):
        assert reads.tag_assignment_key({**child, **change}, kind="span") != key
    assert key[1] == "span" and key != reads.tag_assignment_key(root, kind="span")


@pytest.mark.parametrize(
    "missing",
    [
        "project_id",
        "trace_id",
        "id",
        "service_name",
        "observation_type",
        "parent_span_id",
    ],
)
def test_incomplete_child_identity_never_widens_the_pg_lookup(store, missing):
    row = winner(parent_span_id="parent")
    del row[missing]
    with pytest.raises(TagReadUnavailable, match="identity_incomplete"):
        reads.enrich_entity_tags([row], kind="span")
    store[0].using.assert_not_called()


def test_empty_rows_do_not_query_postgres(store):
    reads.enrich_entity_tags([], kind="trace")
    store[0].using.assert_not_called()


def test_assignment_failure_is_unavailable_and_does_not_partially_mutate(store):
    manager, _ = store
    manager.using.return_value.filter.side_effect = OperationalError(
        "private diagnostic"
    )
    row = winner()
    before = row.copy()
    with pytest.raises(TagReadUnavailable, match="tag_assignment_unavailable") as error:
        reads.enrich_entity_tags([row], kind="span")
    assert "private diagnostic" not in str(error.value)
    assert row == before


def test_no_overlay_keeps_legacy_but_an_explicit_clear_suppresses_it(store):
    manager, records = store
    row = winner(trace_tags='[{"name":"old-ui","color":"#123456"}]')
    reads.enrich_entity_tags([row], kind="span")
    assert row["tags"] == [{"name": "sdk"}, {"name": "old-ui", "color": "#123456"}]
    records.__getitem__.return_value = [
        {
            **dict(
                zip(
                    reads._IDENTITY_FIELDS,
                    reads.tag_assignment_key(row, kind="span"),
                    strict=True,
                )
            ),
            "manual_tags": [],
            "revision": 2,
        }
    ]
    # Use fresh raw telemetry, not an already-enriched response as source input.
    row = winner(trace_tags='["old-ui"]')
    reads.enrich_entity_tags([row], kind="span")
    assert row["tags"] == [{"name": "sdk"}]
    assert row["manual_tags"] == [] and row["tag_revision"] == 2
    assert manager.using.call_count == 2


def test_invalid_assignment_in_later_row_leaves_every_row_unmodified(store):
    _, records = store
    rows = [winner(), winner(trace_id="second")]
    records.__getitem__.return_value = [
        {
            **dict(
                zip(
                    reads._IDENTITY_FIELDS,
                    reads.tag_assignment_key(rows[1], kind="trace"),
                    strict=True,
                )
            ),
            "manual_tags": [42],
            "revision": 1,
        }
    ]
    before = [row.copy() for row in rows]
    with pytest.raises(TagReadUnavailable, match="tag_assignment_invalid"):
        reads.enrich_entity_tags(rows, kind="trace")
    assert rows == before


def test_assignment_query_is_exact_and_deduplicates_candidate_keys(store):
    rows = [winner(), winner()]
    reads.enrich_entity_tags(rows, kind="span")
    predicate = store[0].using.return_value.filter.call_args.args[0]
    fields = dict(predicate.children)
    assert fields == {
        "project_id": PROJECT,
        "trace_id": TRACE,
        "entity_kind": "trace",
        "span_id": "",
        "start_hour": reads.TRACE_ASSIGNMENT_HOUR,
        "observation_type": "",
        "service_name": "",
    }
    store[1].__getitem__.assert_called_once_with(slice(None, 2))


def test_read_target_roundtrips_into_the_existing_mutation_contract(store):
    from tracer.serializers.entity_tags import SpanTagsUpdateSerializer

    row = winner(_version="18446744073709551615")
    reads.enrich_entity_tags([row], kind="span")
    serializer = SpanTagsUpdateSerializer(
        data={
            **row["tag_target"],
            "tags": ["manual"],
            "expected_tag_revision": row["tag_revision"],
        }
    )
    assert serializer.is_valid(), serializer.errors
    assert row["tag_target"]["expected_version"] == "18446744073709551615"


def test_total_tag_payload_budget_fails_without_partial_updates(store, monkeypatch):
    monkeypatch.setattr(reads, "MAX_TAG_READ_BYTES", 1)
    rows = [winner()]
    before = [row.copy() for row in rows]
    with pytest.raises(TagReadUnavailable, match="tag_payload_budget_exceeded"):
        reads.enrich_entity_tags(rows, kind="span")
    assert rows == before


def test_one_oversized_tag_value_makes_only_that_row_read_only(store):
    import json

    oversized = winner(
        id="big",
        parent_span_id="parent",
        attrs_string={"tag.tags": json.dumps(["x" * (1024 * 1024)])},
    )
    healthy = winner(id="small", parent_span_id="parent")
    rows = [oversized, healthy]
    reads.enrich_entity_tags(rows, kind="span")
    # The response survives: the decodable column tag is still shown, but the
    # row cannot be edited from an incomplete view.
    assert oversized["tags"] == [{"name": "sdk"}]
    assert oversized["source_tag_names"] == ["sdk"]
    assert oversized["tag_target"] is None
    assert healthy["tags"] == [{"name": "sdk"}]
    assert healthy["tag_target"]["span_id"] == "small"
