"""Unit tests for the eval-verdict PG -> CH mirror (TH-5642).

Pins the EvalLogger -> tracer_eval_logger_v2 row mapping, the always-on mirror
(no dual-write gate), and the soft_delete_eval_loggers helper. No Django/live
CH — the row builder is pure and the client/queryset are mocked.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tracer.services.clickhouse.v2 import eval_logger_writer as elw


def _eval(**overrides):
    base = {
        "id": uuid.uuid4(),
        "trace_id": uuid.uuid4(),
        "observation_span_id": "ec0a204a6e1e58cc",
        "trace_session_id": None,
        "target_type": "span",
        "custom_eval_config_id": uuid.uuid4(),
        "eval_type_id": "conversation_resolution",
        "output_bool": None,
        "output_float": 0.75,
        "output_str": "partial",
        "output_str_list": ["a", "b"],
        "error": False,
        "error_message": None,
        "eval_explanation": "store hours not fully answered",
        "output_metadata": {"k": "v"},
        "results_tags": ["t1"],
        "results_explanation": {"r": 1},
        "eval_tags": ["e1"],
        "eval_id": "evlog1",
        "eval_task_id": None,
        "created_at": datetime(2026, 6, 19, 12, 0, 0, tzinfo=UTC),
        "updated_at": datetime(2026, 6, 19, 12, 0, 1, tzinfo=UTC),
        "deleted_at": None,
        "deleted": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.mark.unit
def test_eval_row_maps_columns_in_order():
    e = _eval()
    row = elw._eval_to_row(e)
    cols = list(elw._EVAL_COLUMNS)
    d = dict(zip(cols, row, strict=False))
    assert d["id"] == str(e.id)
    assert d["trace_id"] == str(e.trace_id)
    assert d["observation_span_id"] == "ec0a204a6e1e58cc"
    assert d["target_type"] == "span"
    assert d["custom_eval_config_id"] == str(e.custom_eval_config_id)
    assert d["output_float"] == 0.75
    assert d["output_str"] == "partial"
    assert d["eval_explanation"] == "store hours not fully answered"
    # JSON columns are serialized strings (CH String, not nullable).
    import json

    assert json.loads(d["output_str_list"]) == ["a", "b"]
    assert json.loads(d["output_metadata"]) == {"k": "v"}
    assert d["error"] == 0
    assert d["is_deleted"] == 0
    # _version derives from updated_at (ReplacingMergeTree).
    assert d["_version"] == int(e.updated_at.timestamp() * 1_000_000)
    assert len(row) == len(cols)


def _row_dict(e):
    return dict(zip(elw._EVAL_COLUMNS, elw._eval_to_row(e), strict=False))


@pytest.mark.unit
def test_eval_row_bool_and_deleted():
    assert _row_dict(_eval(output_bool=True))["output_bool"] == 1
    assert _row_dict(_eval(output_bool=False))["output_bool"] == 0
    assert _row_dict(_eval(deleted=True))["is_deleted"] == 1


@pytest.mark.unit
def test_mirror_empty_ids_noop(monkeypatch):
    monkeypatch.setattr(
        elw, "_get_client", lambda: pytest.fail("should not insert for empty ids")
    )
    elw.mirror_eval_loggers_to_clickhouse([None, ""])


@pytest.mark.unit
def test_mirror_fires_insert_for_valid_id(monkeypatch):
    """With the dual-write gate removed, a valid id ALWAYS reaches the CH client
    (the inverse of the old gated-off behaviour)."""
    e = _eval()
    captured = {}

    class _FakeMgr:
        def filter(self, **kw):
            captured["filter"] = kw
            return [e]

    import tracer.models.observation_span as obs

    _mgr = _FakeMgr()
    # both attrs: the mirror does getattr(EvalLogger, "all_objects", EvalLogger.objects)
    # and the default arg eagerly accesses .objects.
    monkeypatch.setattr(
        obs, "EvalLogger", SimpleNamespace(all_objects=_mgr, objects=_mgr)
    )

    class _FakeClient:
        def insert(self, table, rows, column_names=None):
            captured["table"] = table
            captured["rows"] = rows

    monkeypatch.setattr(elw, "_get_client", lambda: _FakeClient())
    elw.mirror_eval_loggers_to_clickhouse([e.id])

    assert captured["table"] == elw._EVAL_LOGGER_V2_TABLE
    assert captured["filter"]["id__in"] == [str(e.id)]
    assert len(captured["rows"]) == 1


@pytest.mark.unit
def test_soft_delete_eval_loggers_bumps_version_and_mirrors(monkeypatch):
    """soft_delete_eval_loggers captures ids, soft-deletes WITH an updated_at
    bump (so the RMT _version advances and the deleted row wins under FINAL),
    and mirrors the captured ids post-commit."""
    from unittest.mock import MagicMock

    import django.db.transaction as dj_tx

    ids = [uuid.uuid4(), uuid.uuid4()]
    qs = MagicMock()
    qs.values_list.return_value = ids

    on_commit_cbs = []
    monkeypatch.setattr(dj_tx, "on_commit", on_commit_cbs.append)
    mirrored = {}
    monkeypatch.setattr(
        elw,
        "mirror_eval_loggers_to_clickhouse",
        lambda x: mirrored.__setitem__("ids", x),
    )

    n = elw.soft_delete_eval_loggers(qs)

    assert n == 2
    qs.update.assert_called_once()
    kw = qs.update.call_args.kwargs
    assert kw["deleted"] is True
    assert kw["deleted_at"] is not None
    assert kw["updated_at"] == kw["deleted_at"]  # version-bump must match deleted_at
    for cb in on_commit_cbs:
        cb()
    assert mirrored["ids"] == ids


@pytest.mark.unit
def test_soft_delete_eval_loggers_empty_is_noop(monkeypatch):
    from unittest.mock import MagicMock

    qs = MagicMock()
    qs.values_list.return_value = []
    monkeypatch.setattr(
        elw,
        "mirror_eval_loggers_to_clickhouse",
        lambda x: pytest.fail("no mirror for empty set"),
    )
    assert elw.soft_delete_eval_loggers(qs) == 0
    qs.update.assert_not_called()
