"""Real Users SQL generation with a fake transport; no database reads or writes."""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tracer.services.clickhouse.v2.query_builders.user_list import (
    UserListQueryBuilderV2,
)
from tracer.services.users_list_manager import UsersListManager

PROJECT = "11111111-1111-4111-8111-111111111111"
ORG = "22222222-2222-4222-8222-222222222222"
USER = "33333333-3333-4333-8333-333333333333"
OTHER = "44444444-4444-4444-8444-444444444444"


@pytest.fixture(autouse=True)
def replay_module(monkeypatch):
    # Standard backend pytest does not add the standalone QA scripts to sys.path.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "scripts/qa"))
    global replay
    replay = import_module("replay_observe_queries_readonly")


def fixture(*, days=7, kind="text", workspace=False, search=""):
    end = datetime(2026, 8, 1, 12, tzinfo=UTC)
    start = end - timedelta(days=days)
    filters = [{"column_id": "created_at", "filter_config": {
        "filter_type": "datetime", "filter_op": "between",
        "filter_value": [start.isoformat(), end.isoformat()],
    }}]
    if kind is not None:
        filters.append({"column_id": "fixture_attribute", "filter_config": {
            "col_type": "SPAN_ATTRIBUTE", "filter_type": "text" if kind == "typed_text" else kind,
            "filter_op": "greater_than" if kind == "number" else "in" if kind == "typed_text" else "equals",
            "filter_value": 10 if kind == "number" else ["synthetic string"] if kind == "typed_text" else "synthetic string",
            **({"attribute_value_types": ["string"]} if kind == "typed_text" else {}),
        }})
    params = {"page_size": 25, "cursor_mode": True, "filters": json.dumps(filters)}
    if not workspace:
        params["project_id"] = PROJECT
    case = {"surface": "users_workspace" if workspace else "users_project",
            "request": {"method": "GET", "path": "/tracer/users/",
                        "target_rows": 25, "params": params},
            "window": {"start": start.isoformat(), "end": end.isoformat()}}
    manager = UsersListManager(organization_id=ORG, allowed_project_ids=[PROJECT],
                               project_id=None if workspace else PROJECT,
                               filters=replay.normalize_filters(case["request"]),
                               search=search)
    scope = {"organization_id": ORG, "project_id": PROJECT}
    reader = object.__new__(replay.ReadOnlyExecutor)
    reader.mode, reader.projects, reader.calls = "candidate", [PROJECT], []
    reader._users_context = reader._users_certificate = None
    reader._users_origin_expected = False
    reader.prefix, reader.deadline = "offline-fixture", time.monotonic() + 60
    reader.args = SimpleNamespace(threads=1, read_gib=1)
    reader.client = SimpleNamespace(
        execute=Mock(return_value=([(USER, PROJECT)],
                                  [("end_user_id", "String"), ("project_id", "String")])),
        last_query=SimpleNamespace(progress=SimpleNamespace(rows=1, bytes=32)),
    )
    builder = UserListQueryBuilderV2(organization_id=ORG, project_ids=[PROJECT],
                                    filters=manager.filters, search=search)
    sql, bindings = builder.build_dimension_candidate_query(
        limit=65 if manager.attribute_exact_text_filters else 26,
        window_start=start, window_end=end,
    )
    return reader, manager, case, scope, builder, sql, bindings


def configure(items):
    reader, manager, case, scope, *_ = items
    reader._configure_users_remap(manager, case, scope, "synthetic-plan")
    return reader


@pytest.mark.parametrize("days", [7, 30, 365])
@pytest.mark.parametrize("kind", [None, "text", "typed_text", "number"])
@pytest.mark.parametrize("workspace", [False, True])
def test_reviewed_origin_shapes_are_exactly_bound(days, kind, workspace):
    items = fixture(days=days, kind=kind, workspace=workspace)
    reader = configure(items)
    sql, bindings = items[-2:]
    digest = hashlib.sha256(sql.strip().rstrip(";").encode()).hexdigest()
    assert replay._users_sources_current()
    assert digest in replay._USERS_ORIGIN_SHAS
    assert reader._users_context.origin_sql_sha256 == digest
    assert reader._users_context.origin_row_limit == (65 if kind in {"text", "typed_text"} else 26)
    assert reader._users_context.origin_bindings == replay.replay.digest(replay.safe_json(bindings))
    reader.client.execute.assert_not_called()
    reader.execute_ch_query(sql, bindings)
    assert reader._users_certificate.ids == (USER,)
    limits = reader.client.execute.call_args.kwargs["settings"]
    assert limits["readonly"] == 2
    assert 0 < limits["max_memory_usage"] <= 4 * 1024**3
    assert limits["max_bytes_to_read"] == 1024**3
    assert limits["max_result_rows"] == 100001
    assert limits["read_overflow_mode"] == limits["result_overflow_mode"] == "throw"


@pytest.mark.parametrize("change", ["search", "source", "project", "page_size", "cursor", "method"])
def test_unreviewed_context_fails_before_transport(change, monkeypatch):
    items = fixture(search="synthetic" if change == "search" else "")
    reader, manager, case, *_ = items
    if change == "source":
        monkeypatch.setattr(replay, "_users_sources_current", lambda: False)
    elif change == "project":
        manager.scoped_project_ids = [OTHER]
    elif change in {"page_size", "cursor"}:
        case["request"]["params"][change] = 100 if change == "page_size" else "prior-page"
    elif change == "method":
        case["request"]["method"] = "POST"
    with pytest.raises(replay.replay.ReplayError, match="USERS_REMAP_(CONTEXT|ORIGIN)_NOT_QUALIFIED"):
        configure(items)
    reader.client.execute.assert_not_called()


@pytest.mark.parametrize("change", ["sql", "bindings", "source", "scope"])
def test_origin_cannot_change_after_configuration(change, monkeypatch):
    items = fixture()
    reader = configure(items)
    sql, bindings = items[-2:]
    if change == "sql":
        sql, bindings = fixture(kind="number")[-2:]
    elif change == "bindings":
        bindings = {**bindings, "limit": 27}
    elif change == "source":
        monkeypatch.setattr(replay, "_users_sources_current", lambda: False)
    else:
        reader.projects = [OTHER]
    with pytest.raises(replay.replay.ReplayError, match="USERS_REMAP_"):
        reader.execute_ch_query(sql, bindings)
    reader.client.execute.assert_not_called()


@pytest.mark.parametrize("kind", [None, "text", "typed_text", "number"])
def test_actual_manager_initial_query_matches_approved_bindings(kind):
    items = fixture(kind=kind)
    reader = configure(items)

    class TransportReached(BaseException):
        pass

    # Stop at the fake transport, after the real manager and guard agree.
    reader.client.execute.side_effect = TransportReached
    with patch("tracer.services.users_list_manager.V2AnalyticsQueryService", return_value=reader):
        with pytest.raises(TransportReached):
            items[1].list_cursor_payload(page_size=25)
    reader.client.execute.assert_called_once()
    assert reader.client.execute.call_args.args[1]["limit"] == (65 if kind in {"text", "typed_text"} else 26)


@pytest.mark.parametrize("kind", [None, "text", "typed_text", "number"])
def test_origin_result_may_not_exceed_its_exact_initial_bound(kind):
    items = fixture(kind=kind)
    reader = configure(items)
    count = reader._users_context.origin_row_limit + 1
    reader.client.execute.return_value = (
        [(USER, PROJECT)] * count, [("end_user_id", "String"), ("project_id", "String")],
    )
    with pytest.raises(replay.replay.ReplayError, match="USERS_REMAP_ORIGIN_RESULT_INVALID"):
        reader.execute_ch_query(*items[-2:])
    assert reader._users_certificate is None


@pytest.mark.parametrize("change", [None, "ids", "client", "origin", "scope"])
def test_remap_is_finite_context_bound_and_single_use(change):
    items = fixture()
    reader = configure(items)
    reader.execute_ch_query(*items[-2:])
    remap_sql, params = items[4].build_dimension_survivor_query([USER])
    reader.client.execute.reset_mock()
    reader.client.execute.return_value = ([(USER, USER)], [("any_id", "String"), ("survivor_id", "String")])
    if change == "ids":
        params = {"dimension_candidate_ids": (OTHER,)}
    elif change == "client":
        reader.client = SimpleNamespace(execute=Mock())
    elif change == "origin":
        reader.calls[-1]["query_id"] = "another-origin"
    elif change == "scope":
        reader.projects = [OTHER]
    if change:
        with pytest.raises(replay.replay.ReplayError):
            reader.execute_ch_query(remap_sql, params)
        reader.client.execute.assert_not_called()
        return
    reader.execute_ch_query(remap_sql, params)
    receipt = reader.calls[-1]["scope_certificate"]
    assert receipt["origin_sql_sha256"] == reader._users_context.origin_sql_sha256
    assert receipt["result_validated"] and receipt["candidate_count"] == 1
    reader.client.execute.reset_mock()
    with pytest.raises(replay.replay.ReplayError):
        reader.execute_ch_query(remap_sql, params)
    reader.client.execute.assert_not_called()
