"""Observer contracts; only the native scale tests can supply memory evidence."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from clickhouse_driver import Client

from tracer.services.derived_metrics.retention import _span_owner_cleanup_required
from tracer.tests import _retention_cleanup_probe as observer


def _check_owners(execute):
    writer = SimpleNamespace(
        store=SimpleNamespace(execute=execute),
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=uuid4()),
    )
    return _span_owner_cleanup_required(writer, None, "private-field-source", {}, None)


def _probe():
    client = Mock(spec=Client, connection=SimpleNamespace(database="test_probe"))
    return observer.CleanupProbe(SimpleNamespace(client=client))


def _report(capsys):
    (line,) = capsys.readouterr().out.strip().splitlines()
    prefix, payload = line.split(" ", 1)
    assert prefix == "M241_PROBE" and "private" not in payload
    return json.loads(payload)


@pytest.mark.parametrize(("counts", "result"), [([1], True), ([0, 0], False)])
def test_probe_follows_the_real_short_circuit(counts, result):
    probe = _probe()
    ids = []

    def execute(sql, params, **kwargs):
        probe.tag(sql, params, kwargs)
        ids.append(kwargs["query_id"])
        return [(counts[len(ids) - 1],)]

    assert _check_owners(execute) is result
    assert ids == list(probe.ids.values())[: len(counts)]
    if len(counts) == 1:
        assert probe.statements["fields"] == {"status": "not_executed"}


@pytest.mark.parametrize(
    ("delayed", "terminal"),
    [
        (False, "ExceptionWhileProcessing"),
        (False, None),  # Initial lookup denied.
        (True, "ExceptionWhileProcessing"),
        (True, "ExceptionBeforeStart"),
        (True, None),  # Recheck denied after the first ID resolved.
    ],
)
def test_failed_statement_keeps_exception_and_reports_only_owned_rows(
    delayed, terminal, monkeypatch, capsys
):
    import conftest

    probe = _probe()
    first, second = probe.ids.values()
    error = RuntimeError("private-product-error")
    first_row = (first, "QueryFinish", 9, 8, 7, 6, {})
    rows = [(second, terminal, 5, 4, 3, 2, {"SelectedMarks": 0, "private-event": 9})]
    if not delayed:
        rows.insert(0, first_row)
    diag = Mock()
    diag.execute.side_effect = [
        [("25.3.1.1", 12345, 1, 1)],
        [("a" * 40,)],
        [],
        *([[first_row], []] if delayed else []),
        rows if terminal else RuntimeError("private-lookup-error"),
    ]
    diagnostic_client = Mock(return_value=nullcontext(diag))
    monkeypatch.setattr(conftest, "_ch_test_native_client", diagnostic_client)
    monkeypatch.setattr(observer, "monotonic", lambda: 0)
    monkeypatch.setattr(observer, "sleep", Mock())
    dispatches = []

    def execute(sql, params, **kwargs):
        probe.tag(sql, params, kwargs)
        dispatches.append(kwargs["query_id"])
        if kwargs["query_id"] == first:
            return [(0,)]
        raise error

    with (
        pytest.raises(RuntimeError) as caught,
        probe.after_refresh("mixed", SimpleNamespace(disabled=nullcontext)),
    ):
        _check_owners(execute)
    assert caught.value is error
    report = _report(capsys)
    assert report["refresh"] == "raised" and report["build_git_hash"] == "a" * 40
    assert report["version"] == "25.3.1.1" and report["flush"] == "ok"
    assert dispatches == [
        first,
        second,
    ]  # No product retry, even on a diagnostic error.
    diagnostic_client.assert_called_once_with(
        database="test_probe",
        connect_timeout=1,
        send_receive_timeout=1,
        sync_request_timeout=1,
        disable_reconnect=True,
    )
    polls = 2 if delayed else 1
    calls = diag.execute.call_args_list
    assert report["poll_count"] == polls and len(calls) == 2 + 2 * polls
    assert all(not call.kwargs for call in calls)
    assert all(call.args == ("SYSTEM FLUSH LOGS", None) for call in calls[2::2])
    assert calls[3].args[1]["ids"] == (first, second)
    assert set(calls[3].args[1]["terminal"]) == {
        "QueryFinish",
        "ExceptionWhileProcessing",
        "ExceptionBeforeStart",
    }
    if delayed:
        assert calls[5].args[1]["ids"] == (second,)
    if delayed or terminal:
        assert report["statements"]["dependencies"]["status"] == "QueryFinish"
    record = report["statements"]["fields"]
    if terminal is None:
        assert record == {"query_id": second, "status": "query_log_unavailable"}
    else:
        assert record == {
            "query_id": second,
            "status": terminal,
            "memory_usage": 5,
            "read_rows": 4,
            "read_bytes": 3,
            "query_duration_ms": 2,
            "ProfileEvents": {"SelectedMarks": 0},
        }


def test_missing_and_duplicate_terminal_rows_are_not_measurements(monkeypatch):
    probe = _probe()

    def execute(sql, params, **kwargs):
        probe.tag(sql, params, kwargs)
        return [(0,)]

    assert _check_owners(execute) is False
    first, second = probe.ids.values()
    duplicate = (first, "QueryFinish", 0, 0, 0, 0, {})
    diag = Mock()
    diag.execute.side_effect = [[], [], [], [duplicate, duplicate]] + [[]] * 8
    monkeypatch.setattr(observer, "monotonic", lambda: 0)
    pause = Mock()
    monkeypatch.setattr(observer, "sleep", pause)
    report = {}
    probe._collect(diag, report, deadline=5)
    assert report["poll_count"] == 5 and diag.execute.call_count == 12
    assert pause.call_count == 4
    assert [call.args[1]["ids"] for call in diag.execute.call_args_list[3::2]] == [
        (first, second),
        (second,),
        (second,),
        (second,),
        (second,),
    ]
    assert probe.statements == {
        "dependencies": {"query_id": first, "status": "ambiguous"},
        "fields": {"query_id": second, "status": "no_terminal_row"},
    }


@pytest.mark.parametrize(
    ("setup_seconds", "calls", "polls", "status"),
    [
        (4.5, 0, 0, "query_log_unavailable"),  # Setup left less than one I/O slice.
        (2.0, 3, 1, "query_log_unavailable"),  # Flush left too little time for lookup.
        (1.5, 4, 1, "no_terminal_row"),  # Successful empty lookup, no time to recheck.
    ],
)
def test_deadline_charges_setup_and_each_diagnostic_call(
    setup_seconds, calls, polls, status, monkeypatch, capsys
):
    probe = _probe()
    now = 0.0
    monkeypatch.setattr(observer, "monotonic", lambda: now)
    pause = Mock()
    monkeypatch.setattr(observer, "sleep", pause)

    def diagnostic_execute(sql, params):
        nonlocal now
        now += 0.75
        return []

    diag = Mock()
    diag.execute.side_effect = diagnostic_execute

    def connect():
        nonlocal now
        now += setup_seconds
        return nullcontext(diag)

    monkeypatch.setattr(probe, "_diagnostic_client", connect)

    def execute(sql, params, **kwargs):
        probe.tag(sql, params, kwargs)
        return [(1,)]

    with probe.after_refresh("mixed", SimpleNamespace(disabled=nullcontext)):
        assert _check_owners(execute) is True
    report = _report(capsys)
    assert report["refresh"] == "returned" and report["poll_count"] == polls
    assert diag.execute.call_count == calls
    pause.assert_not_called()
    assert report["statements"]["dependencies"] == {
        "query_id": probe.ids["dependencies"],
        "status": status,
    }


def test_no_helper_no_log_lookup_or_invented_usage(monkeypatch, capsys):
    monkeypatch.setattr(observer, "monotonic", lambda: 0)
    probe = _probe()
    diag = Mock()
    diag.execute.side_effect = [[("25.3.1.1", 12345, 1, 1)], [("private-build",)]]
    monkeypatch.setattr(probe, "_diagnostic_client", lambda: nullcontext(diag))
    with probe.after_refresh("conversation", SimpleNamespace(disabled=nullcontext)):
        kwargs = {}
        probe.tag("unrelated", {}, kwargs)
        assert not kwargs
    report = _report(capsys)
    assert report["refresh"] == "returned" and report["build_git_hash"] is None
    assert report["flush"] == "not_attempted" and report["poll_count"] == 0
    assert diag.execute.call_count == 2
    assert all(r == {"status": "not_executed"} for r in report["statements"].values())
