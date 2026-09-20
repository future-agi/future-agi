"""Do not reuse cached results or cursor prefixes from older filter semantics."""

from datetime import UTC, datetime, timedelta

import pytest
from django.conf import settings
from django.core import signing

from tracer.services import exact_aggregation_cache as snapshots
from tracer.services.clickhouse import list_cursor

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("key", ["created_at", "start_time"])
@pytest.mark.parametrize("kind,value", [("text", "raw"), ("number", 1)])
def test_snapshot_normalization_preserves_raw_dates_and_separates_values(
    days, key, kind, value
):
    end = datetime(2026, 9, 5, tzinfo=UTC)
    native_date = {
        "column_id": "created_at",
        "filter_config": {
            "col_type": "SYSTEM_METRIC",
            "filter_type": "datetime",
            "filter_op": "between",
            "filter_value": [end - timedelta(days=days), end],
        },
    }
    raw = {
        "column_id": key,
        "filter_config": {
            "col_type": "SPAN_ATTRIBUTE",
            "filter_type": kind,
            "filter_op": "equals",
            "filter_value": value,
        },
    }
    identity = {"project_id": "p", "filters": [native_date, raw]}
    normalized = snapshots.normalize_exact_observe_identity(identity)
    assert raw in normalized["filters"]
    changed_raw = {
        **raw,
        "filter_config": {
            **raw["filter_config"],
            "filter_value": "other" if kind == "text" else 2,
        },
    }
    changed = snapshots.normalize_exact_observe_identity(
        {**identity, "filters": [native_date, changed_raw]}
    )
    without_raw = snapshots.normalize_exact_observe_identity(
        {**identity, "filters": [native_date]}
    )
    keys = {
        snapshots.snapshot_cache_key("observe-system-graph", item)
        for item in [normalized, changed, without_raw]
    }
    assert len(keys) == 3
    assert snapshots.normalize_exact_observe_identity(normalized) == normalized
    assert identity["filters"] == [native_date, raw]


def test_old_results_are_unreachable_without_resetting_scope_admission(monkeypatch):
    identity = {"project_id": "p", "metric_id": "latency"}
    current_key = snapshots.snapshot_cache_key("observe-system-graph", identity)
    admission_key = snapshots._scope_admission_key(identity)
    assert current_key.startswith("exact-aggregation:v4:")
    with monkeypatch.context() as old:
        old.setattr(snapshots, "_CACHE_VERSION", 3)
        legacy_key = snapshots.snapshot_cache_key("observe-system-graph", identity)
        assert snapshots._scope_admission_key(identity) == admission_key
    old_payload = {
        "v": 3,
        "completed_at": "2026-09-04T00:00:00Z",
        "payload": {"data": ["old-result"]},
    }
    stored = {legacy_key: old_payload}

    class FakeCache:
        def get(self, key):
            return stored.get(key)

    monkeypatch.setattr(snapshots, "cache", FakeCache())
    assert current_key != legacy_key
    assert snapshots.read_exact_snapshot("observe-system-graph", identity) is None
    assert stored == {legacy_key: old_payload}
    stored[current_key] = old_payload
    assert snapshots.read_exact_snapshot("observe-system-graph", identity) is None


@pytest.mark.parametrize("resource", ["traces", "sessions", "users", "spans"])
def test_pre_fix_cursor_prefix_is_rejected_and_new_cursor_round_trips(
    resource, monkeypatch
):
    values = {
        "resource": resource,
        "scope": {"project_ids": ["p"]},
        "query": {
            "filters": [
                {
                    "column_id": "company_id",
                    "filter_config": {
                        "col_type": "SPAN_ATTRIBUTE",
                        "filter_type": "text",
                        "filter_op": "equals",
                        "filter_value": "42",
                    },
                }
            ]
        },
        "page_size": 25,
        "window_start": datetime(2026, 8, 1, tzinfo=UTC),
        "window_end": datetime(2026, 9, 1, tzinfo=UTC),
        "order": (datetime(2026, 8, 20, tzinfo=UTC), "entity-id"),
        "seen_rows": 25,
    }
    with monkeypatch.context() as old:
        old.setattr(list_cursor, "CURSOR_VERSION", 3)
        old.setattr(list_cursor, "CURSOR_SALT", "tracer.clickhouse-list-cursor.v3")
        old_token = list_cursor.encode_list_cursor(**values)
    read = {key: values[key] for key in ["resource", "scope", "query", "page_size"]}
    with pytest.raises(list_cursor.ListCursorError) as error:
        list_cursor.decode_list_cursor(old_token, **read)
    assert error.value.code == "invalid_cursor"
    new_token = list_cursor.encode_list_cursor(**values)
    current = list_cursor.decode_list_cursor(new_token, **read)
    assert current.order == values["order"] and current.seen_rows == 25
    payload = signing.loads(
        new_token, key=settings.SECRET_KEY, salt=list_cursor.CURSOR_SALT
    )
    assert payload["v"] == 4
    assert "version_ceiling" not in payload
