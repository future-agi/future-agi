"""A real transport loss, with broken=True and no SQLSTATE.

A loopback-only TCP relay forwards the disposable VM's synthetic PostgreSQL.
Dropping its established sockets at the SQL boundary leaves the listener and
PostgreSQL healthy: a fresh Django connection can succeed immediately.
"""

import select
import socket
import threading
from unittest.mock import patch

import pytest
from django.db import OperationalError, connection

from model_hub.models.choices import CellStatus
from model_hub.models.develop_dataset import Row
from model_hub.utils.eval_metadata import (
    read_evaluation_column_types,
    read_evaluation_workspace,
)

pytestmark = pytest.mark.django_db(transaction=True, databases="__all__")


class TcpRelay:
    def __init__(self, target):
        assert target[0] in ("localhost", "127.0.0.1"), target
        self.target = target
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen()
        self.listener.settimeout(0.2)
        self.port = self.listener.getsockname()[1]
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.pairs = []
        self.workers = []
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            upstream = socket.create_connection(self.target, timeout=3)
            upstream.settimeout(None)
            pair = (client, upstream)
            with self.lock:
                self.pairs.append(pair)
            worker = threading.Thread(target=self.relay, args=(pair,), daemon=True)
            self.workers.append(worker)
            worker.start()

    def relay(self, pair):
        try:
            while not self.stopped.is_set():
                ready, _, _ = select.select(pair, [], [], 0.2)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = pair[1] if source is pair[0] else pair[0]
                    target.sendall(data)
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                if pair in self.pairs:
                    self.pairs.remove(pair)
            for sock in pair:
                sock.close()

    def drop_established(self):
        with self.lock:
            assert self.pairs, (
                "metadata query must already have an established TCP connection"
            )
            for pair in self.pairs:
                for sock in pair:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

    def stop(self):
        self.stopped.set()
        self.listener.close()
        with self.lock:
            for pair in self.pairs:
                for sock in pair:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
        self.thread.join(timeout=3)
        for worker in self.workers:
            worker.join(timeout=3)


@pytest.fixture
def tcp_relay(evaluation_case, monkeypatch):
    db = connection
    db.close()
    proxy = TcpRelay((db.settings_dict["HOST"], int(db.settings_dict["PORT"])))
    monkeypatch.setitem(db.settings_dict, "HOST", "127.0.0.1")
    monkeypatch.setitem(db.settings_dict, "PORT", proxy.port)
    try:
        yield proxy
    finally:
        db.close()
        proxy.stop()


class DisconnectAtMetadata:
    def __init__(self, proxy, table, disconnect):
        self.proxy = proxy
        self.table = table
        self.disconnect = disconnect
        self.calls = 0
        self.events = []

    def __call__(self, execute, sql, params, many, context):
        matches = sql.startswith("SELECT") and f'FROM "{self.table}"' in sql
        if self.table == "model_hub_column":
            matches = matches and sql.startswith(
                'SELECT "model_hub_column"."id", "model_hub_column"."data_type"'
            )
        if not matches:
            return execute(sql, params, many, context)
        self.calls += 1
        if self.calls == 1 and self.disconnect:
            self.proxy.drop_established()
        try:
            return execute(sql, params, many, context)
        except OperationalError as error:
            self.events.append(
                {
                    "driver": type(error.__cause__).__name__,
                    "sqlstate": getattr(error.__cause__, "sqlstate", None),
                    "broken": context["connection"].connection.broken,
                    "message": str(error),
                }
            )
            raise


@pytest.mark.parametrize("table", ["model_hub_column", "accounts_workspace"])
@pytest.mark.parametrize("mode", ["helper", "runner"])
@pytest.mark.parametrize(
    "disconnect", [False, True], ids=["healthy-control", "tcp-disconnect"]
)
def test_broken_transport_reconnects_before_evaluation(
    evaluation_case,
    tcp_relay,
    table,
    mode,
    disconnect,
):
    case = evaluation_case
    row = Row.objects.get(pk=case.row_id)
    fault = DisconnectAtMetadata(tcp_relay, table, disconnect)
    result = None
    caught = None
    with (
        connection.execute_wrapper(fault),
        patch.object(
            connection,
            "close",
            wraps=connection.close,
        ) as close,
    ):
        try:
            if mode == "runner":
                result = case.runner._process_eval_result(row, case.mappings)
            elif table == "model_hub_column":
                result = read_evaluation_column_types(case.mappings.values())
            else:
                result = read_evaluation_workspace(row)
        except Exception as error:
            caught = f"{type(error).__name__}: {error}"
    # After recording the application outcome, demonstrate that only connection
    # disposal is needed: the relay listener and database still serve reads.
    connection.close()
    recovered = read_evaluation_column_types(case.mappings.values())
    assert set(recovered) == set(case.mappings.values())
    diagnostic = (
        f"manual_reconnect=ok table={table} mode={mode} disconnect={disconnect} reads={fault.calls} "
        f"closes={close.call_count} events={fault.events} caught={caught} "
        f"result={result} logs={case.logger.call_count} "
        f"evals={case.runner._run_evaluation.call_count}"
    )
    print(diagnostic)
    if disconnect:
        assert fault.events, diagnostic
        assert fault.events[0]["driver"] == "OperationalError", diagnostic
        assert fault.events[0]["sqlstate"] is None, diagnostic
        assert fault.events[0]["broken"] is True, diagnostic
        assert close.call_count == 1, diagnostic
        assert fault.calls == 2, diagnostic
    assert caught is None, diagnostic
    if mode == "runner":
        response, status, value = result
        assert status == CellStatus.PASS.value, diagnostic
        assert response["reason"] == "matched"
        case.logger.assert_called_once()
        case.runner._run_evaluation.assert_called_once()
        case.runner._emit_cell_usage.assert_called_once()
        case.runner._create_cell(
            case.dataset, case.runner.column, row, response, value, status
        )
        case.output_cell.refresh_from_db()
        assert case.output_cell.status == CellStatus.PASS.value
    elif table == "model_hub_column":
        assert set(result) == set(case.mappings.values())
    else:
        assert result.id == case.workspace.id
