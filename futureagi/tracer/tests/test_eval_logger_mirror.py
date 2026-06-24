"""Unit tests for the eval-verdict PG -> CH mirror (TH-5642).

Pins the EvalLogger -> tracer_eval_logger_v2 row mapping and the dual-write
gate. No Django/live CH — the row builder is pure and the client is mocked.
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
