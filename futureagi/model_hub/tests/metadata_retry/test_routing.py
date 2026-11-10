"""Exercise real Django model routing and server-terminated PG connections."""

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import psycopg
import pytest
from django.db import OperationalError, connections, router, transaction

from accounts.models.workspace import Workspace
from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Dataset, Row
from model_hub.tests.test_eval_metadata_retry import ReadFault
from model_hub.utils.eval_metadata import (
    EvaluationMetadataUnavailable,
    read_evaluation_workspace,
)
from tfc.routers import ReadReplicaRouter

pytestmark = pytest.mark.django_db(transaction=True, databases="__all__")


class TerminateAtSelect:
    def __init__(self, table):
        self.table = table
        self.events = []
        self.calls = 0

    def __call__(self, execute, sql, params, many, context):
        if not (sql.startswith("SELECT") and f'FROM "{self.table}"' in sql):
            return execute(sql, params, many, context)
        self.calls += 1
        db = context["connection"]
        if self.calls == 1:
            # Real PostgreSQL sends AdminShutdown and closes only this backend.
            # The test server remains available for an immediate reconnect.
            pid = db.connection.info.backend_pid
            with psycopg.connect(**db.get_connection_params()) as admin:
                assert admin.execute(
                    "SELECT pg_terminate_backend(%s)", (pid,)
                ).fetchone()[0]
        try:
            return execute(sql, params, many, context)
        except OperationalError as error:
            self.events.append(
                {
                    "alias": db.alias,
                    "driver": type(error.__cause__).__name__,
                    "sqlstate": getattr(error.__cause__, "sqlstate", None),
                    "broken": db.connection.broken,
                }
            )
            raise


def exercise_route(
    case, settings, monkeypatch, *, opt_in, cached_dataset, mode, interrupt
):
    settings.READ_REPLICA_OPT_IN = [opt_in]
    monkeypatch.setattr(router, "routers", [ReadReplicaRouter()])
    row_alias = "replica" if opt_in == "Row" else "default"
    failed_alias = "default" if opt_in == "Row" else "replica"
    table = "accounts_workspace" if opt_in == "Row" else "model_hub_dataset"
    assert Row.objects.all().db == row_alias
    assert Workspace.objects.all().db == "default"
    assert Dataset.objects.all().db == ("replica" if opt_in == "Dataset" else "default")
    rows = (
        Row.objects.select_related("dataset") if cached_dataset else Row.objects.all()
    )
    row = rows.get(pk=case.row_id)
    assert row._state.db == row_alias
    assert Row._meta.get_field("dataset").is_cached(row) is cached_dataset
    if cached_dataset:
        assert not Dataset._meta.get_field("workspace").is_cached(row.dataset)
    wrappers = {alias: connections[alias] for alias in ("default", "replica")}
    fault = TerminateAtSelect(table)
    caught = None
    result = None
    closes = {}
    with ExitStack() as stack:
        for alias, db in wrappers.items():
            closes[alias] = stack.enter_context(
                patch.object(db, "close", wraps=db.close)
            )
        if interrupt:
            stack.enter_context(wrappers[failed_alias].execute_wrapper(fault))
        try:
            if mode == "helper":
                result = read_evaluation_workspace(row)
            else:
                # Real worker/direct core; provider and billing are spies from
                # the existing fixture. Avoid entry/exit close_old_connections
                # masking which wrapper the metadata helper itself disposes.
                result = case.runner._process_eval_result(row, case.mappings)
        except Exception as error:
            caught = f"{type(error).__name__}: {error}"
    counts = {alias: spy.call_count for alias, spy in closes.items()}
    # Cleanup is outside spies and outside the code path under test.
    for db in wrappers.values():
        db.close()
    # Verify the outage really was transient. This operator-style reconnect is
    # after the measured outcome, not a patch to the implementation under test.
    recovered = read_evaluation_workspace(row)
    assert recovered.id == case.workspace.id
    diagnostic = (
        f"manual_reconnect=ok opt_in={opt_in} row_alias={row_alias} failed_alias={failed_alias} "
        f"mode={mode} events={fault.events} closes={counts} caught={caught} "
        f"result={result} logs={case.logger.call_count} "
        f"evals={case.runner._run_evaluation.call_count}"
    )
    print(diagnostic)
    if interrupt:
        assert fault.events[0] == {
            "alias": wrappers[failed_alias].alias,
            "driver": "AdminShutdown",
            "sqlstate": "57P01",
            "broken": True,
        }, diagnostic
        assert counts == {alias: int(alias == failed_alias) for alias in wrappers}, (
            diagnostic
        )
        assert fault.calls == 2, diagnostic
    assert caught is None, diagnostic
    if mode == "helper":
        assert result.id == case.workspace.id, diagnostic
    else:
        response, status, value = result
        assert status == CellStatus.PASS.value, diagnostic
        assert response["reason"] == "matched", diagnostic
        case.logger.assert_called_once()
        assert case.logger.call_args.kwargs["workspace"].id == case.workspace.id
        case.runner._run_evaluation.assert_called_once()
        case.runner._emit_cell_usage.assert_called_once()
        case.runner._create_cell(
            case.dataset, case.runner.column, row, response, value, status
        )
        case.output_cell.refresh_from_db()
        assert case.output_cell.status == CellStatus.PASS.value


@pytest.mark.parametrize("opt_in,cached_dataset", [("Row", True), ("Dataset", False)])
@pytest.mark.parametrize("mode", ["helper", "runner"])
@pytest.mark.parametrize(
    "interrupt", [False, True], ids=["healthy-control", "shutdown"]
)
def test_relation_query_disposes_its_routed_connection(
    evaluation_case,
    replica_connection,
    settings,
    monkeypatch,
    opt_in,
    cached_dataset,
    mode,
    interrupt,
):
    exercise_route(
        evaluation_case,
        settings,
        monkeypatch,
        opt_in=opt_in,
        cached_dataset=cached_dataset,
        mode=mode,
        interrupt=interrupt,
    )


@pytest.mark.parametrize("opt_in", ["Row", "Dataset"])
@pytest.mark.parametrize("atomic_on_query", [False, True])
def test_relation_retry_checks_transaction_on_query_connection(
    evaluation_case,
    replica_connection,
    settings,
    monkeypatch,
    opt_in,
    atomic_on_query,
):
    settings.READ_REPLICA_OPT_IN = [opt_in]
    monkeypatch.setattr(router, "routers", [ReadReplicaRouter()])
    row = (
        Row.objects.select_related("dataset").get(pk=evaluation_case.row_id)
        if opt_in == "Row"
        else Row.objects.get(pk=evaluation_case.row_id)
    )
    read_alias = "default" if opt_in == "Row" else "replica"
    other_alias = "replica" if opt_in == "Row" else "default"
    table = "accounts_workspace" if opt_in == "Row" else "model_hub_dataset"
    fault = ReadFault(table)
    db = connections[read_alias]
    other = connections[other_alias]
    with (
        transaction.atomic(using=read_alias if atomic_on_query else other_alias),
        db.execute_wrapper(fault),
        patch.object(db, "close", wraps=db.close) as close,
        patch.object(other, "close", wraps=other.close) as other_close,
    ):
        if atomic_on_query:
            with pytest.raises(EvaluationMetadataUnavailable):
                read_evaluation_workspace(row)
            close.assert_not_called()
            assert fault.calls == 1
        else:
            assert read_evaluation_workspace(row).id == evaluation_case.workspace.id
            close.assert_called_once()
            assert fault.calls == 2
        other_close.assert_not_called()


def test_cached_relations_do_not_query_or_route(evaluation_case, monkeypatch):
    row = Row.objects.select_related("dataset__workspace").get(
        pk=evaluation_case.row_id
    )
    with patch.object(
        router, "db_for_read", side_effect=AssertionError("cached read routed")
    ):
        assert read_evaluation_workspace(row) is row.dataset.workspace


def test_nullable_workspace_stays_none(evaluation_case):
    Dataset.objects.filter(pk=evaluation_case.dataset.id).update(workspace=None)
    row = Row.objects.get(pk=evaluation_case.row_id)
    assert read_evaluation_workspace(row) is None


def test_workspace_query_pins_route_across_retries(
    evaluation_case,
    replica_connection,
    monkeypatch,
):
    row = (
        Row.objects.using("default")
        .select_related("dataset")
        .get(pk=evaluation_case.row_id)
    )
    route = MagicMock(side_effect=["default", "replica"])
    monkeypatch.setattr(router, "db_for_read", route)
    fault = ReadFault("accounts_workspace")
    with connections["default"].execute_wrapper(fault):
        assert read_evaluation_workspace(row).id == evaluation_case.workspace.id
    route.assert_called_once_with(Workspace, instance=row.dataset)
    assert fault.calls == 2
