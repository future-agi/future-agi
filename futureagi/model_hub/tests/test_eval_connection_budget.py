"""The background budget is thread-local, restored, and finite on failure."""

import copy
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from django.db import OperationalError, connections

from model_hub.tests.test_eval_result_connections import DelayedPostgres
from model_hub.utils.evaluation_connections import evaluation_connection_budget


def test_background_budget_does_not_change_interactive_thread_or_shared_settings():
    baseline = copy.deepcopy(connections.databases)
    entered = threading.Barrier(2)
    observed = threading.Barrier(2)

    def inspect(background):
        connection = connections["default"]
        original = connection.settings_dict
        local = {**original, "OPTIONS": {**original["OPTIONS"], "connect_timeout": 1}}
        connection.settings_dict = local

        def check():
            entered.wait(timeout=10)
            result = connection.settings_dict["OPTIONS"]["connect_timeout"]
            observed.wait(timeout=10)
            return result

        try:
            result = evaluation_connection_budget(check)() if background else check()
            assert connection.settings_dict is local
            assert connection.settings_dict["OPTIONS"]["connect_timeout"] == 1
            return result
        finally:
            connection.settings_dict = original

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(inspect, [True, False])) == [5, 1]
    assert connections.databases == baseline


def test_background_budget_restores_every_alias_when_callback_raises():
    originals = [
        (connection, connection.settings_dict) for connection in connections.all()
    ]

    @evaluation_connection_budget
    def fail():
        for connection, original in originals:
            if connection.vendor == "postgresql":
                assert connection.settings_dict is not original
                assert connection.settings_dict["OPTIONS"] is not original["OPTIONS"]
                assert (
                    connection.settings_dict["CONN_MAX_AGE"] == original["CONN_MAX_AGE"]
                )
        raise RuntimeError("synthetic callback failure")

    with pytest.raises(RuntimeError, match="synthetic callback failure"):
        fail()
    for connection, original in originals:
        assert connection.settings_dict is original


@pytest.mark.django_db(transaction=True)
def test_connection_stall_beyond_background_budget_fails_without_retry():
    settings = connections["default"].settings_dict
    target = (settings["HOST"], int(settings["PORT"]))
    with DelayedPostgres(target, delay=7) as server:
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()

        def attempt():
            connection = connections["default"]
            original = connection.settings_dict
            local = copy.deepcopy(original)
            local.update(HOST="127.0.0.1", PORT=server.server_address[1])
            local["OPTIONS"].update(
                connect_timeout=1,
                sslmode="disable",
                gssencmode="disable",
            )
            connection.settings_dict = local

            @evaluation_connection_budget
            def open_once():
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT 1")
                finally:
                    connection.close()

            started = time.monotonic()
            try:
                with pytest.raises(OperationalError, match="connection timeout"):
                    open_once()
                assert connection.settings_dict is local
                assert connection.connection is None
                assert 4 <= time.monotonic() - started < 9
            finally:
                connection.close()
                connection.settings_dict = original

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                executor.submit(attempt).result(timeout=15)
        finally:
            server.shutdown()
            serving.join(timeout=5)
        assert server.accepted == 1
