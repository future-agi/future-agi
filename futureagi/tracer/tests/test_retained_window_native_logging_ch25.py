"""Retained-window logging at the real native driver and server boundary."""

import logging
from concurrent.futures import ThreadPoolExecutor

import pytest
from clickhouse_driver.errors import ServerException
from structlog.testing import capture_logs

from conftest import _ch_test_native_client, _ch_test_owned_database
from tracer.services.clickhouse.attribute_reads import _GLOBAL_RETAINED_START_SQL
from tracer.services.clickhouse.client import ClickHouseClient


@pytest.mark.integration
@pytest.mark.parametrize("server_error", [False, True], ids=["success", "failure"])
def test_only_retained_driver_call_omits_query_logs(monkeypatch, caplog, server_error):
    # A string is not a valid argument to fromUnixTimestamp64Micro. The error
    # case must reach the server after the driver has sent the bound query.
    bound = "invalid-retained-timestamp" if server_error else 1763512345678901
    owner = ClickHouseClient(pool_size=2, server_enforced_readonly=False)
    with (
        _ch_test_owned_database("test_retained_logs_") as db,
        _ch_test_native_client(database=db) as metadata,
        _ch_test_native_client(database=db) as ordinary,
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        owner._return_client(metadata)
        owner._return_client(ordinary)
        receive = metadata.receive_result

        def receive_after_control(*args, **kwargs):
            # send_query has run, but the retained read hasn't received its
            # result yet. Collect the ordinary query from a different thread.
            control = worker.submit(owner.execute_read, "SELECT 23 AS concurrent_read")
            assert control.result(timeout=5)[0] == [(23,)]
            return receive(*args, **kwargs)

        def lookup():
            return owner.execute_read_with_progress(
                _GLOBAL_RETAINED_START_SQL,
                {"window_end_us": bound},
                retained_lookup=True,
            )

        monkeypatch.setattr(metadata, "receive_result", receive_after_control)
        caplog.set_level(logging.DEBUG, logger="clickhouse_driver")
        caplog.clear()
        with capture_logs() as wrapper_logs:
            if server_error:
                with pytest.raises(ServerException, match="fromUnixTimestamp64Micro"):
                    lookup()
            else:
                assert lookup()[0] == [(None,)]  # No parts in this database.
            # Same calling context after the retained read unwinds.
            assert owner.execute_read("SELECT 31 AS subsequent_read")[0] == [(31,)]

        native_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name.startswith("clickhouse_driver.")
        ]
        for marker in ("concurrent_read", "subsequent_read"):
            assert any(marker in message for message in native_messages)
        for marker in (
            "system.parts", "retained_start", "fromUnixTimestamp64Micro", str(bound)
        ):
            assert marker not in caplog.text
            assert marker not in repr(wrapper_logs)
