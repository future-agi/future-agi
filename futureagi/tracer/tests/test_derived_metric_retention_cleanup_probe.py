"""Observer contracts; only the native scale tests can supply memory evidence."""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from clickhouse_driver import Client

from tracer.models.derived_metric import MetricStateConflict
from tracer.services.derived_metrics.retention import (
    ProposalCaptures,
    _span_owner_cleanup_required,
)
from tracer.services.derived_metrics.storage import FIELD_VALUES
from tracer.tests import _retention_cleanup_probe as observer


def _check_owners(execute):
    writer = SimpleNamespace(
        store=SimpleNamespace(execute=execute),
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=uuid4()),
    )
    captures = ProposalCaptures(writer, None, None)
    captures.overlay = Mock(return_value=("private-overlay-source", {}))
    return _span_owner_cleanup_required(
        writer,
        None,
        "private-field-source",
        {},
        None,
        span_fields=captures.span_owner_fields,
    )


def test_span_projection_has_one_winner_and_two_exact_physical_fences():
    project, checkpoint, delta = uuid4(), uuid4(), uuid4()
    writer = SimpleNamespace(org_id=uuid4(), lease=SimpleNamespace(project_id=project))
    head = SimpleNamespace(
        project_id=project,
        checkpoint_attempt_id=checkpoint,
        allowed_attempts=lambda: [(1, checkpoint), (2, delta)],
    )
    captures = ProposalCaptures(writer, None, head)
    captures.overlay = Mock(return_value=("SELECT * FROM private_frozen_draft", {}))
    sql, bindings = captures.span_owner_fields()
    columns = (
        *FIELD_VALUES.logical_key,
        "generation",
        "_version",
        "session_id",
        "user_id",
    )
    assert sql.count("SELECT " + ", ".join(columns) + " FROM ") == 4
    assert sql.count("ORDER BY generation DESC, _version DESC LIMIT 1 BY ") == 1
    assert sql.endswith(", ".join(FIELD_VALUES.logical_key))
    assert sql.count("WHERE (generation, attempt_id) IN ") == 2
    assert "private_frozen_draft) WHERE level = 'span'" in sql
    assert bindings["span_owner_fields_base"] == ((1, checkpoint),)
    assert bindings["span_owner_fields_deltas"] == ((2, delta),)
    assert bindings["span_owner_fields_kin_0"] == ("span",)
    assert not any(word in sql for word in ("notEmpty", "is_deleted", "unit_start"))
    captures.overlay.assert_called_once_with(FIELD_VALUES)
    head.project_id = uuid4()
    with pytest.raises(MetricStateConflict, match="manifest project mismatch"):
        captures.span_owner_fields()
    captures.overlay.assert_called_once()


def test_lazy_span_input_and_binding_conflicts_fail_closed():
    execute = Mock(return_value=[(1,)])
    writer = SimpleNamespace(
        store=SimpleNamespace(execute=execute),
        org_id=uuid4(),
        lease=SimpleNamespace(project_id=uuid4()),
    )
    select = Mock(return_value=("other source", {"retire_cutoff": 2}))
    args = (writer, None, "full proposal", {"retire_cutoff": 1}, None)
    assert _span_owner_cleanup_required(*args, span_fields=select)
    select.assert_not_called()
    execute.return_value = [(0,)]
    with pytest.raises(ValueError, match="conflicting metric query bindings"):
        _span_owner_cleanup_required(*args, span_fields=select)
    select.side_effect = RuntimeError("frozen input unavailable")
    with pytest.raises(RuntimeError, match="frozen input unavailable"):
        _span_owner_cleanup_required(*args, span_fields=select)
    assert execute.call_count == 3  # Only the dependency checks ran.


def test_observer_does_not_attribute_the_old_field_query():
    probe = _probe()
    writer = SimpleNamespace(org_id=uuid4(), lease=SimpleNamespace(project_id=uuid4()))

    def execute(sql, params, **kwargs):
        probe.tag(sql, params, kwargs)
        return [(0,)]

    writer.store = SimpleNamespace(execute=execute)
    assert not _span_owner_cleanup_required(writer, None, "old full fields", {}, None)
    assert probe.unclassified == 1
    assert probe.statements["fields"] == {"status": "not_executed"}


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
