"""Page-zero skill hints use bounded dispatch or stay unavailable."""

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from clickhouse_driver.errors import ErrorCodes, ServerException

from tracer.services.clickhouse import client as client_module
from tracer.services.clickhouse import read_budget
from tracer.services.clickhouse.application_read_policy import (
    UNLIMITED_STATEMENT_SETTINGS,
)
from tracer.services.clickhouse.query_service import AnalyticsQueryService, QueryResult
from tracer.tests.test_application_read_transport import native_client
from tracer.tests.test_bounded_trace_filter_reads import (
    PROJECT_ID,
    _attribute_filter,
    _call_observe_trace_list_with_bounded_page,
    _complete_empty_page,
    _short_time_filter,
)
from tracer.tests.test_trace_root_physical_replay import mock_content_rows
from tracer.views import trace


def _page_zero(analytics, *, bounded_page=None):
    response, *_ = _call_observe_trace_list_with_bounded_page(
        bounded_page=bounded_page or _complete_empty_page(),
        validated_data={
            "filters": [
                _short_time_filter(),
                _attribute_filter("final_status", "Rejected"),
            ],
            "page_number": 0,
            "page_size": 1,
            "include_skill_links": True,
        },
        analytics=analytics,
    )
    return response


def _record_native_probe(monkeypatch, send):
    """Run the service and native client unmodified, faking only the driver."""
    owner, driver, connection, _ = native_client(monkeypatch)
    driver.connections = ()
    connection.context = SimpleNamespace()

    def send_query(sql, params):
        # The driver serializes context.settings here, not at execute() entry.
        settings = connection.context.settings
        assert all(
            settings[key] == 0
            for key in UNLIMITED_STATEMENT_SETTINGS
            if key != "max_execution_time"
        )
        data = send(sql, params, settings["max_execution_time"])
        columns = list(data[0]) if data else []
        return [tuple(row[c] for c in columns) for row in data], columns

    def execute(sql, params, *, with_column_types, settings):
        connection.context.settings = settings
        return connection.send_query(sql, params)

    connection.send_query = send_query
    driver.execute.side_effect = execute
    return AnalyticsQueryService(ch_client=owner)


def _nonempty_page(monkeypatch, analytics):
    execute = analytics.execute_ch_query

    def enrich(sql, params, **kwargs):
        if "skill_page_size" in params or "skill_replay_keys" in params:
            # The old endpoint used the ordinary analytics service for the probe.
            return execute(sql, params, **kwargs)
        data = (
            mock_content_rows([{"trace_id": "kept", "input": "hello"}], params)
            if "content_trace_ids" in params
            else []
        )
        return QueryResult(data, len(data), "clickhouse", 0.0)

    monkeypatch.setattr(analytics, "execute_ch_query", enrich)
    return replace(
        _complete_empty_page(),
        rows=[{"trace_id": "kept"}],
        rows_returned=1,
        total_rows_lower_bound=1,
        query_count=1,
    )


def _clock(monkeypatch, *, total_ms, statement_ms):
    now = [0.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(client_module, "time", clock)
    monkeypatch.setattr(read_budget, "time", clock)
    monkeypatch.setattr(trace, "TRACE_LIST_WALL_DEADLINE_MS", total_ms)
    monkeypatch.setattr(trace, "TRACE_LIST_ENRICHMENT_TIMEOUT_MS", statement_ms)
    return now


def test_every_skill_probe_statement_uses_remaining_wall(monkeypatch):
    total_ms, statement_ms = 4_000, 2_250
    now = _clock(monkeypatch, total_ms=total_ms, statement_ms=statement_ms)
    monkeypatch.setitem(trace.TRACE_LIST_READ_SETTINGS, "max_result_rows", 1)
    calls = []

    def send(sql, params, server_cap_seconds):
        discovery = "skill_page_size" in params
        calls.append((discovery, server_cap_seconds * 1000, total_ms - now[0] * 1000))
        assert len(calls) <= 4
        now[0] += 0.875
        if discovery:
            candidates = [
                {
                    "project_id": PROJECT_ID,
                    "trace_id": "trace",
                    "id": f"load-{i}",
                    "observation_type": "span",
                    "service_name": "test",
                    "skill_hour": 0,
                }
                for i in range(1 if len(calls) > 1 else 0, 2)
            ]
            return candidates[
                : params.get("skill_page_limit", params["skill_page_size"])
            ]
        assert "skill_replay_keys" in params
        # The first load has no surviving winner; the second page does.
        return [{"1": 1}] if len(calls) == 4 else []

    response = _page_zero(_record_native_probe(monkeypatch, send))
    assert response[0] == "ok"
    assert response[1]["metadata"]["skill_links_exist"] is True
    assert [discovery for discovery, _, _ in calls] == [True, False, True, False]
    for _, cap, remaining in calls:
        assert 0 < cap <= min(remaining, statement_ms)
    assert calls[-1][1] < calls[0][1]


@pytest.mark.parametrize("admission_seconds", [0.125, 0.75], ids=["shrink", "expire"])
def test_native_probe_does_not_renew_wall_after_admission(
    monkeypatch, admission_seconds
):
    now = _clock(monkeypatch, total_ms=500, statement_ms=2_000)
    sent = []

    def send(sql, params, server_cap_seconds):
        sent.append(server_cap_seconds)
        return []

    analytics = _record_native_probe(monkeypatch, send)
    execute = analytics.ch_client.execute_read_with_progress

    def delayed_entry(*args, **kwargs):
        # Time spent after the view calculated its cap but before native
        # admission begins must also count: a relative timeout would renew it.
        now[0] += 0.125
        return execute(*args, **kwargs)

    monkeypatch.setattr(
        analytics.ch_client, "execute_read_with_progress", delayed_entry
    )

    def acquire(*args, **kwargs):
        now[0] += admission_seconds
        return True

    admission = Mock(acquire=Mock(side_effect=acquire))
    # Delay either admission path, including the ordinary one on the old head.
    monkeypatch.setattr(analytics.ch_client, "_read_admission", admission)
    monkeypatch.setattr(analytics.ch_client, "_retained_lookup_admission", admission)
    submit = ThreadPoolExecutor.submit

    def submitted(pool, fn, *args, **kwargs):
        # The endpoint can stop waiting on expiry; join the worker before
        # asserting what it sent and that it returned its permit.
        future = submit(pool, fn, *args, **kwargs)
        future.result(timeout=10)
        return future

    monkeypatch.setattr(ThreadPoolExecutor, "submit", submitted)
    response = _page_zero(
        analytics, bounded_page=_nonempty_page(monkeypatch, analytics)
    )
    assert response[0] == "ok"
    assert [row["trace_id"] for row in response[1]["table"]] == ["kept"]
    if admission_seconds > 0.5:
        assert sent == []
        assert "skill_links_exist" not in response[1]["metadata"]
    else:
        assert sent == pytest.approx([0.5 - 0.125 - admission_seconds])
        assert response[1]["metadata"]["skill_links_exist"] is False
    admission.acquire.assert_called_once_with(blocking=False)
    admission.release.assert_called_once_with()


@pytest.mark.parametrize(
    "error",
    [
        ServerException("Discovery refused", code=ErrorCodes.TOO_MANY_ROWS),
        ServerException(
            "Discovery refused", code=ErrorCodes.TOO_MANY_SIMULTANEOUS_QUERIES
        ),
        EOFError(),
        ConnectionResetError(),
    ],
    ids=["row-limit", "saturated", "native-eof", "connection-reset"],
)
def test_discovery_refusal_preserves_page_without_a_skill_verdict(monkeypatch, error):
    sent = []

    def refuse(sql, params, server_cap_seconds):
        sent.append(params)
        raise error

    analytics = _record_native_probe(monkeypatch, refuse)
    page = _nonempty_page(monkeypatch, analytics)
    response = _page_zero(analytics, bounded_page=page)
    assert response[0] == "ok"
    assert len(sent) == 1 and "skill_page_size" in sent[0]
    assert [row["trace_id"] for row in response[1]["table"]] == ["kept"]
    metadata = response[1]["metadata"]
    assert metadata["query_complete"] is True
    assert "skill_links_exist" not in metadata


def test_pending_optional_probe_cannot_expire_a_completed_page(monkeypatch):
    now = _clock(monkeypatch, total_ms=500, statement_ms=500)
    analytics = _record_native_probe(monkeypatch, Mock(side_effect=AssertionError))
    page = _nonempty_page(monkeypatch, analytics)
    pending = Future()
    submitted_probes = []
    submit = ThreadPoolExecutor.submit

    def submit_with_stalled_probe(pool, fn, *args, **kwargs):
        if fn.__name__ == "_probe_skill_links":
            # Simulate a connection stuck in the worker, not a completed
            # future that happened to raise TimeoutError. No real-clock wait.
            now[0] = 0.5
            submitted_probes.append(pending)
            return pending
        return submit(pool, fn, *args, **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, "submit", submit_with_stalled_probe)
    response = _page_zero(analytics, bounded_page=page)
    assert response[0] == "ok"
    assert [row["trace_id"] for row in response[1]["table"]] == ["kept"]
    assert "skill_links_exist" not in response[1]["metadata"]
    assert submitted_probes == [pending]


def test_optional_probe_does_not_hide_programming_errors(monkeypatch):
    analytics = _record_native_probe(
        monkeypatch, Mock(side_effect=ValueError("bad probe"))
    )
    with pytest.raises(ValueError, match="bad probe"):
        _page_zero(analytics, bounded_page=_nonempty_page(monkeypatch, analytics))


@pytest.mark.parametrize(
    "verdict", [True, False, None], ids=["winner", "empty", "locked"]
)
def test_page_zero_skill_links_metadata(monkeypatch, verdict):
    if verdict is None:
        owner, driver, _, _ = native_client(monkeypatch, locked=True)
        driver.execute.return_value = ([], [])
        analytics = AnalyticsQueryService(ch_client=owner)
    else:

        def send(sql, params, server_cap_seconds):
            if "skill_page_size" in params:
                return (
                    [{"project_id": PROJECT_ID, "trace_id": "trace", "id": "load"}]
                    if verdict
                    else []
                )
            assert "skill_replay_keys" in params
            return [{"1": 1}]

        analytics = _record_native_probe(monkeypatch, send)
    with patch.object(
        ThreadPoolExecutor,
        "submit",
        autospec=True,
        side_effect=ThreadPoolExecutor.submit,
    ) as submitted:
        response = _page_zero(analytics)
    assert response[0] == "ok"
    metadata = response[1]["metadata"]
    if verdict is None:
        submitted.assert_not_called()
        driver.execute.assert_not_called()
        assert "skill_links_exist" not in metadata
    else:
        submitted.assert_called_once()
        assert metadata["skill_links_exist"] is verdict


def test_query_settings_without_guarded_native_dispatch_does_not_enable_probe():
    execute = Mock(return_value=QueryResult([], 0, "clickhouse", 0.0))
    analytics = SimpleNamespace(
        supports_per_query_read_settings=True, execute_ch_query=execute
    )
    with patch.object(
        ThreadPoolExecutor,
        "submit",
        autospec=True,
        side_effect=ThreadPoolExecutor.submit,
    ) as submitted:
        response = _page_zero(analytics)
    submitted.assert_not_called()
    execute.assert_not_called()
    assert response[0] == "ok"
    assert "skill_links_exist" not in response[1]["metadata"]
