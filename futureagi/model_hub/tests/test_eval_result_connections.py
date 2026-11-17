"""Real concurrent result writes with a bounded PostgreSQL handshake delay."""

import copy
import select
import socket
import socketserver
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from traceback import extract_stack
from types import SimpleNamespace

import pytest
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper

from accounts.models import Organization, Workspace
from model_hub.models.develop_dataset import Cell, Column, Dataset, Row
from model_hub.models.evals_metric import EvalTemplate, UserEvalMetric
from model_hub.models.experiments import ExperimentDatasetTable, ExperimentsTable
from model_hub.views import eval_runner


class DelayedPostgres(socketserver.ThreadingTCPServer):
    """Forward to the test DB after one handshake delay; never retry a connection."""

    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, target, delay):
        self.target = target
        self.delay = delay
        self.accepted = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", 0), PostgresForwarder)


class PostgresForwarder(socketserver.BaseRequestHandler):
    def handle(self):
        with self.server.lock:
            self.server.accepted += 1
        time.sleep(self.server.delay)
        try:
            with socket.create_connection(self.server.target, timeout=10) as upstream:
                upstream.settimeout(None)
                sockets = [self.request, upstream]
                while True:
                    readable, _, _ = select.select(sockets, [], [], 20)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = (
                            upstream if source is self.request else self.request
                        )
                        destination.sendall(data)
        except (OSError, ConnectionError):
            return


def seed_results(user, ordinal, count):
    organization = Organization.objects.create(
        name=f"Result connection tenant {ordinal}"
    )
    workspace = Workspace.objects.create(
        name="Result connections",
        organization=organization,
        created_by=user,
    )
    dataset = Dataset.objects.create(
        name="Result connections",
        organization=organization,
        workspace=workspace,
        user=user,
    )
    experiment = ExperimentsTable.objects.create(
        name="Result connections",
        dataset=dataset,
        snapshot_dataset=dataset,
        user=user,
    )
    run = ExperimentDatasetTable.objects.create(name="Sibling", experiment=experiment)
    source = Column.objects.create(
        name="Output",
        dataset=dataset,
        data_type="text",
        source="experiment",
    )
    template = EvalTemplate.objects.create(
        name=f"connection_judge_{ordinal}",
        organization=organization,
        workspace=workspace,
        config={"eval_type_id": "CustomPromptEvaluator", "output": "Pass/Fail"},
    )
    metric = UserEvalMetric.objects.create(
        name="connection_judge",
        template=template,
        dataset=dataset,
        organization=organization,
        workspace=workspace,
        model="gpt-4o",
        config={
            "mapping": {"output": str(source.id)},
            "config": {},
            "reason_column": True,
        },
    )
    column = Column.objects.create(
        name="Judge",
        dataset=dataset,
        data_type="boolean",
        source="experiment_evaluation",
        source_id=f"{run.id}-{source.id}-sourceid-{metric.id}",
    )
    run.columns.add(source, column)
    rows = [Row.objects.create(dataset=dataset, order=index) for index in range(count)]
    Cell.objects.bulk_create(
        [
            Cell(
                dataset=dataset,
                row=row,
                column=source,
                value="synthetic output",
                status="pass",
            )
            for row in rows
        ]
    )
    runner = eval_runner.EvaluationRunner(
        user_eval_metric_id=metric.id, format_output=True
    )
    runner.user_eval_metric = metric
    runner.eval_template = template
    runner.dataset = dataset
    runner.column = source
    runner.replace_column_id = source.id
    runner.experiment_dataset = run
    runner.organization_id = organization.id
    runner.workspace_id = workspace.id
    return runner, dataset, column, rows


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("delay,rows_per_tenant", [(0, 24), (3, 4)])
def test_concurrent_result_writes_survive_bounded_handshake_delay(
    user,
    monkeypatch,
    delay,
    rows_per_tenant,
):
    """Only the external evaluator/billing integration is replaced, not ORM writes."""
    seeds = [seed_results(user, ordinal, rows_per_tenant) for ordinal in range(2)]
    monkeypatch.setattr(eval_runner, "log_and_deduct_cost_for_api_request", None)
    observed = []
    called_rows = []
    opened = []
    lock = threading.Lock()
    original_open = DatabaseWrapper.get_new_connection

    def record_open(connection, parameters):
        with lock:
            opened.append(tuple(frame.name for frame in extract_stack(limit=18)))
        return original_open(connection, parameters)

    monkeypatch.setattr(DatabaseWrapper, "get_new_connection", record_open)

    def evaluator_standin(self, row, mappings, config):
        connection = connections["default"]
        state = (connection.get_autocommit(), connection.in_atomic_block)
        with lock:
            observed.append(state)
            called_rows.append(row.id)
        result = SimpleNamespace(
            eval_results=[
                {
                    "data": "Pass",
                    "failure": False,
                    "reason": "Result-write STANDIN",
                }
            ]
        )
        return result, {}, {}, {}, SimpleNamespace(cost={}, token_usage={}), None

    monkeypatch.setattr(
        eval_runner.EvaluationRunner, "_run_evaluation", evaluator_standin
    )
    settings = connections["default"].settings_dict
    target = (settings["HOST"], int(settings["PORT"]))
    with DelayedPostgres(target, delay) as server:
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()

        def write_one(task):
            runner, dataset, column, row = task
            connection = connections["default"]
            original = connection.settings_dict
            connection.settings_dict = copy.deepcopy(original)
            connection.settings_dict.update(
                HOST="127.0.0.1", PORT=server.server_address[1]
            )
            # Match the interactive default used by the worker before the fix.
            # Plaintext loopback transport: count DB opens, not SSL/GSS fallback sockets.
            connection.settings_dict["OPTIONS"].update(
                connect_timeout=1,
                sslmode="disable",
                gssencmode="disable",
            )
            connection.settings_dict["CONN_MAX_AGE"] = 0
            try:
                runner._process_and_create_cell(
                    row,
                    runner.user_eval_metric.config["mapping"],
                    dataset,
                    column,
                )
                assert connection.settings_dict["OPTIONS"]["connect_timeout"] == 1
                assert connection.connection is None, (
                    "Row thread leaked its database connection"
                )
            finally:
                connection.close()
                connection.settings_dict = original

        tasks = [
            (runner, dataset, column, row)
            for runner, dataset, column, rows in seeds
            for row in rows
        ]
        try:
            with ThreadPoolExecutor(max_workers=10) as executor:
                list(executor.map(write_one, tasks))
        finally:
            server.shutdown()
            serving.join(timeout=5)
            print("connection_open_paths", dict(Counter(opened)))
            print("accepted_connections", server.accepted)
    assert sorted(called_rows) == sorted(task[3].id for task in tasks)
    assert observed == [(True, False)] * len(tasks), (
        "Do not hold a transaction over the external evaluator"
    )
    for runner, dataset, column, _rows in seeds:
        assert (
            list(Cell.objects.filter(column=column).values_list("status", "value"))
            == [("pass", "Passed")] * rows_per_tenant
        )
        reason = Column.objects.get(
            dataset=dataset,
            source="evaluation_reason",
            source_id=f"{column.id}-sourceid-{runner.user_eval_metric_id}",
        )
        assert (
            list(Cell.objects.filter(column=reason).values_list("status", "value"))
            == [("pass", "Result-write STANDIN")] * rows_per_tenant
        )
        assert set(
            Cell.objects.filter(column__in=[column, reason]).values_list(
                "dataset_id", flat=True
            )
        ) == {dataset.id}
