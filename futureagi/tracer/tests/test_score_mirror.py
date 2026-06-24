"""Unit tests for the Score PG -> CH mirror (TH-5642 annotation filters).

Pins the Score -> model_hub_score_v2 row mapping, the always-on mirror (no
dual-write gate), and the soft_delete_scores helper. Pure: the row builder is a
plain function and the client/queryset are mocked.
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2 import score_writer as sw


def _score(**overrides):
    base = {
        "id": uuid.uuid4(),
        "source_type": "observation_span",
        "trace_id": uuid.uuid4(),
        "observation_span_id": "ch_abc123def456",  # CH-only span id (16-hex-ish)
        "trace_session_id": None,
        "project_id": uuid.uuid4(),
        "label_id": uuid.uuid4(),
        "value": {"rating": 4},
        "annotator_id": uuid.uuid4(),
        "organization_id": uuid.uuid4(),
        "deleted": False,
        "deleted_at": None,
        "created_at": datetime(2026, 6, 20, 12, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 20, 12, 0, 1, tzinfo=UTC),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
def test_score_row_maps_columns_in_order():
    s = _score()
    row = dict(zip(sw._SCORE_COLUMNS, sw._score_to_row(s), strict=False))
    assert row["id"] == str(s.id)
    assert row["source_type"] == "observation_span"
    assert row["observation_span_id"] == "ch_abc123def456"
    assert row["trace_id"] == str(s.trace_id)
    assert row["label_id"] == str(s.label_id)
    assert row["annotator_id"] == str(s.annotator_id)
    assert json.loads(row["value"]) == {"rating": 4}  # JSON String column
    assert row["deleted"] == 0
    assert row["_version"] == int(s.updated_at.timestamp() * 1_000_000)
    assert len(sw._score_to_row(s)) == len(sw._SCORE_COLUMNS)


@pytest.mark.unit
def test_score_row_soft_delete_and_empty_value():
    assert (
        dict(
            zip(sw._SCORE_COLUMNS, sw._score_to_row(_score(deleted=True)), strict=False)
        )["deleted"]
        == 1
    )
    # null value serializes to '{}' (CH String column, JSONExtract-safe)
    assert (
        dict(
            zip(sw._SCORE_COLUMNS, sw._score_to_row(_score(value=None)), strict=False)
        )["value"]
        == "{}"
    )


@pytest.mark.unit
def test_mirror_targets_v2_table():
    assert sw._SCORE_V2_TABLE == "model_hub_score_v2"


@pytest.mark.unit
def test_mirror_fires_insert_for_valid_id(monkeypatch):
    """With the dual-write gate removed, a valid id ALWAYS reaches the CH client."""
    s = _score()
    captured = {}

    class _FakeMgr:
        def filter(self, **kw):
            captured["filter"] = kw
            return [s]

    import model_hub.models.score as score_mod

    _mgr = _FakeMgr()
    # both attrs: getattr(Score, "all_objects", Score.objects) eagerly accesses .objects
    monkeypatch.setattr(
        score_mod, "Score", SimpleNamespace(all_objects=_mgr, objects=_mgr)
    )

    class _FakeClient:
        def insert(self, table, rows, column_names=None):
            captured["table"] = table
            captured["rows"] = rows

    monkeypatch.setattr(sw, "_get_client", lambda: _FakeClient())
    sw.mirror_scores_to_clickhouse([s.id])

    assert captured["table"] == sw._SCORE_V2_TABLE
    assert captured["filter"]["id__in"] == [str(s.id)]
    assert len(captured["rows"]) == 1


@pytest.mark.unit
def test_soft_delete_scores_bumps_version_and_mirrors(monkeypatch):
    """soft_delete_scores captures ids, soft-deletes WITH an updated_at bump, and
    mirrors the captured ids post-commit (queryset .update() bypasses post_save)."""
    from unittest.mock import MagicMock

    import django.db.transaction as dj_tx

    ids = [uuid.uuid4(), uuid.uuid4()]
    qs = MagicMock()
    qs.values_list.return_value = ids

    cbs = []
    monkeypatch.setattr(dj_tx, "on_commit", cbs.append)
    mirrored = {}
    monkeypatch.setattr(
        sw, "mirror_scores_to_clickhouse", lambda x: mirrored.__setitem__("ids", x)
    )

    n = sw.soft_delete_scores(qs)

    assert n == 2
    kw = qs.update.call_args.kwargs
    assert kw["deleted"] is True
    assert kw["updated_at"] == kw["deleted_at"]
    for cb in cbs:
        cb()
    assert mirrored["ids"] == ids
